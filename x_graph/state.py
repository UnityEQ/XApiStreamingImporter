from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from x_graph.graph import InteractionEdge, InteractionGraph, UserNode


class StateStore:
    """SQLite-backed incremental state for long-running collection."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seen_posts (
                    post_id TEXT PRIMARY KEY,
                    engagement INTEGER DEFAULT 0,
                    expanded INTEGER DEFAULT 0,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS expansion_queue (
                    post_id TEXT PRIMARY KEY,
                    author_id TEXT NOT NULL,
                    engagement INTEGER NOT NULL,
                    priority REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    user_id TEXT PRIMARY KEY,
                    username TEXT,
                    name TEXT,
                    profile_image_url TEXT
                );
                CREATE TABLE IF NOT EXISTS edges (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    interaction TEXT NOT NULL,
                    weight INTEGER NOT NULL DEFAULT 1,
                    post_id TEXT,
                    PRIMARY KEY (source_id, target_id, interaction)
                );
                CREATE TABLE IF NOT EXISTS run_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT CURRENT_TIMESTAMP,
                    event TEXT NOT NULL,
                    detail TEXT
                );
                """
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def log_event(self, event: str, detail: dict[str, Any] | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO run_log(event, detail) VALUES(?, ?)",
                (event, json.dumps(detail or {}, ensure_ascii=False)),
            )

    def mark_post_seen(
        self,
        post_id: str,
        *,
        engagement: int = 0,
        expanded: bool = False,
        created_at: str | None = None,
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen_posts(post_id, engagement, expanded, created_at) "
                "VALUES(?, ?, ?, ?)",
                (post_id, engagement, int(expanded), created_at),
            )
            return cur.rowcount > 0

    def is_post_seen(self, post_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_posts WHERE post_id = ?", (post_id,)
            ).fetchone()
            return row is not None

    def is_post_expanded(self, post_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT expanded FROM seen_posts WHERE post_id = ?", (post_id,)
            ).fetchone()
            return bool(row and row["expanded"])

    def mark_post_expanded(self, post_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE seen_posts SET expanded = 1 WHERE post_id = ?", (post_id,)
            )
            conn.execute("DELETE FROM expansion_queue WHERE post_id = ?", (post_id,))

    def enqueue_expansion(self, post_id: str, author_id: str, engagement: int, priority: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO expansion_queue(post_id, author_id, engagement, priority) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(post_id) DO UPDATE SET "
                "engagement = MAX(expansion_queue.engagement, excluded.engagement), "
                "priority = MAX(expansion_queue.priority, excluded.priority)",
                (post_id, author_id, engagement, priority),
            )

    def pop_expansion_batch(self, limit: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT post_id, author_id, engagement, priority "
                "FROM expansion_queue ORDER BY priority DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_node(self, node: UserNode) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO nodes(user_id, username, name, profile_image_url) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "username = COALESCE(NULLIF(excluded.username, ''), nodes.username), "
                "name = COALESCE(NULLIF(excluded.name, ''), nodes.name), "
                "profile_image_url = COALESCE(NULLIF(excluded.profile_image_url, ''), nodes.profile_image_url)",
                (node.user_id, node.username, node.name, node.profile_image_url),
            )

    def add_edge(self, edge: InteractionEdge) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO edges(source_id, target_id, interaction, weight, post_id) "
                "VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, target_id, interaction) DO UPDATE SET "
                "weight = edges.weight + excluded.weight",
                (
                    edge.source_id,
                    edge.target_id,
                    edge.interaction,
                    edge.weight,
                    edge.post_id,
                ),
            )

    def load_graph(self) -> InteractionGraph:
        graph = InteractionGraph()
        with self._conn() as conn:
            for row in conn.execute(
                "SELECT user_id, username, name, profile_image_url FROM nodes"
            ):
                graph.nodes[row["user_id"]] = UserNode(
                    row["user_id"], row["username"] or "", row["name"] or "", row["profile_image_url"] or ""
                )
            for row in conn.execute(
                "SELECT source_id, target_id, interaction, weight, post_id FROM edges"
            ):
                key = (row["source_id"], row["target_id"], row["interaction"])
                graph.edges[key] = InteractionEdge(
                    row["source_id"],
                    row["target_id"],
                    row["interaction"],
                    row["weight"],
                    row["post_id"],
                )
        return graph

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            return {
                "nodes": conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"],
                "edges": conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"],
                "seen_posts": conn.execute("SELECT COUNT(*) c FROM seen_posts").fetchone()["c"],
                "expanded_posts": conn.execute(
                    "SELECT COUNT(*) c FROM seen_posts WHERE expanded = 1"
                ).fetchone()["c"],
                "queued_expansions": conn.execute(
                    "SELECT COUNT(*) c FROM expansion_queue"
                ).fetchone()["c"],
            }