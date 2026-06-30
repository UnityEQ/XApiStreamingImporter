from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


POST_FIELDS = (
    "id,text,author_id,created_at,public_metrics,referenced_tweets,entities,conversation_id"
)
USER_FIELDS = "id,username,name,profile_image_url"
SEARCH_EXPANSIONS = (
    "author_id,referenced_tweets.id,referenced_tweets.id.author_id,"
    "entities.mentions.username"
)


@dataclass
class CollectorConfig:
    """Cost-aware settings for incremental graph collection."""

    query: str
    work_dir: Path = Path("data")

    # Search: "recent" (7-day, OAuth user/app) or "all" (full archive, app-only).
    search_mode: str = "recent"
    sort_order: str = "recency"
    search_page_size: int = 100
    max_search_pages_per_run: int = 5

    # Forward-only incremental collection uses since_id from state when set.
    use_since_id: bool = True

    # Engagement expansion thresholds (score = likes + 2*RT + 3*quotes + replies).
    min_engagement_to_expand: int = 10
    high_engagement_threshold: int = 100
    expand_high_engagement: bool = True
    expand_medium_sample_rate: float = 0.25

    max_expansions_per_run: int = 50
    max_likers_per_post: int = 100
    max_reposters_per_post: int = 100
    max_quotes_per_post: int = 50
    expansion_page_size: int = 100

    # Hard budget: stop after this many API calls in one run.
    api_call_budget: int = 200
    sleep_seconds: float = 0.5
    max_retries: int = 3

    edge_types: tuple[str, ...] = ("MENTION", "REPLY", "RETWEET", "QUOTE", "LIKE")

    def __post_init__(self) -> None:
        self.work_dir = Path(self.work_dir)
        if self.search_page_size < 10:
            self.search_page_size = 10
        if self.search_page_size > 100:
            self.search_page_size = 100
        if self.expansion_page_size < 10:
            self.expansion_page_size = 10
        if self.expansion_page_size > 100:
            self.expansion_page_size = 100

    @property
    def state_db(self) -> Path:
        return self.work_dir / "state.db"

    @property
    def output_dir(self) -> Path:
        return self.work_dir / "output"