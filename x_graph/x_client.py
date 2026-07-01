from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlencode

from x_graph.config import POST_FIELDS, SEARCH_EXPANSIONS, USER_FIELDS

logger = logging.getLogger(__name__)

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


class ApiRateLimitError(Exception):
    """Raised when rate-limit retries are exhausted."""


class ApiFatalError(Exception):
    """Raised on auth/config errors — stop the run, do not retry."""


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


ErrorKind = Literal["rate_limit", "transient", "fatal"]


class _ApiResponseError(Exception):
    """Raw API/xurl failure before classification and retry policy."""

    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        returncode: int | None = None,
    ) -> None:
        self.message = message
        self.payload = payload
        self.returncode = returncode
        super().__init__(message)


def _http_status_from_text(text: str) -> int | None:
    lowered = text.lower()
    for pattern in (
        r"\bstatus(?:\s*code)?[:\s]+(\d{3})\b",
        r"\bhttp[:\s/]+(\d{3})\b",
        r"\berror[:\s]+(\d{3})\b",
    ):
        match = re.search(pattern, lowered)
        if match:
            return int(match.group(1))
    if re.search(r"\b429\b", lowered):
        return 429
    return None


def _classify_payload_errors(payload: dict[str, Any]) -> ErrorKind | None:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return None
    for err in errors:
        if not isinstance(err, dict):
            continue
        title = str(err.get("title", "")).lower()
        err_type = str(err.get("type", "")).lower()
        detail = str(err.get("detail", "")).lower()
        blob = " ".join((title, err_type, detail))
        if any(
            token in blob
            for token in (
                "too many requests",
                "rate limit",
                "rate-limit",
                "too-many-requests",
                "usage-capped",
            )
        ):
            return "rate_limit"
        if any(token in blob for token in ("forbidden", "unauthorized", "unsupported authentication")):
            return "fatal"
    return None


def _classify_api_error(
    message: str,
    payload: dict[str, Any] | None = None,
    returncode: int | None = None,
) -> ErrorKind:
    text = message.lower()
    status = returncode
    if status is None:
        status = _http_status_from_text(text)
    if payload:
        payload_status = payload.get("status") or payload.get("status_code")
        if isinstance(payload_status, int):
            status = payload_status
        payload_kind = _classify_payload_errors(payload)
        if payload_kind:
            return payload_kind
        title = str(payload.get("title", "")).lower()
        detail = str(payload.get("detail", "")).lower()
        blob = f"{title} {detail}"
        if any(
            token in blob
            for token in ("too many requests", "rate limit", "usage-capped")
        ):
            return "rate_limit"

    if status == 429:
        return "rate_limit"
    if status in (502, 503, 504):
        return "transient"
    if status in (401, 403):
        return "fatal"

    if any(
        token in text
        for token in (
            "too many requests",
            "rate limit",
            "rate-limit",
            "usage-capped",
            "usage cap",
            "throttl",
        )
    ):
        return "rate_limit"
    if any(
        token in text
        for token in (
            "401",
            "403",
            "unsupported authentication",
            "forbidden",
            "unauthorized",
        )
    ):
        return "fatal"
    if any(
        token in text
        for token in (
            "request failed",
            "502",
            "503",
            "504",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "temporarily unavailable",
            "econnreset",
            "etimedout",
            "timeout",
            "network",
            "socket hang up",
            "over capacity",
        )
    ):
        return "transient"
    return "fatal"


def _raise_classified_error(
    message: str,
    payload: dict[str, Any] | None = None,
    returncode: int | None = None,
) -> None:
    raise _ApiResponseError(message, payload=payload, returncode=returncode)


class XApiClient:
    """Thin client over X API v2, mirroring xapi MCP tool semantics."""

    def __init__(
        self,
        *,
        mcp_call: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        xurl_command: list[str] | None = None,
        api_call_budget: int = 30,
        sleep_seconds: float = 2.5,
        max_retries: int = 0,
        rate_limit_retries: int = 2,
        transient_retries: int = 1,
        rate_limit_backoff_seconds: float = 60.0,
        transient_backoff_seconds: float = 20.0,
        auth: str | None = None,
    ) -> None:
        self._mcp_call = mcp_call
        self._xurl_command = xurl_command or self._default_xurl_command()
        self._api_call_budget = api_call_budget
        self._sleep_seconds = sleep_seconds
        self._max_retries = max(0, max_retries)
        self._rate_limit_retries = max(0, rate_limit_retries)
        self._transient_retries = max(0, transient_retries)
        self._rate_limit_backoff_seconds = max(1.0, rate_limit_backoff_seconds)
        self._transient_backoff_seconds = max(1.0, transient_backoff_seconds)
        self._auth = auth
        self.calls_attempted = 0
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
        if self.calls_attempted >= self._api_call_budget:
            raise ApiBudgetExceeded(
                f"API call budget exhausted ({self._api_call_budget} calls)"
            )

    def _record_attempt(self) -> None:
        self.calls_attempted += 1

    def _backoff_seconds(self, kind: ErrorKind, attempt: int) -> float:
        base = (
            self._rate_limit_backoff_seconds
            if kind == "rate_limit"
            else self._transient_backoff_seconds
        )
        return min(300.0, base * (2 ** max(0, attempt - 1)))

    def _handle_response_errors(self, payload: dict[str, Any]) -> None:
        if "errors" not in payload or payload.get("data") or payload.get("includes"):
            return
        missing_id = _errors_indicate_not_found(payload["errors"])
        if missing_id:
            raise PostNotFoundError(
                missing_id,
                json.dumps(payload["errors"], ensure_ascii=False),
            )
        detail = json.dumps(payload["errors"], ensure_ascii=False)
        _raise_classified_error(detail, payload)

    def _request(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        rate_limit_attempts = 0
        transient_attempts = 0
        generic_attempts = 0
        max_generic_attempts = 1 + self._max_retries
        last_error: str | None = None

        while True:
            self._check_budget()
            self._record_attempt()
            try:
                if self._mcp_call is not None:
                    payload = self._mcp_call(tool_name, params)
                else:
                    payload = self._xurl_request(tool_name, params)
                self._handle_response_errors(payload)
                if self._sleep_seconds:
                    time.sleep(self._sleep_seconds)
                self.calls_made += 1
                return payload
            except ApiBudgetExceeded:
                raise
            except PostNotFoundError:
                raise
            except _ApiResponseError as exc:
                kind = _classify_api_error(
                    exc.message, exc.payload, exc.returncode
                )
                last_error = exc.message
                if kind == "rate_limit" and rate_limit_attempts < self._rate_limit_retries:
                    rate_limit_attempts += 1
                    wait = self._backoff_seconds(kind, rate_limit_attempts)
                    logger.warning(
                        "Rate limited (%s). Waiting %.0fs, retry %s/%s",
                        exc.message,
                        wait,
                        rate_limit_attempts,
                        self._rate_limit_retries,
                    )
                    time.sleep(wait)
                    continue
                if kind == "transient" and transient_attempts < self._transient_retries:
                    transient_attempts += 1
                    wait = self._backoff_seconds(kind, transient_attempts)
                    logger.warning(
                        "Transient API error (%s). Waiting %.0fs, retry %s/%s",
                        exc.message,
                        wait,
                        transient_attempts,
                        self._transient_retries,
                    )
                    time.sleep(wait)
                    continue
                if kind == "rate_limit":
                    raise ApiRateLimitError(exc.message) from exc
                raise ApiFatalError(exc.message) from exc
            except Exception as exc:
                missing_id = _errors_indicate_not_found(_coerce_errors(exc))
                if missing_id:
                    raise PostNotFoundError(missing_id, str(exc)) from exc
                message = str(exc)
                kind = _classify_api_error(message)
                last_error = message
                if kind == "rate_limit" and rate_limit_attempts < self._rate_limit_retries:
                    rate_limit_attempts += 1
                    wait = self._backoff_seconds(kind, rate_limit_attempts)
                    logger.warning(
                        "Rate limited (%s). Waiting %.0fs, retry %s/%s",
                        message,
                        wait,
                        rate_limit_attempts,
                        self._rate_limit_retries,
                    )
                    time.sleep(wait)
                    continue
                if kind == "transient" and transient_attempts < self._transient_retries:
                    transient_attempts += 1
                    wait = self._backoff_seconds(kind, transient_attempts)
                    logger.warning(
                        "Transient API error (%s). Waiting %.0fs, retry %s/%s",
                        message,
                        wait,
                        transient_attempts,
                        self._transient_retries,
                    )
                    time.sleep(wait)
                    continue
                if kind == "rate_limit":
                    raise ApiRateLimitError(message) from exc
                generic_attempts += 1
                if generic_attempts >= max_generic_attempts:
                    raise ApiFatalError(message) from exc
                logger.warning(
                    "API error (attempt %s/%s): %s",
                    generic_attempts,
                    max_generic_attempts,
                    exc,
                )
                time.sleep(self._sleep_seconds)

        raise ApiFatalError(f"X API request failed: {last_error}")

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
            check=False,
            timeout=120,
            shell=False,
            env=os.environ.copy(),
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()

        payload: dict[str, Any] | None = None
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = None

        if result.returncode != 0 or payload is None:
            detail = stderr or stdout or f"xurl exited {result.returncode}"
            _raise_classified_error(
                detail,
                payload,
                returncode=result.returncode if result.returncode != 0 else None,
            )

        return payload

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
        if pagination_token:
            params["next_token"] = pagination_token
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