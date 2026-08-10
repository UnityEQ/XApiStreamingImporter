from __future__ import annotations

import logging
import random
from typing import Any

from x_graph.archive_window import (
    advance_archive_window,
    archive_window_from_state,
    clear_search_exhausted,
    crawl_has_more,
    crawl_is_exhausted,
    mark_search_exhausted,
)
from x_graph.config import CollectorConfig
from x_graph.graph import InteractionEdge, InteractionGraph
from x_graph.state import StateStore
from x_graph.offline import OfflineXApiClient
from x_graph.run_lock import RunLock
from x_graph.x_client import (
    ApiBudgetExceeded,
    ApiFatalError,
    ApiRateLimitError,
    ApiSpendExceeded,
    PostNotFoundError,
    XApiClient,
)

logger = logging.getLogger(__name__)


class GraphCollector:
    """Incrementally collect an interaction graph from X search results."""

    def __init__(self, config: CollectorConfig, client: XApiClient | None = None) -> None:
        self.config = config
        self.state = StateStore(config.state_db)
        if client is not None:
            self.client = client
        elif config.dry_run:
            self.client = OfflineXApiClient()
        else:
            auth = "app" if config.search_mode == "all" else None
            self.client = XApiClient(
                api_call_budget=config.api_call_budget,
                max_spend_usd=config.max_spend_usd,
                post_read_usd=config.post_read_usd,
                user_read_usd=config.user_read_usd,
                sleep_seconds=config.sleep_seconds,
                max_retries=config.max_retries,
                rate_limit_retries=config.rate_limit_retries,
                transient_retries=config.transient_retries,
                rate_limit_backoff_seconds=config.rate_limit_backoff_seconds,
                transient_backoff_seconds=config.transient_backoff_seconds,
                auth=auth,
            )

    def run_once(self) -> dict[str, Any]:
        lock = RunLock(self.config.work_dir / ".collect.lock")
        with lock:
            return self._run_once_locked()

    def _run_once_locked(self) -> dict[str, Any]:
        self._prepare_run()
        if hasattr(self.client, "calls_attempted"):
            self.client.calls_attempted = 0
            self.client.calls_made = 0
        if hasattr(self.client, "estimated_spend_usd"):
            self.client.estimated_spend_usd = 0.0
            self.client.estimated_posts = 0
            self.client.estimated_users = 0
            if hasattr(self.client, "last_response_spend_usd"):
                self.client.last_response_spend_usd = 0.0
            # Fresh run: re-learn $/slot from this run's first full page.
            if hasattr(self.client, "last_search_max_results"):
                self.client.last_search_max_results = 0
            if hasattr(self.client, "last_search_spend_usd"):
                self.client.last_search_spend_usd = 0.0
        if self.config.dry_run:
            logger.info("DRY RUN — no X API calls will be made")
            return self._dry_run_summary()

        self.state.set_meta("query", self.config.query)
        self.state.set_meta("search_mode", self.config.search_mode)
        self.state.set_meta("collection_mode", self.config.collection_mode)
        summary: dict[str, Any] = {
            "query": self.config.query,
            "work_dir": str(self.config.work_dir),
            "collection_mode": self.config.collection_mode,
            "search_posts_new": 0,
            "edges_added": 0,
            "expansions_done": 0,
            "api_calls_attempted": 0,
            "api_calls_ok": 0,
            "estimated_spend_usd": 0.0,
            "estimated_spend_total_usd": self._load_total_spend(),
            "max_spend_usd": self.config.max_spend_usd,
            "pricing_note": (
                "estimated: search bills data posts only; user-list endpoints bill "
                "data users; includes.* not counted — not official X bill"
            ),
            "stopped_reason": "completed",
        }
        search_ok = True
        self._search_posts_new_this_run = 0
        self._edges_added_this_run = 0

        # Avoid re-burning credits when a backward crawl already finished.
        # --fresh and --incremental always search.
        if (
            self.config.collection_mode == "backward"
            and not self.config.fresh
            and crawl_is_exhausted(
                self.state,
                search_mode=self.config.search_mode,
                auto_expand_archive=self.config.auto_expand_archive,
                lookback_days=self.config.lookback_days,
            )
        ):
            logger.info(
                "Search already exhausted for this query — skipping API calls. "
                "Use --fresh to restart from newest, or --incremental for new posts only."
            )
            summary["stopped_reason"] = "search_exhausted"
            summary["api_calls_attempted"] = self.client.calls_attempted
            summary["api_calls_ok"] = self.client.calls_made
            summary.update(self.state.stats())
            summary["has_more_older_posts"] = False
            if self.config.search_mode == "all" and self.config.auto_expand_archive:
                summary["archive_window_start"] = self.state.get_meta("archive_window_start")
                summary["archive_window_end"] = self.state.get_meta("archive_window_end")
            self.state.log_event("run_complete", summary)
            return summary

        try:
            summary["search_posts_new"] = self._collect_search_pages()
        except ApiBudgetExceeded:
            summary["stopped_reason"] = "api_budget_exhausted"
            summary["search_posts_new"] = self._search_posts_new_this_run
            search_ok = False
        except ApiSpendExceeded as exc:
            summary["stopped_reason"] = "max_spend_reached"
            summary["search_posts_new"] = self._search_posts_new_this_run
            logger.info("Stopping: %s", exc)
            search_ok = False
        except ApiRateLimitError as exc:
            summary["stopped_reason"] = type(exc).__name__
            summary["search_posts_new"] = self._search_posts_new_this_run
            logger.error(
                "Rate limit persisted after backoff retries — try again later: %s",
                exc,
            )
            search_ok = False
        except ApiFatalError as exc:
            summary["stopped_reason"] = type(exc).__name__
            summary["search_posts_new"] = self._search_posts_new_this_run
            logger.error("Stopping run immediately: %s", exc)
            search_ok = False
        except RuntimeError as exc:
            summary["stopped_reason"] = "search_failed"
            summary["search_posts_new"] = self._search_posts_new_this_run
            logger.error("Search failed, skipping expansions: %s", exc)
            search_ok = False

        if (
            search_ok
            and not self.config.search_only
            and self.config.max_expansions_per_run > 0
        ):
            try:
                summary["expansions_done"] = self._process_expansion_queue()
            except ApiBudgetExceeded:
                summary["stopped_reason"] = "api_budget_exhausted"
            except ApiSpendExceeded as exc:
                summary["stopped_reason"] = "max_spend_reached"
                logger.info("Stopping expansions: %s", exc)
            except ApiRateLimitError as exc:
                summary["stopped_reason"] = type(exc).__name__
                logger.error(
                    "Rate limit persisted after backoff retries — try again later: %s",
                    exc,
                )
            except ApiFatalError as exc:
                summary["stopped_reason"] = type(exc).__name__
                logger.error("Stopping expansions immediately: %s", exc)

        summary["api_calls_attempted"] = self.client.calls_attempted
        summary["api_calls_ok"] = self.client.calls_made
        summary["estimated_spend_usd"] = getattr(
            self.client, "estimated_spend_usd", 0.0
        )
        summary["estimated_posts_billed"] = getattr(
            self.client, "estimated_posts", 0
        )
        summary["estimated_users_billed"] = getattr(
            self.client, "estimated_users", 0
        )
        summary["estimated_spend_total_usd"] = self._add_total_spend(
            float(summary["estimated_spend_usd"])
        )
        summary.update(self.state.stats())
        # Per-run counter (stats() only has cumulative totals like "edges").
        summary["edges_added"] = self._edges_added_this_run
        summary["has_more_older_posts"] = crawl_has_more(
            self.state,
            search_mode=self.config.search_mode,
            auto_expand_archive=self.config.auto_expand_archive,
            lookback_days=self.config.lookback_days,
        )
        if self.config.search_mode == "all" and self.config.auto_expand_archive:
            summary["archive_window_start"] = self.state.get_meta("archive_window_start")
            summary["archive_window_end"] = self.state.get_meta("archive_window_end")
        self.state.log_event("run_complete", summary)
        return summary

    def _load_total_spend(self) -> float:
        raw = self.state.get_meta("estimated_spend_total_usd")
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    def _add_total_spend(self, run_spend: float) -> float:
        total = round(self._load_total_spend() + max(0.0, run_spend), 6)
        self.state.set_meta("estimated_spend_total_usd", f"{total:.6f}")
        return total

    def _dry_run_summary(self) -> dict[str, Any]:
        batch = self.state.pop_expansion_batch(self.config.max_expansions_per_run)
        for item in batch:
            self.state.enqueue_expansion(
                item["post_id"],
                item["author_id"],
                item["engagement"],
                item["priority"],
            )
        from x_graph.pricing import PricingConfig

        pricing = PricingConfig(
            post_read_usd=self.config.post_read_usd,
            user_read_usd=self.config.user_read_usd,
        )
        page_ceiling = pricing.estimate_search_page_ceiling(
            self.config.search_page_size
        )
        return {
            "query": self.config.query,
            "work_dir": str(self.config.work_dir),
            "collection_mode": self.config.collection_mode,
            "dry_run": True,
            "stopped_reason": "dry_run",
            "would_search_pages": self.config.max_search_pages_per_run,
            "would_expansions": min(
                self.config.max_expansions_per_run,
                self.state.stats().get("queued_expansions", 0),
            ),
            "api_calls_attempted": 0,
            "api_calls_ok": 0,
            "max_spend_usd": self.config.max_spend_usd,
            "estimated_spend_total_usd": self._load_total_spend(),
            "pricing": {
                "post_read_usd": self.config.post_read_usd,
                "user_read_usd": self.config.user_read_usd,
                "worst_case_per_search_page_usd": round(page_ceiling, 4),
                "note": (
                    "Search estimate: data posts only (~$0.005 each); search includes "
                    "are not counted. User-list expansions (likers etc.) bill data users "
                    "(~$0.01). --api-budget is HTTP attempts; --max-spend is an estimate cap."
                ),
            },
            **self.state.stats(),
        }

    def _prepare_run(self) -> None:
        stored_query = self.state.get_meta("query")
        if self.config.fresh:
            self.state.reset_search_cursors()
            clear_search_exhausted(self.state)
            self.state.log_event("fresh_start", {"query": self.config.query})
        elif stored_query and stored_query != self.config.query:
            logger.warning(
                "Query changed in %s (%r → %r); resetting search cursors.",
                self.config.work_dir,
                stored_query,
                self.config.query,
            )
            self.state.reset_search_cursors()
            clear_search_exhausted(self.state)

        if self.config.collection_mode == "backward":
            self.state.clear_meta("since_id")
        elif self.config.collection_mode == "incremental":
            # Incremental always looks for newer posts; clear exhausted flag.
            clear_search_exhausted(self.state)

    def _search_pages_this_run(self) -> int:
        if self.config.search_only:
            return min(
                self.config.max_search_pages_per_run,
                max(0, self.config.api_call_budget - self.client.calls_attempted),
            )
        reserve = min(
            self.config.min_calls_for_expansion,
            max(0, self.config.api_call_budget - 1),
        )
        available = max(1, self.config.api_call_budget - reserve - self.client.calls_attempted)
        return min(self.config.max_search_pages_per_run, available)

    def _search_max_results_this_page(self) -> int:
        """Preferred page size, shrunk near ``max_spend_usd`` to use leftover budget."""
        preferred = self.config.search_page_size
        adaptive = getattr(self.client, "adaptive_search_max_results", None)
        if callable(adaptive) and self.config.max_spend_usd is not None:
            return int(adaptive(preferred))
        return preferred

    def _expansions_this_run(self) -> int:
        if self.config.search_only:
            return 0
        remaining = max(0, self.config.api_call_budget - self.client.calls_attempted)
        if remaining < 3:
            return 0
        return min(self.config.max_expansions_per_run, remaining // 3)

    def _search_time_bounds(self) -> tuple[str | None, str | None]:
        if (
            self.config.search_mode != "all"
            or not self.config.auto_expand_archive
            or self.config.collection_mode == "incremental"
        ):
            return None, None
        return archive_window_from_state(
            self.state,
            chunk_days=self.config.archive_chunk_days,
            lookback_days=self.config.lookback_days,
        )

    def _maybe_expand_archive_window(self, posts_on_page: int) -> bool:
        if (
            self.config.search_mode != "all"
            or not self.config.auto_expand_archive
            or self.config.collection_mode == "incremental"
        ):
            return False
        if posts_on_page >= self.config.search_page_size:
            return False
        expanded = advance_archive_window(
            self.state,
            chunk_days=self.config.archive_chunk_days,
            lookback_days=self.config.lookback_days,
        )
        if expanded:
            start = self.state.get_meta("archive_window_start")
            end = self.state.get_meta("archive_window_end")
            logger.info(
                "Sparse page (%s posts) — expanding archive window to %s → %s",
                posts_on_page,
                start,
                end,
            )
        return expanded

    def _collect_search_pages(self) -> int:
        new_posts = 0
        self._search_posts_new_this_run = 0
        pagination_token = self.state.get_meta("search_pagination_token")
        since_id: str | None = None
        if self.config.collection_mode == "incremental":
            since_id = self.state.get_meta("since_id")

        page_limit = self._search_pages_this_run()
        empty_archive_windows = 0
        for page in range(page_limit):
            request_token = pagination_token
            start_time, end_time = self._search_time_bounds()
            max_results = self._search_max_results_this_page()
            if max_results <= 0:
                self._search_posts_new_this_run = new_posts
                raise ApiSpendExceeded(
                    "Estimated spend cap reached — remaining budget cannot cover "
                    "another search page (even a minimum page of 10 results)"
                )
            try:
                payload = self.client.search_posts(
                    self.config.query,
                    mode=self.config.search_mode,
                    max_results=max_results,
                    pagination_token=pagination_token,
                    since_id=since_id,
                    start_time=start_time,
                    end_time=end_time,
                    sort_order=self.config.sort_order,
                )
            except ApiBudgetExceeded:
                self._search_posts_new_this_run = new_posts
                raise
            except ApiSpendExceeded:
                self._search_posts_new_this_run = new_posts
                raise
            except (ApiRateLimitError, ApiFatalError):
                self._search_posts_new_this_run = new_posts
                raise
            except RuntimeError as exc:
                self._search_posts_new_this_run = new_posts
                logger.error("Search failed on page %s: %s", page + 1, exc)
                raise

            # Any successful search means we are actively crawling again.
            clear_search_exhausted(self.state)

            posts = payload.get("data") or []
            includes = payload.get("includes") or {}
            users = {u["id"]: u for u in includes.get("users", [])}
            ref_posts = {p["id"]: p for p in includes.get("tweets", [])}

            for user in users.values():
                self._persist_user(user)

            page_new = 0
            for post in posts:
                if self.state.mark_post_seen(
                    post["id"],
                    engagement=InteractionGraph.engagement_score(post.get("public_metrics")),
                    created_at=post.get("created_at"),
                ):
                    page_new += 1
                    new_posts += 1
                    self._ingest_post_edges(post, users, ref_posts)
            self._search_posts_new_this_run = new_posts

            meta = payload.get("meta") or {}
            newest_id = meta.get("newest_id")
            if newest_id and self.config.collection_mode == "incremental":
                current = self.state.get_meta("since_id")
                if not current or int(newest_id) > int(current):
                    self.state.set_meta("since_id", str(newest_id))

            # Zero-new page: stop paging this run so we do not re-walk history.
            # Common after a completed crawl restarts from the newest page.
            if (
                self.config.stop_on_duplicate_page
                and self.config.collection_mode == "backward"
                and posts
                and page_new == 0
            ):
                logger.info(
                    "Search page returned %s already-seen posts (0 new) — "
                    "stopping to avoid re-crawl credit burn. Use --fresh to restart.",
                    len(posts),
                )
                self.state.set_meta("search_pagination_token", "")
                # Head of results already fully ingested: try next archive window
                # once, otherwise mark exhausted so future runs skip free.
                if not request_token:
                    if self._maybe_expand_archive_window(len(posts)):
                        empty_archive_windows += 1
                        if (
                            empty_archive_windows
                            >= self.config.max_empty_archive_windows_per_run
                        ):
                            break
                        pagination_token = None
                        continue
                    mark_search_exhausted(self.state)
                break

            next_token = meta.get("next_token") or meta.get("pagination_token")
            if not next_token or not posts:
                self.state.set_meta("search_pagination_token", "")
                if not posts:
                    empty_archive_windows += 1
                if self._maybe_expand_archive_window(len(posts)):
                    if empty_archive_windows >= self.config.max_empty_archive_windows_per_run:
                        logger.info(
                            "Hit %s empty/sparse archive windows this run — "
                            "pausing. Re-run collect to continue stepping older.",
                            empty_archive_windows,
                        )
                        break
                    pagination_token = None
                    continue
                mark_search_exhausted(self.state)
                break
            # Full page with some new posts — reset empty-window streak.
            empty_archive_windows = 0
            pagination_token = next_token
            self.state.set_meta("search_pagination_token", next_token)

        self._search_posts_new_this_run = new_posts
        return new_posts

    def _ingest_post_edges(
        self,
        post: dict[str, Any],
        users: dict[str, Any],
        ref_posts: dict[str, Any],
    ) -> None:
        author_id = str(post.get("author_id", ""))
        if not author_id:
            return

        author = users.get(author_id)
        if author:
            self._persist_user(author)

        post_id = post["id"]
        metrics = post.get("public_metrics")
        engagement = InteractionGraph.engagement_score(metrics)

        for mention in (post.get("entities") or {}).get("mentions", []):
            target_id = str(mention.get("id") or "")
            if not target_id:
                continue
            self._persist_edge(author_id, target_id, "MENTION", post_id=post_id)
            self._persist_user(
                {
                    "id": target_id,
                    "username": mention.get("username", ""),
                    "name": mention.get("username", ""),
                }
            )

        for ref in post.get("referenced_tweets", []):
            ref_id = ref.get("id")
            ref_type = ref.get("type")
            ref_post = ref_posts.get(ref_id, {})
            target_author = str(ref_post.get("author_id", ""))
            if not target_author:
                continue
            if ref_type == "replied_to":
                self._persist_edge(author_id, target_author, "REPLY", post_id=post_id)
            elif ref_type == "retweeted":
                self._persist_edge(author_id, target_author, "RETWEET", post_id=post_id)
            elif ref_type == "quoted":
                self._persist_edge(author_id, target_author, "QUOTE", post_id=post_id)

        expand_post_id, expand_author_id, expand_engagement = self._expansion_target(
            post, ref_posts
        )
        if expand_post_id and expand_author_id and self._should_enqueue_expansion(
            expand_post_id, expand_engagement
        ):
            priority = float(expand_engagement) + random.random()
            self.state.enqueue_expansion(
                expand_post_id, expand_author_id, expand_engagement, priority
            )

    def _expansion_target(
        self,
        post: dict[str, Any],
        ref_posts: dict[str, Any],
    ) -> tuple[str, str, int]:
        """Resolve the tweet/author to expand (original for pure retweets)."""
        post_id = str(post["id"])
        author_id = str(post.get("author_id", ""))
        engagement = InteractionGraph.engagement_score(post.get("public_metrics"))

        refs = post.get("referenced_tweets") or []
        retweet_refs = [r for r in refs if r.get("type") == "retweeted"]
        if len(refs) == 1 and len(retweet_refs) == 1:
            orig_id = str(retweet_refs[0]["id"])
            orig_post = ref_posts.get(orig_id, {})
            orig_author = str(orig_post.get("author_id", ""))
            if orig_id and orig_author:
                orig_engagement = InteractionGraph.engagement_score(
                    orig_post.get("public_metrics")
                )
                return orig_id, orig_author, max(engagement, orig_engagement)
        return post_id, author_id, engagement

    def _should_enqueue_expansion(self, post_id: str, engagement: int) -> bool:
        if self.state.is_post_expanded(post_id):
            return False
        if engagement < self.config.min_engagement_to_expand:
            return False
        if engagement >= self.config.high_engagement_threshold:
            return self.config.expand_high_engagement
        return random.random() < self.config.expand_medium_sample_rate

    def _process_expansion_queue(self) -> int:
        done = 0
        batch = self.state.pop_expansion_batch(self._expansions_this_run())
        if not batch:
            return 0
        for item in batch:
            post_id = item["post_id"]
            author_id = item["author_id"]
            if self.state.is_post_expanded(post_id):
                continue
            try:
                self._expand_post(post_id, author_id)
                self.state.mark_post_expanded(post_id)
                done += 1
            except PostNotFoundError as exc:
                logger.info("Skipping unavailable post %s: %s", post_id, exc.detail or exc)
                self.state.mark_post_expanded(post_id)
                self.state.log_event("post_not_found", {"post_id": post_id, "detail": str(exc)})
                done += 1
            except (ApiRateLimitError, ApiFatalError):
                raise
            except ApiBudgetExceeded:
                raise
            except RuntimeError as exc:
                logger.error("Stopping expansions after API error on %s: %s", post_id, exc)
                self.state.log_event("expansion_failed", {"post_id": post_id, "detail": str(exc)})
                break
        return done

    def _expand_post(self, post_id: str, author_id: str) -> None:
        if "LIKE" in self.config.edge_types:
            self._expand_likers(post_id, author_id)
        if "RETWEET" in self.config.edge_types:
            self._expand_reposters(post_id, author_id)
        if "QUOTE" in self.config.edge_types:
            self._expand_quotes(post_id, author_id)

    def _expand_likers(self, post_id: str, author_id: str) -> None:
        fetched = 0
        token: str | None = None
        while fetched < self.config.max_likers_per_post:
            page_size = min(
                self.config.expansion_page_size,
                self.config.max_likers_per_post - fetched,
            )
            payload = self.client.get_liking_users(
                post_id, max_results=page_size, pagination_token=token
            )
            users = payload.get("data") or []
            for user in users:
                self._persist_user(user)
                self._persist_edge(str(user["id"]), author_id, "LIKE", post_id=post_id)
            fetched += len(users)
            token = (payload.get("meta") or {}).get("next_token")
            if not token or not users:
                break

    def _expand_reposters(self, post_id: str, author_id: str) -> None:
        fetched = 0
        token: str | None = None
        while fetched < self.config.max_reposters_per_post:
            page_size = min(
                self.config.expansion_page_size,
                self.config.max_reposters_per_post - fetched,
            )
            payload = self.client.get_reposted_by(
                post_id, max_results=page_size, pagination_token=token
            )
            users = payload.get("data") or []
            for user in users:
                self._persist_user(user)
                self._persist_edge(str(user["id"]), author_id, "RETWEET", post_id=post_id)
            fetched += len(users)
            token = (payload.get("meta") or {}).get("next_token")
            if not token or not users:
                break

    def _expand_quotes(self, post_id: str, author_id: str) -> None:
        fetched = 0
        token: str | None = None
        while fetched < self.config.max_quotes_per_post:
            page_size = min(
                self.config.expansion_page_size,
                self.config.max_quotes_per_post - fetched,
            )
            payload = self.client.get_quoted_posts(
                post_id, max_results=page_size, pagination_token=token
            )
            quotes = payload.get("data") or []
            users = {u["id"]: u for u in (payload.get("includes") or {}).get("users", [])}
            for quote in quotes:
                quoter_id = str(quote.get("author_id", ""))
                if not quoter_id:
                    continue
                user = users.get(quoter_id, {"id": quoter_id})
                self._persist_user(user)
                self._persist_edge(quoter_id, author_id, "QUOTE", post_id=quote.get("id"))
            fetched += len(quotes)
            token = (payload.get("meta") or {}).get("next_token")
            if not token or not quotes:
                break

    def _persist_user(self, user: dict[str, Any]) -> None:
        from x_graph.graph import UserNode

        self.state.upsert_node(
            UserNode(
                str(user["id"]),
                user.get("username", "") or "",
                user.get("name", "") or "",
                user.get("profile_image_url", "") or "",
            )
        )

    def _persist_edge(
        self,
        source_id: str,
        target_id: str,
        interaction: str,
        *,
        post_id: str | None = None,
    ) -> None:
        if source_id == target_id:
            return
        self.state.add_edge(
            InteractionEdge(source_id, target_id, interaction, 1, post_id)
        )
        # Count each recorded interaction event this run (including weight
        # bumps on existing source/target/type keys). Self-loops are skipped.
        self._edges_added_this_run = getattr(self, "_edges_added_this_run", 0) + 1