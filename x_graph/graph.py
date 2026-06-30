from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UserNode:
    user_id: str
    username: str = ""
    name: str = ""
    profile_image_url: str = ""


@dataclass
class InteractionEdge:
    source_id: str
    target_id: str
    interaction: str
    weight: int = 1
    post_id: str | None = None


@dataclass
class InteractionGraph:
    nodes: dict[str, UserNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], InteractionEdge] = field(default_factory=dict)

    def upsert_user(self, user: dict[str, Any]) -> None:
        uid = str(user["id"])
        existing = self.nodes.get(uid)
        username = user.get("username") or (existing.username if existing else "")
        name = user.get("name") or (existing.name if existing else "")
        pic = user.get("profile_image_url") or (existing.profile_image_url if existing else "")
        self.nodes[uid] = UserNode(uid, username, name, pic)

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        interaction: str,
        *,
        post_id: str | None = None,
        weight: int = 1,
    ) -> None:
        if source_id == target_id:
            return
        key = (source_id, target_id, interaction)
        if key in self.edges:
            self.edges[key].weight += weight
        else:
            self.edges[key] = InteractionEdge(
                source_id=source_id,
                target_id=target_id,
                interaction=interaction,
                weight=weight,
                post_id=post_id,
            )

    @staticmethod
    def engagement_score(metrics: dict[str, Any] | None) -> int:
        if not metrics:
            return 0
        return (
            int(metrics.get("like_count", 0))
            + 2 * int(metrics.get("retweet_count", 0))
            + 3 * int(metrics.get("quote_count", 0))
            + int(metrics.get("reply_count", 0))
        )