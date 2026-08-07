from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_api_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_api_time(value: str) -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned).astimezone(timezone.utc)


def init_archive_window(
    *,
    chunk_days: int,
    lookback_days: int | None,
) -> tuple[str, str]:
    """Return (start_time, end_time) for the first archive chunk (newest window)."""
    end = utc_now()
    start = end - timedelta(days=chunk_days)
    if lookback_days is not None:
        earliest = end - timedelta(days=lookback_days)
        if start < earliest:
            start = earliest
    return to_api_time(start), to_api_time(end)


def archive_window_from_state(
    state: object,
    *,
    chunk_days: int,
    lookback_days: int | None,
) -> tuple[str | None, str | None]:
    """Load or initialize the active archive window from crawl state."""
    get_meta = state.get_meta  # type: ignore[attr-defined]
    set_meta = state.set_meta  # type: ignore[attr-defined]

    start = get_meta("archive_window_start")
    end = get_meta("archive_window_end")
    if start and end:
        return start, end

    start, end = init_archive_window(
        chunk_days=chunk_days,
        lookback_days=lookback_days,
    )
    set_meta("archive_window_start", start)
    set_meta("archive_window_end", end)
    set_meta("archive_can_expand", "1")
    return start, end


def archive_can_expand(
    state: object,
    *,
    lookback_days: int | None,
) -> bool:
    get_meta = state.get_meta  # type: ignore[attr-defined]
    flag = get_meta("archive_can_expand", "1")
    if flag != "1":
        return False
    start_s = get_meta("archive_window_start")
    if not start_s:
        return True
    if lookback_days is None:
        return True
    start = parse_api_time(start_s)
    earliest = utc_now() - timedelta(days=lookback_days)
    return start > earliest


def advance_archive_window(
    state: object,
    *,
    chunk_days: int,
    lookback_days: int | None,
) -> bool:
    """Step the archive window older. Returns False when lookback is exhausted."""
    get_meta = state.get_meta  # type: ignore[attr-defined]
    set_meta = state.set_meta  # type: ignore[attr-defined]
    clear_meta = state.clear_meta  # type: ignore[attr-defined]

    start_s = get_meta("archive_window_start")
    if not start_s:
        return False

    current_start = parse_api_time(start_s)
    new_end = current_start
    new_start = current_start - timedelta(days=chunk_days)
    earliest = None
    if lookback_days is not None:
        earliest = utc_now() - timedelta(days=lookback_days)

    if earliest is not None and new_start < earliest:
        if new_end <= earliest:
            set_meta("archive_can_expand", "0")
            return False
        new_start = earliest

    set_meta("archive_window_end", to_api_time(new_end))
    set_meta("archive_window_start", to_api_time(new_start))
    set_meta("archive_can_expand", "1")
    clear_meta("search_pagination_token")
    return True


def crawl_has_more(
    state: object,
    *,
    search_mode: str,
    auto_expand_archive: bool,
    lookback_days: int | None,
) -> bool:
    get_meta = state.get_meta  # type: ignore[attr-defined]
    if get_meta("search_pagination_token"):
        return True
    if search_mode == "all" and auto_expand_archive:
        return archive_can_expand(state, lookback_days=lookback_days)
    return False


def crawl_is_exhausted(
    state: object,
    *,
    search_mode: str,
    auto_expand_archive: bool,
    lookback_days: int | None,
) -> bool:
    """True when a prior backward crawl finished and another search would only re-fetch.

    First-ever run (no posts yet) is never exhausted. Incremental / --fresh callers
    should ignore this and always search.
    """
    get_meta = state.get_meta  # type: ignore[attr-defined]
    if get_meta("search_exhausted") == "1":
        return True
    # Heuristic for older state.db files without the flag: we have posts, no
    # pagination cursor, and archive (if any) cannot step older.
    stats = state.stats()  # type: ignore[attr-defined]
    if int(stats.get("seen_posts", 0) or 0) <= 0:
        return False
    if crawl_has_more(
        state,
        search_mode=search_mode,
        auto_expand_archive=auto_expand_archive,
        lookback_days=lookback_days,
    ):
        return False
    # Recent mode with posts and no token means the 7-day window was fully paged.
    # Archive mode with can_expand=0 is fully stepped through lookback.
    return True


def mark_search_exhausted(state: object) -> None:
    set_meta = state.set_meta  # type: ignore[attr-defined]
    set_meta("search_exhausted", "1")


def clear_search_exhausted(state: object) -> None:
    clear_meta = state.clear_meta  # type: ignore[attr-defined]
    clear_meta("search_exhausted")