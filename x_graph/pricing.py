"""Estimated pay-per-use cost helpers for X API responses.

X bills per *resource returned* on pay-per-use (not a flat fee per HTTP call).
Pilot rates (check console.x.com — they change):

  Post (read):  ~$0.005 each
  User (read):  ~$0.01 each

These estimates are intentionally conservative upper bounds for planning.
They are NOT a bill. Always verify spend on developer.x.com / console.x.com.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Official pilot defaults (override via CLI / env if X changes pricing).
DEFAULT_POST_READ_USD = 0.005
DEFAULT_USER_READ_USD = 0.01


@dataclass(frozen=True)
class PricingConfig:
    post_read_usd: float = DEFAULT_POST_READ_USD
    user_read_usd: float = DEFAULT_USER_READ_USD

    def estimate_search_page_ceiling(self, max_results: int = 100) -> float:
        """Rough upper bound for one search page before the response arrives.

        Assumes a full page of posts plus a full page of included users and
        referenced posts (worst-case expansions). Used for dry-run planning and
        as a fallback when no observed page cost is available yet.
        """
        n = max(1, max_results)
        # data posts + includes.tweets + includes.users
        return n * self.post_read_usd + n * self.post_read_usd + n * self.user_read_usd

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


def count_billable_resources(payload: dict[str, Any]) -> tuple[int, int]:
    """Return (post_count, user_count) from a typical X API v2 JSON body."""
    posts = 0
    users = 0

    data = payload.get("data")
    items: list[Any]
    if data is None:
        items = []
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        if _looks_like_post(item):
            posts += 1
        elif _looks_like_user(item):
            users += 1
        elif "id" in item:
            # Ambiguous id-only objects: treat as posts (search/quote payloads).
            posts += 1

    includes = payload.get("includes") or {}
    if isinstance(includes, dict):
        for tweet in includes.get("tweets") or []:
            if isinstance(tweet, dict):
                posts += 1
        for user in includes.get("users") or []:
            if isinstance(user, dict):
                users += 1

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
) -> dict[str, float | int]:
    posts, users = count_billable_resources(payload)
    post_cost = posts * pricing.post_read_usd
    user_cost = users * pricing.user_read_usd
    return {
        "posts": posts,
        "users": users,
        "post_cost_usd": round(post_cost, 6),
        "user_cost_usd": round(user_cost, 6),
        "total_usd": round(post_cost + user_cost, 6),
    }


def format_usd(value: float) -> str:
    return f"${value:.4f}"
