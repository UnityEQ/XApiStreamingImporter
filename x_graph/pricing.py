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
        referenced posts (worst-case expansions). Used only to refuse the next
        call when remaining dollar budget cannot cover another page.
        """
        n = max(1, max_results)
        # data posts + includes.tweets + includes.users
        return n * self.post_read_usd + n * self.post_read_usd + n * self.user_read_usd

    def estimate_user_list_ceiling(self, max_results: int = 100) -> float:
        return max(1, max_results) * self.user_read_usd


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
