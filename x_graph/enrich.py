from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


def dominant_interactions(edges_path: Path) -> dict[str, str]:
    """Most common outgoing Interaction per source node."""
    counts: dict[str, Counter[str]] = {}
    with edges_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            src = row.get("Source", "").strip()
            interaction = row.get("Interaction", "").strip()
            if not src or not interaction:
                continue
            counts.setdefault(src, Counter())[interaction] += 1
    return {uid: c.most_common(1)[0][0] for uid, c in counts.items()}


def enrich_nodes(
    nodes_path: Path,
    edges_path: Path,
    output_path: Path,
    *,
    in_place: bool = False,
) -> dict[str, int]:
    """Add primary_interaction column to nodes CSV for Gephi node coloring."""
    if not nodes_path.is_file():
        raise FileNotFoundError(f"Nodes file not found: {nodes_path}")
    if not edges_path.is_file():
        raise FileNotFoundError(f"Edges file not found: {edges_path}")

    primary = dominant_interactions(edges_path)

    with nodes_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"No rows in {nodes_path}")

    fieldnames = list(rows[0].keys())
    if "primary_interaction" not in fieldnames:
        fieldnames.append("primary_interaction")

    matched = 0
    for row in rows:
        uid = row.get("Id", "").strip()
        value = primary.get(uid, "none")
        row["primary_interaction"] = value
        if value != "none":
            matched += 1

    dest = nodes_path if in_place else output_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "nodes": len(rows),
        "with_primary_interaction": matched,
        "interaction_types": len({v for v in primary.values()}),
    }