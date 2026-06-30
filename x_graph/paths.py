from __future__ import annotations

import hashlib
import re
from pathlib import Path


def query_slug(query: str, max_len: int = 56) -> str:
    """Filesystem-safe slug from an X search query."""
    normalized = query.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        slug = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


def default_work_dir(query: str, base: Path = Path("data/queries")) -> Path:
    return base / query_slug(query)