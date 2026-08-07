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
from x_graph.pricing import PricingConfig, estimate_payload_usd

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


class ApiSpendExceeded(Exception):
    """Raised when the estimated dollar spend cap is reached."""


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


def _clean_xurl_text(text: str) -> str:
    """Strip xurl noise so embedded API JSON can be parsed."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower() in {"error: request failed", "error: request failed."}:
            continue
        if stripped.lower().startswith("error: auth error:"):
            return stripped
        lines.append(line)
    return "\n".join(lines).strip()


def _parse_xurl_payload(stdout: str, stderr: str) -> tuple[dict[str, Any] | None, str]:
    """Parse JSON from xurl stdout/stderr, tolerating malformed error wrappers."""
    chunks = [_clean_xurl_text(stdout), _clean_xurl_text(stderr)]
    combined = "\n".join(chunk for chunk in chunks if chunk)
    for candidate in chunks + ([combined] if combined else []):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", candidate)
            if not match:
                continue
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, dict):
            return parsed, combined or candidate
    return None, combined or stderr or stdout


def _format_api_error_detail(
    payload: dict[str, Any] | None,
    fallback: str,
    *,
    auth: str | None = None,
    tool_name: str | None = None,
) -> str:
    if not payload:
        if "bearer token not found" in fallback.lower():
            return (
                "Bearer token not found for app-only auth. "
                "Full-archive search (--search-mode all) requires a bearer token "
                "from developer.x.com → your app → Keys and tokens: "
                "npx -y @xdevplatform/xurl auth app-only <BEARER_TOKEN>"
            )
        return fallback

    title = str(payload.get("title", "")).strip()
    detail = str(payload.get("detail", "")).strip()
    status = payload.get("status")
    parts = [part for part in (title, detail) if part]
    message = ": ".join(parts) if parts else fallback
    if status is not None:
        message = f"{message} (HTTP {status})"

    lowered = message.lower()
    if (
        tool_name == "search_posts_all"
        or "unsupported authentication" in lowered
        or (
            auth != "app"
            and "oauth 2.0 user context is forbidden" in lowered
        )
    ):
        message += (
            ". Full-archive search requires app-only bearer auth "
            "(--auth app). Use --search-mode recent for the last 7 days "
            "with OAuth user auth, or store a bearer token via "
            "xurl auth app-only <BEARER_TOKEN>"
        )
    return message


class XApiClient:
    """Thin client over X API v2, mirroring xapi MCP tool semantics."""

    def __init__(
        self,
        *,
        mcp_call: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        xurl_command: list[str] | None = None,
        api_call_budget: int = 30,
        max_spend_usd: float | None = None,
        post_read_usd: float = 0.005,
        user_read_usd: float = 0.01,
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
        self._max_spend_usd = max_spend_usd
        self._pricing = PricingConfig(
            post_read_usd=post_read_usd,
            user_read_usd=user_read_usd,
        )
        self._sleep_seconds = sleep_seconds
        self._max_retries = max(0, max_retries)
        self._rate_limit_retries = max(0, rate_limit_retries)
        self._transient_retries = max(0, transient_retries)
        self._rate_limit_backoff_seconds = max(1.0, rate_limit_backoff_seconds)
        self._transient_backoff_seconds = max(1.0, transient_backoff_seconds)
        self._auth = auth
        self.calls_attempted = 0
        self.calls_made = 0
        # Estimated USD from resources returned this run (not official billing).
        self.estimated_spend_usd = 0.0
        self.estimated_posts = 0
        self.estimated_users = 0
        self.last_response_spend_usd = 0.0
        # Hint for logging / dry-run only (not a hard pre-call gate on first request).
        self.next_call_ceiling_usd = self._pricing.estimate_search_page_ceiling(100)

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
        if self._max_spend_usd is not None:
            remaining = self._max_spend_usd - self.estimated_spend_usd
            if remaining <= 0:
                raise ApiSpendExceeded(
                    f"Estimated spend cap reached "
                    f"(${self.estimated_spend_usd:.4f} / ${self._max_spend_usd:.4f})"
                )
            # After at least one response: stop if remaining cannot cover another
            # similar page (uses actual last cost so we rarely overshoot the cap).
            if (
                self.calls_made > 0
                and self.last_response_spend_usd > 0
                and remaining < self.last_response_spend_usd
            ):
                raise ApiSpendExceeded(
                    f"Estimated spend near cap "
                    f"(${self.estimated_spend_usd:.4f} / ${self._max_spend_usd:.4f}; "
                    f"remaining ${remaining:.4f} < last page ~$"
                    f"{self.last_response_spend_usd:.4f})"
                )

    def _record_attempt(self) -> None:
        self.calls_attempted += 1

    def _record_payload_spend(self, payload: dict[str, Any]) -> None:
        est = estimate_payload_usd(payload, self._pricing)
        cost = float(est["total_usd"])
        self.estimated_posts += int(est["posts"])
        self.estimated_users += int(est["users"])
        self.last_response_spend_usd = cost
        self.estimated_spend_usd = round(self.estimated_spend_usd + cost, 6)
        logger.info(
            "Est. +$%.4f this response (%s posts, %s users) → run total ~$%.4f%s",
            cost,
            est["posts"],
            est["users"],
            self.estimated_spend_usd,
            (
                f" / cap ${self._max_spend_usd:.4f}"
                if self._max_spend_usd is not None
                else ""
            ),
        )

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
                self._record_payload_spend(payload)
                if self._sleep_seconds:
                    time.sleep(self._sleep_seconds)
                self.calls_made += 1
                return payload
            except ApiBudgetExceeded:
                raise
            except ApiSpendExceeded:
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

        payload, raw_detail = _parse_xurl_payload(stdout, stderr)
        status_code: int | None = None
        if payload is not None:
            payload_status = payload.get("status") or payload.get("status_code")
            if isinstance(payload_status, int):
                status_code = payload_status

        if result.returncode != 0 or payload is None:
            if payload and (payload.get("data") or payload.get("includes")):
                return payload
            detail = _format_api_error_detail(
                payload,
                raw_detail or stderr or stdout or f"xurl exited {result.returncode}",
                auth=self._auth,
                tool_name=tool_name,
            )
            _raise_classified_error(
                detail,
                payload,
                returncode=(
                    status_code
                    if status_code is not None
                    else (result.returncode if result.returncode != 0 else None)
                ),
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
        start_time: str | None = None,
        end_time: str | None = None,
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
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        self.next_call_ceiling_usd = self._pricing.estimate_search_page_ceiling(
            max_results
        )
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
        self.next_call_ceiling_usd = self._pricing.estimate_user_list_ceiling(
            max_results
        )
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
        self.next_call_ceiling_usd = self._pricing.estimate_user_list_ceiling(
            max_results
        )
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
        self.next_call_ceiling_usd = self._pricing.estimate_search_page_ceiling(
            max_results
        )
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