from __future__ import annotations

from typing import Any


class OfflineXApiClient:
    """Stand-in client that never contacts the X API."""

    calls_attempted = 0
    calls_made = 0

    def search_posts(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}

    def get_liking_users(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}

    def get_reposted_by(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}

    def get_quoted_posts(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"data": [], "meta": {"result_count": 0}}