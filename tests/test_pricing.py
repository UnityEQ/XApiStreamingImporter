"""Unit tests for pay-per-use spend estimates."""

from __future__ import annotations

import unittest

from x_graph.pricing import (
    PricingConfig,
    billing_mode_for_tool,
    count_billable_resources,
    estimate_payload_usd,
)


class CountBillableResourcesTests(unittest.TestCase):
    def test_search_counts_data_posts_only_not_includes(self) -> None:
        payload = {
            "data": [
                {
                    "id": "1",
                    "text": "hello",
                    "author_id": "u1",
                    "public_metrics": {"like_count": 1},
                },
                {
                    "id": "2",
                    "text": "world",
                    "author_id": "u2",
                    "public_metrics": {"like_count": 0},
                },
            ],
            "includes": {
                "users": [
                    {"id": "u1", "username": "a", "name": "A"},
                    {"id": "u2", "username": "b", "name": "B"},
                    {"id": "u3", "username": "c", "name": "C"},
                ],
                "tweets": [
                    {"id": "9", "text": "ref", "author_id": "u9"},
                    {"id": "10", "text": "ref2", "author_id": "u10"},
                ],
            },
        }
        posts, users = count_billable_resources(payload, mode="posts_primary")
        self.assertEqual(posts, 2)
        self.assertEqual(users, 0)

        est = estimate_payload_usd(payload, PricingConfig(), mode="posts_primary")
        self.assertEqual(est["posts"], 2)
        self.assertEqual(est["users"], 0)
        self.assertAlmostEqual(float(est["total_usd"]), 0.01)  # 2 * 0.005

    def test_user_list_counts_data_users_only(self) -> None:
        payload = {
            "data": [
                {"id": "u1", "username": "alice", "name": "Alice"},
                {"id": "u2", "username": "bob", "name": "Bob"},
            ],
            "includes": {
                # Should not be billed on a user-list endpoint.
                "tweets": [{"id": "1", "text": "x", "author_id": "u1"}],
            },
        }
        posts, users = count_billable_resources(payload, mode="users_primary")
        self.assertEqual(posts, 0)
        self.assertEqual(users, 2)

        est = estimate_payload_usd(payload, PricingConfig(), mode="users_primary")
        self.assertEqual(est["users"], 2)
        self.assertAlmostEqual(float(est["total_usd"]), 0.02)  # 2 * 0.01

    def test_single_post_object_in_data(self) -> None:
        payload = {
            "data": {
                "id": "1",
                "text": "solo",
                "author_id": "u1",
            },
            "includes": {
                "users": [{"id": "u1", "username": "a", "name": "A"}],
            },
        }
        posts, users = count_billable_resources(payload, mode="posts_primary")
        self.assertEqual(posts, 1)
        self.assertEqual(users, 0)

    def test_fnaf_style_page_matches_console_post_math(self) -> None:
        """426 posts in data → $2.13 regardless of fat includes."""
        n = 426
        payload = {
            "data": [
                {
                    "id": str(i),
                    "text": f"p{i}",
                    "author_id": f"u{i % 100}",
                    "public_metrics": {},
                }
                for i in range(n)
            ],
            "includes": {
                "users": [
                    {"id": f"u{i}", "username": f"u{i}", "name": f"U{i}"}
                    for i in range(585)
                ],
                "tweets": [
                    {"id": f"r{i}", "text": "ref", "author_id": "x"}
                    for i in range(198)
                ],
            },
        }
        est = estimate_payload_usd(payload, PricingConfig(), mode="posts_primary")
        self.assertEqual(est["posts"], 426)
        self.assertEqual(est["users"], 0)
        self.assertAlmostEqual(float(est["total_usd"]), 2.13)

    def test_tool_billing_modes(self) -> None:
        self.assertEqual(billing_mode_for_tool("search_posts_recent"), "posts_primary")
        self.assertEqual(billing_mode_for_tool("search_posts_all"), "posts_primary")
        self.assertEqual(billing_mode_for_tool("get_posts_liking_users"), "users_primary")
        self.assertEqual(billing_mode_for_tool("get_posts_reposted_by"), "users_primary")
        self.assertEqual(billing_mode_for_tool("get_posts_quoted_posts"), "posts_primary")


class SearchCeilingTests(unittest.TestCase):
    def test_search_ceiling_is_posts_only(self) -> None:
        p = PricingConfig()
        # Old model was $2.00 for 100 (posts+includes tweets+users).
        self.assertAlmostEqual(p.estimate_search_page_ceiling(100), 0.50)
        self.assertAlmostEqual(p.estimate_user_list_ceiling(100), 1.00)

    def test_max_results_for_budget_uses_post_ceiling(self) -> None:
        p = PricingConfig()
        # $1.00 buys two full pages of 100 at $0.50, or 200 posts — capped at 100.
        n = p.max_search_results_for_budget(1.00, max_results=100)
        self.assertEqual(n, 100)
        # $0.05 buys exactly 10 posts at $0.005.
        n = p.max_search_results_for_budget(0.05, max_results=100)
        self.assertEqual(n, 10)
        # Below min page cost → 0.
        n = p.max_search_results_for_budget(0.04, max_results=100)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
