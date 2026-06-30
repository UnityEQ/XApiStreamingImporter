from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from x_graph.collector import GraphCollector
from x_graph.config import CollectorConfig
from x_graph.export import export_graph
from x_graph.state import StateStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build incremental X interaction graphs for Gephi (via xapi / xurl)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Run one collection pass (safe to repeat)")
    collect.add_argument("--query", "-q", required=True, help='Search query, e.g. "AI OR grok lang:en"')
    collect.add_argument("--work-dir", default="data", help="State/output directory")
    collect.add_argument(
        "--search-mode",
        choices=["recent", "all"],
        default="recent",
        help="recent=7-day (OAuth); all=full archive (app-only)",
    )
    collect.add_argument("--api-budget", type=int, default=200, help="Max API calls per run")
    collect.add_argument("--search-pages", type=int, default=5, help="Search pages per run")
    collect.add_argument("--expansions", type=int, default=50, help="Post expansions per run")
    collect.add_argument("--min-engagement", type=int, default=10, help="Min score to expand")
    collect.add_argument("--loop", action="store_true", help="Run continuously with sleep")
    collect.add_argument("--sleep-minutes", type=float, default=15.0, help="Sleep between loop runs")
    collect.add_argument("--export-after", action="store_true", help="Export GEXF/CSV after each run")

    export = sub.add_parser("export", help="Export current graph to GEXF and CSV")
    export.add_argument("--work-dir", default="data")
    export.add_argument("--basename", default="x_graph")
    export.add_argument("--format", default="gexf,csv", help="Comma-separated: gexf,csv")

    stats = sub.add_parser("status", help="Show collection stats")
    stats.add_argument("--work-dir", default="data")

    return parser


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
    config = CollectorConfig(
        query=args.query,
        work_dir=Path(args.work_dir),
        search_mode=args.search_mode,
        api_call_budget=args.api_budget,
        max_search_pages_per_run=args.search_pages,
        max_expansions_per_run=args.expansions,
        min_engagement_to_expand=args.min_engagement,
    )
    collector = GraphCollector(config)

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
    work_dir = Path(args.work_dir)
    state = StateStore(work_dir / "state.db")
    formats = tuple(f.strip() for f in args.format.split(",") if f.strip())
    paths = export_graph(state, work_dir / "output", basename=args.basename, formats=formats)
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir)
    state = StateStore(work_dir / "state.db")
    info = {
        "stats": state.stats(),
        "since_id": state.get_meta("since_id"),
        "search_pagination_token": state.get_meta("search_pagination_token"),
        "query": state.get_meta("query"),
    }
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())