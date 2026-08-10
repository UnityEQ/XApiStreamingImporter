"""edges_added run summary must count interactions recorded this run."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from x_graph.collector import GraphCollector
from x_graph.config import CollectorConfig


class EdgesAddedTelemetryTests(unittest.TestCase):
    def test_persist_edge_increments_edges_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            config = CollectorConfig(
                query="test lang:en",
                work_dir=work,
                dry_run=True,
                search_only=True,
            )
            collector = GraphCollector(config, client=MagicMock())
            collector._edges_added_this_run = 0

            collector._persist_edge("1", "2", "MENTION", post_id="p1")
            collector._persist_edge("1", "2", "RETWEET", post_id="p1")
            # Weight bump on existing key still counts as a recorded event.
            collector._persist_edge("1", "2", "MENTION", post_id="p2")
            # Self-loop ignored.
            collector._persist_edge("1", "1", "MENTION", post_id="p3")

            self.assertEqual(collector._edges_added_this_run, 3)
            stats = collector.state.stats()
            self.assertEqual(stats["edges"], 2)  # unique (source, target, type)

    def test_run_summary_includes_edges_added_from_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            config = CollectorConfig(
                query="test lang:en",
                work_dir=work,
                dry_run=False,
                search_only=True,
                max_search_pages_per_run=1,
                api_call_budget=5,
                max_spend_usd=1.0,
            )
            client = MagicMock()
            client.calls_attempted = 1
            client.calls_made = 1
            client.estimated_spend_usd = 0.01
            client.estimated_posts = 2
            client.estimated_users = 0
            client.adaptive_search_max_results.return_value = 100
            client.search_posts.return_value = {
                "data": [
                    {
                        "id": "10",
                        "author_id": "u1",
                        "text": "hi @bob",
                        "created_at": "2026-08-08T12:00:00.000Z",
                        "entities": {
                            "mentions": [{"id": "u2", "username": "bob"}]
                        },
                        "public_metrics": {"like_count": 0, "retweet_count": 0},
                    },
                    {
                        "id": "11",
                        "author_id": "u3",
                        "text": "rt",
                        "created_at": "2026-08-08T12:01:00.000Z",
                        "referenced_tweets": [{"type": "retweeted", "id": "9"}],
                        "public_metrics": {"like_count": 1, "retweet_count": 0},
                    },
                ],
                "includes": {
                    "users": [
                        {"id": "u1", "username": "alice", "name": "Alice"},
                        {"id": "u2", "username": "bob", "name": "Bob"},
                        {"id": "u3", "username": "cara", "name": "Cara"},
                        {"id": "u9", "username": "orig", "name": "Orig"},
                    ],
                    "tweets": [
                        {"id": "9", "author_id": "u9", "text": "original"},
                    ],
                },
                "meta": {"result_count": 2},
            }

            collector = GraphCollector(config, client=client)
            summary = collector.run_once()

            # MENTION (alice->bob) + RETWEET (cara->orig) + often MENTION on RT path
            self.assertGreater(summary["edges_added"], 0)
            self.assertEqual(summary["edges_added"], summary["edges"])
            self.assertEqual(summary["search_posts_new"], 2)
            self.assertNotEqual(summary["edges_added"], 0)


if __name__ == "__main__":
    unittest.main()
