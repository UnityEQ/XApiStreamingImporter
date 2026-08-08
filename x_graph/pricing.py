"""Estimated pay-per-use cost helpers for X API responses.

X bills per *resource returned* on pay-per-use (not a flat fee per HTTP call).
Pilot rates (check console.x.com — they change):

  Post (read):  ~$0.005 each
  User (read):  ~$0.01 each

Empirical match to console.x.com for keyword **search** (Matt Rife / FNAF, 2026-08):

  * Only posts in response ``data`` are billed as Post reads.
  * ``includes.tweets`` / ``includes.users`` from search expansions did **not**
    show up as User usage or extra Post usage on the console.
  * Dedicated user-list endpoints (likers, reposters, user lookup) bill users
    in ``data``.

These estimates are for planning / ``--max-spend`` only. They are NOT a bill.
Always verify spend on developer.x.com / console.x.com.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


# Official pilot defaults (override via CLI / env if X changes pricing).
DEFAULT_POST_READ_USD = 0.005
DEFAULT_USER_READ_USD = 0.01

# How to attribute a response to billable resources.
# posts_primary — search / post lookup / quote tweets: count posts in data only
# users_primary — liking_users / reposted_by / user lookup: count users in data only
BillingMode = Literal["posts_primary", "users_primary"]


@dataclass(frozen=True)
class PricingConfig:
    post_read_usd: float = DEFAULT_POST_READ_USD
    user_read_usd: float = DEFAULT_USER_READ_USD

    def estimate_search_page_ceiling(self, max_results: int = 100) -> float:
        """Upper bound for one search page before the response arrives.

        Keyword search is billed on posts in ``data`` only (not includes).
        Ceiling is a full page of primary results.
        """
        n = max(1, max_results)
        return n * self.post_read_usd

    def estimate_user_list_ceiling(self, max_results: int = 100) -> float:
        return max(1, max_results) * self.user_read_usd

    def max_search_results_for_budget(
        self,
        remaining_usd: float,
        *,
        max_results: int = 100,
        min_results: int = 10,
        observed_usd_per_result: float | None = None,
        safety: float = 0.85,
    ) -> int:
        """Largest search ``max_results`` that should fit in ``remaining_usd``.

        Prefers an empirical $/requested-slot from the last search page when
        available (scaled by ``safety`` so the final page rarely overshoots the
        cap). Falls back to :meth:`estimate_search_page_ceiling` on the first
        page of a run.

        Returns 0 when even ``min_results`` is unlikely to fit — caller should
        stop rather than issue another search.
        """
        if remaining_usd <= 0:
            return 0
        max_results = max(min_results, min(100, max_results))
        min_results = max(10, min(min_results, max_results))  # X search min is 10

        if observed_usd_per_result is not None and observed_usd_per_result > 0:
            # How many slots can we buy at the last page's effective rate?
            affordable = int((remaining_usd * safety) / observed_usd_per_result)
            if affordable >= min_results:
                return min(max_results, affordable)
            # Tiny remainder: try minimum page only if raw (no safety) still fits.
            if min_results * observed_usd_per_result <= remaining_usd:
                return min_results
            return 0

        # First page / no observation: walk down from preferred size via ceiling.
        for n in range(max_results, min_results - 1, -1):
            if self.estimate_search_page_ceiling(n) <= remaining_usd:
                return n
        return 0


def _data_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def count_billable_resources(
    payload: dict[str, Any],
    *,
    mode: BillingMode = "posts_primary",
) -> tuple[int, int]:
    """Return (post_count, user_count) for estimated billing.

    Counts only primary ``data`` resources. Search expansions in ``includes``
    are ignored — console usage for keyword search matched ``data`` posts only
    and showed zero User reads for author/mention expansions.
    """
    items = _data_items(payload)
    posts = 0
    users = 0

    if mode == "users_primary":
        for item in items:
            if _looks_like_user(item) or "id" in item:
                users += 1
        return posts, users

    # posts_primary (default)
    for item in items:
        if _looks_like_post(item):
            posts += 1
        elif _looks_like_user(item):
            # Unexpected on a posts endpoint; do not bill as a post.
            continue
        elif "id" in item:
            # Ambiguous id-only objects: treat as posts (search/quote payloads).
            posts += 1

    return posts, users


def _looks_like_post(item: dict[str, Any]) -> bool:
    return any(
        key in item
        for key in (
            "text",
            "author_id",
            "conversation_id",
            "referenced_tweets",
            "public_metrics",
            "edit_history_tweet_ids",
        )
    )


def _looks_like_user(item: dict[str, Any]) -> bool:
    return any(
        key in item
        for key in ("username", "profile_image_url", "public_metrics", "name")
    ) and "author_id" not in item and "text" not in item


def estimate_payload_usd(
    payload: dict[str, Any],
    pricing: PricingConfig,
    *,
    mode: BillingMode = "posts_primary",
) -> dict[str, float | int]:
    posts, users = count_billable_resources(payload, mode=mode)
    post_cost = posts * pricing.post_read_usd
    user_cost = users * pricing.user_read_usd
    return {
        "posts": posts,
        "users": users,
        "post_cost_usd": round(post_cost, 6),
        "user_cost_usd": round(user_cost, 6),
        "total_usd": round(post_cost + user_cost, 6),
        "billing_mode": mode,
    }


def format_usd(value: float) -> str:
    return f"${value:.4f}"


# MCP / xurl tool → how we attribute spend for that response.
TOOL_BILLING_MODE: dict[str, BillingMode] = {
    "search_posts_all": "posts_primary",
    "search_posts_recent": "posts_primary",
    "get_posts_quoted_posts": "posts_primary",
    "get_posts_by_id": "posts_primary",
    "get_posts_by_ids": "posts_primary",
    "get_posts_liking_users": "users_primary",
    "get_posts_reposted_by": "users_primary",
    "get_users_by_usernames": "users_primary",
    "get_users_by_username": "users_primary",
    "get_users_by_id": "users_primary",
    "search_users": "users_primary",
}


def billing_mode_for_tool(tool_name: str) -> BillingMode:
    return TOOL_BILLING_MODE.get(tool_name, "posts_primary")
