from __future__ import annotations

from typing import Any


class OfflineXApiClient:
    """Stand-in client that never contacts the X API."""

    calls_attempted = 0
    calls_made = 0
    estimated_spend_usd = 0.0
    estimated_posts = 0
    estimated_users = 0
    last_response_spend_usd = 0.0
    last_search_max_results = 0
    last_search_spend_usd = 0.0
    next_call_ceiling_usd = 0.0

    def adaptive_search_max_results(self, preferred: int = 100) -> int:
        return max(10, min(100, preferred))

    def search_posts(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}

    def get_liking_users(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}

    def get_reposted_by(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}

    def get_quoted_posts(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}