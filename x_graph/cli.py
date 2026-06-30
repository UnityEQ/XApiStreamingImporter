from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from x_graph.collector import GraphCollector
from x_graph.config import CollectorConfig
from x_graph.export import export_graph
from x_graph.paths import default_work_dir, query_slug
from x_graph.state import StateStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build X interaction graphs for Gephi (via xapi / xurl)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser(
        "collect",
        help="Collect posts (default: newest→older per query, separate folder per topic)",
    )
    collect.add_argument("--query", "-q", required=True, help='Search query, e.g. \'"digital circus" lang:en\'')
    collect.add_argument(
        "--work-dir",
        default=None,
        help="Override data dir (default: data/queries/<query-slug>/)",
    )
    collect.add_argument(
        "--search-mode",
        choices=["recent", "all"],
        default="recent",
        help="recent=7-day (OAuth); all=full archive (app-only)",
    )
    collect.add_argument(
        "--api-budget",
        type=int,
        default=30,
        help="Max HTTP attempts per run (failed calls still cost credits)",
    )
    collect.add_argument(
        "--search-pages",
        type=int,
        default=3,
        help="Search pages per run (100 posts/page, goes older each page)",
    )
    collect.add_argument(
        "--expansions",
        type=int,
        default=5,
        help="High-engagement posts to expand per run (likers/RTs/quotes)",
    )
    collect.add_argument(
        "--search-only",
        action="store_true",
        help="Skip expansions entirely (cheapest — inline edges only)",
    )
    collect.add_argument(
        "--min-engagement",
        type=int,
        default=25,
        help="Min engagement score to queue expansion",
    )
    collect.add_argument(
        "--fresh",
        action="store_true",
        help="Restart this query from the most recent posts (clears search cursor)",
    )
    collect.add_argument(
        "--incremental",
        action="store_true",
        help="Only fetch NEW posts since last run (for monitoring). Default is backward.",
    )
    collect.add_argument("--loop", action="store_true", help="Run continuously with sleep")
    collect.add_argument("--sleep-minutes", type=float, default=15.0, help="Sleep between loop runs")
    collect.add_argument("--export-after", action="store_true", help="Export GEXF/CSV after each run")
    collect.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — zero X API calls (also set by X_GRAPH_OFFLINE=1)",
    )
    collect.add_argument(
        "--confirm-spend",
        action="store_true",
        help="Required for live X API calls (each attempt costs credits)",
    )

    export = sub.add_parser("export", help="Export current graph to GEXF and CSV")
    export.add_argument("--work-dir", default=None)
    export.add_argument("--query", "-q", default=None, help="Resolve work dir from query slug")
    export.add_argument("--basename", default="x_graph")
    export.add_argument("--format", default="gexf,csv", help="Comma-separated: gexf,csv")

    stats = sub.add_parser("status", help="Show collection stats")
    stats.add_argument("--work-dir", default=None)
    stats.add_argument("--query", "-q", default=None, help="Resolve work dir from query slug")

    return parser


def _resolve_work_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "work_dir", None):
        return Path(args.work_dir)
    if getattr(args, "query", None):
        return default_work_dir(args.query)
    raise SystemExit("Provide --query or --work-dir")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _build_parser().parse_args(argv)

    if args.command == "collect":
        return _cmd_collect(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "status":
        return _cmd_status(args)
    return 1


def _cmd_collect(args: argparse.Namespace) -> int:
    work_dir = _resolve_work_dir(args)
    mode = "incremental" if args.incremental or args.loop else "backward"
    dry_run = args.dry_run or os.environ.get("X_GRAPH_OFFLINE", "").lower() in {
        "1",
        "true",
        "yes",
    }

    if not dry_run and not args.confirm_spend:
        print(
            "Refusing live X API calls without --confirm-spend.\n"
            "Each HTTP attempt costs credits on pay-per-use, including failures.\n"
            "  Dry run:  python -m x_graph.cli collect -q '...' --dry-run\n"
            "  Live run: python -m x_graph.cli collect -q '...' --confirm-spend --search-only --api-budget 10"
        )
        return 1

    config = CollectorConfig(
        query=args.query,
        work_dir=work_dir,
        collection_mode=mode,
        fresh=args.fresh,
        search_mode=args.search_mode,
        search_only=args.search_only,
        dry_run=dry_run,
        api_call_budget=args.api_budget,
        max_search_pages_per_run=args.search_pages,
        max_expansions_per_run=0 if args.search_only else args.expansions,
        min_engagement_to_expand=args.min_engagement,
    )
    collector = GraphCollector(config)

    logging.info("Query slug: %s", query_slug(args.query))
    logging.info("Work dir: %s | mode: %s", work_dir, mode)

    def one_run() -> dict:
        summary = collector.run_once()
        print(json.dumps(summary, indent=2))
        if args.export_after:
            paths = export_graph(collector.state, config.output_dir)
            print("Exported:", {k: str(v) for k, v in paths.items()})
        return summary

    if args.loop:
        while True:
            one_run()
            time.sleep(max(args.sleep_minutes, 0.1) * 60)
    else:
        one_run()
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    work_dir = _resolve_work_dir(args)
    state = StateStore(work_dir / "state.db")
    formats = tuple(f.strip() for f in args.format.split(",") if f.strip())
    paths = export_graph(state, work_dir / "output", basename=args.basename, formats=formats)
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    work_dir = _resolve_work_dir(args)
    state = StateStore(work_dir / "state.db")
    info = {
        "work_dir": str(work_dir),
        "query_slug": query_slug(args.query) if args.query else None,
        "stats": state.stats(),
        "query": state.get_meta("query"),
        "collection_mode": state.get_meta("collection_mode"),
        "since_id": state.get_meta("since_id"),
        "search_pagination_token": state.get_meta("search_pagination_token"),
        "has_more_older_posts": bool(state.get_meta("search_pagination_token")),
    }
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())