from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from x_graph.config import POST_FIELDS, SEARCH_EXPANSIONS, USER_FIELDS

logger = logging.getLogger(__name__)

# Maps MCP tool names (xapi server) to REST paths used by xurl.
MCP_TOOL_PATHS: dict[str, str] = {
    "search_posts_all": "/2/tweets/search/all",
    "search_posts_recent": "/2/tweets/search/recent",
    "get_posts_liking_users": "/2/tweets/{id}/liking_users",
    "get_posts_reposted_by": "/2/tweets/{id}/retweeted_by",
    "get_posts_quoted_posts": "/2/tweets/{id}/quote_tweets",
    "get_posts_by_id": "/2/tweets/{id}",
    "get_users_by_usernames": "/2/users/by",
    "get_users_by_id": "/2/users/{id}",
}


class ApiBudgetExceeded(Exception):
    """Raised when the per-run API call budget is exhausted."""


class PostNotFoundError(Exception):
    """Raised when a post was deleted or is otherwise unavailable."""

    def __init__(self, post_id: str, detail: str = "") -> None:
        self.post_id = post_id
        self.detail = detail
        super().__init__(detail or f"Post not found: {post_id}")


def _errors_indicate_not_found(errors: Any) -> str | None:
    if not isinstance(errors, list):
        return None
    for err in errors:
        if not isinstance(err, dict):
            continue
        err_type = str(err.get("type", ""))
        title = str(err.get("title", ""))
        if "resource-not-found" in err_type or title == "Not Found Error":
            return str(err.get("resource_id") or err.get("value") or "")
    return None


class XApiClient:
    """Thin client over X API v2, mirroring xapi MCP tool semantics.

    Default transport uses `npx @xdevplatform/xurl` (same OAuth bridge as MCP).
    Inject `mcp_call` to route calls through an MCP host instead.
    """

    def __init__(
        self,
        *,
        mcp_call: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        xurl_command: list[str] | None = None,
        api_call_budget: int = 200,
        sleep_seconds: float = 0.5,
        max_retries: int = 3,
        auth: str | None = None,
    ) -> None:
        self._mcp_call = mcp_call
        self._xurl_command = xurl_command or self._default_xurl_command()
        self._api_call_budget = api_call_budget
        self._sleep_seconds = sleep_seconds
        self._max_retries = max_retries
        self._auth = auth
        self.calls_made = 0

    @staticmethod
    def _default_xurl_command() -> list[str]:
        node = shutil.which("node")
        if node:
            node_dir = Path(node).parent
            npx_cli = node_dir / "node_modules" / "npm" / "bin" / "npx-cli.js"
            if npx_cli.exists():
                return [node, str(npx_cli), "-y", "@xdevplatform/xurl"]
        npx = shutil.which("npx")
        if npx and not str(npx).lower().endswith(".cmd"):
            return [npx, "-y", "@xdevplatform/xurl"]
        return ["npx", "-y", "@xdevplatform/xurl"]

    def _check_budget(self) -> None:
        if self.calls_made >= self._api_call_budget:
            raise ApiBudgetExceeded(
                f"API call budget exhausted ({self._api_call_budget} calls)"
            )

    def _request(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        self._check_budget()
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                if self._mcp_call is not None:
                    payload = self._mcp_call(tool_name, params)
                else:
                    payload = self._xurl_request(tool_name, params)
                self.calls_made += 1
                if self._sleep_seconds:
                    time.sleep(self._sleep_seconds)
                if "errors" in payload and not payload.get("data") and not payload.get("includes"):
                    missing_id = _errors_indicate_not_found(payload["errors"])
                    if missing_id:
                        raise PostNotFoundError(
                            missing_id,
                            json.dumps(payload["errors"], ensure_ascii=False),
                        )
                    raise RuntimeError(json.dumps(payload["errors"], ensure_ascii=False))
                return payload
            except ApiBudgetExceeded:
                raise
            except PostNotFoundError:
                raise
            except Exception as exc:
                missing_id = _errors_indicate_not_found(_coerce_errors(exc))
                if missing_id:
                    raise PostNotFoundError(missing_id, str(exc)) from exc
                last_error = exc
                wait = self._sleep_seconds * (2**attempt)
                logger.warning("API error (attempt %s/%s): %s", attempt + 1, self._max_retries, exc)
                time.sleep(wait)

        if isinstance(last_error, PostNotFoundError):
            raise last_error
        raise RuntimeError(f"X API request failed after retries: {last_error}")

    def _xurl_request(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        path_template = MCP_TOOL_PATHS[tool_name]
        path = path_template
        query: dict[str, Any] = {}

        for key, value in params.items():
            if value is None:
                continue
            if key == "id" and "{id}" in path:
                path = path.replace("{id}", str(value))
                continue
            if key == "usernames" and tool_name == "get_users_by_usernames":
                path = f"{path}?usernames={value}"
                continue
            api_key = key.replace("post.fields", "tweet.fields")
            query[api_key] = value

        if query:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}{urlencode(query, doseq=True)}"

        cmd = list(self._xurl_command)
        if self._auth:
            cmd.extend(["--auth", self._auth])
        cmd.append(path)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            shell=False,
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            raise RuntimeError(stderr or stdout or f"xurl exited {result.returncode}")

        stdout = result.stdout.strip()
        if not stdout:
            return {}
        return json.loads(stdout)

    def search_posts(
        self,
        query: str,
        *,
        mode: str = "recent",
        max_results: int = 100,
        pagination_token: str | None = None,
        since_id: str | None = None,
        until_id: str | None = None,
        sort_order: str = "recency",
    ) -> dict[str, Any]:
        tool = "search_posts_all" if mode == "all" else "search_posts_recent"
        params: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "sort_order": sort_order,
            "post.fields": POST_FIELDS,
            "user.fields": USER_FIELDS,
            "expansions": SEARCH_EXPANSIONS,
        }
        token = pagination_token
        if token:
            params["pagination_token"] = token
            params["next_token"] = token
        if since_id:
            params["since_id"] = since_id
        if until_id:
            params["until_id"] = until_id
        return self._request(tool, params)

    def get_liking_users(
        self,
        post_id: str,
        *,
        max_results: int = 100,
        pagination_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": post_id,
            "max_results": max_results,
            "user.fields": USER_FIELDS,
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        return self._request("get_posts_liking_users", params)

    def get_reposted_by(
        self,
        post_id: str,
        *,
        max_results: int = 100,
        pagination_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": post_id,
            "max_results": max_results,
            "user.fields": USER_FIELDS,
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        return self._request("get_posts_reposted_by", params)

    def get_quoted_posts(
        self,
        post_id: str,
        *,
        max_results: int = 100,
        pagination_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": post_id,
            "max_results": max_results,
            "post.fields": "id,author_id",
            "user.fields": USER_FIELDS,
            "expansions": "author_id",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        return self._request("get_posts_quoted_posts", params)


def _coerce_errors(exc: Exception) -> Any:
    text = str(exc).strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None