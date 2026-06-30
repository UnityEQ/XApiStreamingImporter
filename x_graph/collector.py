from __future__ import annotations

import logging
import random
from typing import Any

from x_graph.config import CollectorConfig
from x_graph.graph import InteractionEdge, InteractionGraph
from x_graph.state import StateStore
from x_graph.offline import OfflineXApiClient
from x_graph.run_lock import RunLock
from x_graph.x_client import (
    ApiBudgetExceeded,
    ApiFatalError,
    ApiRateLimitError,
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
            self.client = XApiClient(
                api_call_budget=config.api_call_budget,
                sleep_seconds=config.sleep_seconds,
                max_retries=config.max_retries,
            )

    def run_once(self) -> dict[str, Any]:
        lock = RunLock(self.config.work_dir / ".collect.lock")
        with lock:
            return self._run_once_locked()

    def _run_once_locked(self) -> dict[str, Any]:
        self._prepare_run()
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
            "stopped_reason": "completed",
        }
        search_ok = True

        try:
            summary["search_posts_new"] = self._collect_search_pages()
        except ApiBudgetExceeded:
            summary["stopped_reason"] = "api_budget_exhausted"
            search_ok = False
        except (ApiRateLimitError, ApiFatalError) as exc:
            summary["stopped_reason"] = type(exc).__name__
            logger.error("Stopping run immediately (no retries): %s", exc)
            search_ok = False
        except RuntimeError as exc:
            summary["stopped_reason"] = "search_failed"
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
            except (ApiRateLimitError, ApiFatalError) as exc:
                summary["stopped_reason"] = type(exc).__name__
                logger.error("Stopping expansions immediately: %s", exc)

        summary["api_calls_attempted"] = self.client.calls_attempted
        summary["api_calls_ok"] = self.client.calls_made
        summary.update(self.state.stats())
        self.state.log_event("run_complete", summary)
        return summary

    def _dry_run_summary(self) -> dict[str, Any]:
        batch = self.state.pop_expansion_batch(self.config.max_expansions_per_run)
        for item in batch:
            self.state.enqueue_expansion(
                item["post_id"],
                item["author_id"],
                item["engagement"],
                item["priority"],
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
            **self.state.stats(),
        }

    def _prepare_run(self) -> None:
        stored_query = self.state.get_meta("query")
        if self.config.fresh:
            self.state.reset_search_cursors()
            self.state.log_event("fresh_start", {"query": self.config.query})
        elif stored_query and stored_query != self.config.query:
            logger.warning(
                "Query changed in %s (%r → %r); resetting search cursors.",
                self.config.work_dir,
                stored_query,
                self.config.query,
            )
            self.state.reset_search_cursors()

        if self.config.collection_mode == "backward":
            self.state.clear_meta("since_id")

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

    def _expansions_this_run(self) -> int:
        if self.config.search_only:
            return 0
        remaining = max(0, self.config.api_call_budget - self.client.calls_attempted)
        if remaining < 3:
            return 0
        return min(self.config.max_expansions_per_run, remaining // 3)

    def _collect_search_pages(self) -> int:
        new_posts = 0
        pagination_token = self.state.get_meta("search_pagination_token")
        since_id: str | None = None
        if self.config.collection_mode == "incremental":
            since_id = self.state.get_meta("since_id")

        page_limit = self._search_pages_this_run()
        for page in range(page_limit):
            try:
                payload = self.client.search_posts(
                    self.config.query,
                    mode=self.config.search_mode,
                    max_results=self.config.search_page_size,
                    pagination_token=pagination_token,
                    since_id=since_id,
                    sort_order=self.config.sort_order,
                )
            except ApiBudgetExceeded:
                raise
            except (ApiRateLimitError, ApiFatalError):
                raise
            except RuntimeError as exc:
                logger.error("Search failed on page %s: %s", page + 1, exc)
                raise

            posts = payload.get("data") or []
            includes = payload.get("includes") or {}
            users = {u["id"]: u for u in includes.get("users", [])}
            ref_posts = {p["id"]: p for p in includes.get("tweets", [])}

            for user in users.values():
                self._persist_user(user)

            for post in posts:
                if self.state.mark_post_seen(
                    post["id"],
                    engagement=InteractionGraph.engagement_score(post.get("public_metrics")),
                    created_at=post.get("created_at"),
                ):
                    new_posts += 1
                    self._ingest_post_edges(post, users, ref_posts)

            meta = payload.get("meta") or {}
            newest_id = meta.get("newest_id")
            if newest_id and self.config.collection_mode == "incremental":
                current = self.state.get_meta("since_id")
                if not current or int(newest_id) > int(current):
                    self.state.set_meta("since_id", str(newest_id))

            next_token = meta.get("next_token") or meta.get("pagination_token")
            if not next_token or not posts:
                self.state.set_meta("search_pagination_token", "")
                break
            pagination_token = next_token
            self.state.set_meta("search_pagination_token", next_token)

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