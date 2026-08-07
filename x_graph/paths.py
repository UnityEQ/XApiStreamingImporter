from __future__ import annotations

import hashlib
import re
from pathlib import Path


def normalize_query(query: str) -> str:
    """Strip accidental shell quotes from a search query."""
    cleaned = query.strip()
    while len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "'\"":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def user_interaction_query(username: str) -> str:
    """Build a search query for one account's outbound + inbound interactions.

    Covers:
    - from:user  — posts they authored (mentions, replies, RTs, quotes they make)
    - to:user    — replies directed at them
    - @user      — posts that mention them

    This is the high-yield pattern for a force-directed ego graph around one user.
    Prefer with --search-only so each API page (~100 posts) becomes free edges.
    """
    handle = username.strip().lstrip("@")
    if not handle:
        raise ValueError("username must be non-empty")
    if any(ch.isspace() for ch in handle):
        raise ValueError(f"username must be a single handle, got {username!r}")
    return f"from:{handle} OR to:{handle} OR @{handle}"


def query_slug(query: str, max_len: int = 56) -> str:
    """Filesystem-safe slug from an X search query."""
    normalized = normalize_query(query).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        slug = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


def default_work_dir(query: str, base: Path = Path("data/queries")) -> Path:
    return base / query_slug(query)