from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from x_graph.archive_window import crawl_has_more
from x_graph.collector import GraphCollector
from x_graph.config import CollectorConfig
from x_graph.enrich import enrich_nodes
from x_graph.export import export_graph
from x_graph.paths import (
    default_work_dir,
    normalize_query,
    query_slug,
    user_interaction_query,
)
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
    collect.add_argument(
        "--query",
        "-q",
        default=None,
        help='Search query, e.g. \'"digital circus" lang:en\' (or use --user)',
    )
    collect.add_argument(
        "--user",
        "-u",
        default=None,
        help=(
            "Ego-graph mode: build query from:USER OR to:USER OR @USER "
            "(all interactions involving this account). Cheapest with --search-only."
        ),
    )
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
        "--lookback-days",
        type=int,
        default=365,
        help="Full-archive only: how far back to crawl (default 365). Recent mode is capped at 7 days by X.",
    )
    collect.add_argument(
        "--archive-chunk-days",
        type=int,
        default=30,
        help="Full-archive only: days per time window when auto-expanding sparse pages",
    )
    collect.add_argument(
        "--no-auto-expand-archive",
        action="store_true",
        help="Disable stepping to older date windows when a search page is not full",
    )
    collect.add_argument(
        "--api-budget",
        type=int,
        default=None,
        help=(
            "Max HTTP attempts this run (failed calls still cost credits). "
            "Optional when --max-spend is set (defaults to a high safety ceiling). "
            "Default without --max-spend: 30."
        ),
    )
    collect.add_argument(
        "--max-spend",
        type=float,
        default=None,
        metavar="USD",
        help=(
            "Primary spend control: stop this run near this many estimated dollars "
            "(posts/users returned × rates; not the official X bill). "
            "You usually do not need --api-budget with this. "
            "Pages until the $ cap, crawl end, or HTTP safety ceiling."
        ),
    )
    collect.add_argument(
        "--price-post",
        type=float,
        default=0.005,
        metavar="USD",
        help="Estimated $ per post resource read (default 0.005; override if X changes rates)",
    )
    collect.add_argument(
        "--price-user",
        type=float,
        default=0.01,
        metavar="USD",
        help="Estimated $ per user resource read (default 0.01; override if X changes rates)",
    )
    collect.add_argument(
        "--search-pages",
        type=int,
        default=None,
        help=(
            "Search pages per run (100 posts/page). Default: 1, or equal to "
            "--api-budget when --max-spend is set"
        ),
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
    collect.add_argument(
        "--loop",
        action="store_true",
        help="Repeat until Ctrl+C (backward paging by default; pair with --incremental for new-post monitoring)",
    )
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

    enrich = sub.add_parser(
        "enrich",
        help="Add primary_interaction to nodes CSV for Gephi coloring (no API calls)",
    )
    enrich.add_argument("--work-dir", default=None)
    enrich.add_argument("--query", "-q", default=None, help="Resolve work dir from query slug")
    enrich.add_argument(
        "--output",
        default=None,
        help="Output path (default: <work-dir>/output/x_graph_nodes_enriched.csv)",
    )
    enrich.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite x_graph_nodes.csv instead of writing a new file",
    )

    return parser


def _resolve_work_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "work_dir", None):
        return Path(args.work_dir)
    if getattr(args, "query", None):
        return default_work_dir(args.query)
    if getattr(args, "user", None):
        return default_work_dir(user_interaction_query(args.user))
    raise SystemExit("Provide --query, --user, or --work-dir")


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
    if args.command == "enrich":
        return _cmd_enrich(args)
    return 1


def _cmd_collect(args: argparse.Namespace) -> int:
    if not args.query and not args.user:
        print("Provide --query / -q or --user / -u")
        return 1
    if args.query and args.user:
        print("Use either --query or --user, not both")
        return 1

    if args.user:
        try:
            raw_query = user_interaction_query(args.user)
        except ValueError as exc:
            print(f"Invalid --user: {exc}")
            return 1
        handle = args.user.strip().lstrip("@")
        logging.info("Ego-graph query for @%s: %s", handle, raw_query)
        # Prefer a short stable folder: data/queries/user-<handle>/
        if not args.work_dir:
            args.work_dir = str(Path("data/queries") / f"user-{query_slug(handle)}")
    else:
        raw_query = args.query

    # When only -q is given, path resolves from the query slug.
    if not args.work_dir and args.query:
        pass  # _resolve_work_dir uses query
    elif args.query is None:
        args.query = raw_query  # status-friendly; path already set for --user
    work_dir = _resolve_work_dir(args)
    mode = "incremental" if args.incremental else "backward"
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
            "  Live run: python -m x_graph.cli collect -q '...' --confirm-spend --search-only --api-budget 10\n"
            "  $ cap:    python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --max-spend 1.00\n"
            "  User ego: python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --api-budget 10"
        )
        return 1

    query = normalize_query(raw_query)
    if query != raw_query.strip():
        logging.warning("Normalized query: %r → %r", raw_query, query)

    lookback_days = args.lookback_days
    if args.search_mode == "recent" and lookback_days > 7:
        logging.warning(
            "Recent search is capped at 7 days by the X API (requested lookback=%s). "
            "Use --search-mode all with a bearer token for older history.",
            lookback_days,
        )
        lookback_days = 7

    if args.max_spend is not None and args.max_spend <= 0:
        print("--max-spend must be a positive dollar amount (e.g. 1.00)")
        return 1

    # Spend-first mode: --max-spend is the real brake; HTTP budget is only a
    # safety ceiling (so a runaway loop cannot hammer the API forever).
    if args.api_budget is None:
        api_budget = 500 if args.max_spend is not None else 30
    else:
        api_budget = args.api_budget

    # With a dollar cap, keep paging until $ or crawl end (not one page per run).
    if args.search_pages is None:
        search_pages = api_budget if args.max_spend is not None else 1
    else:
        search_pages = args.search_pages

    config = CollectorConfig(
        query=query,
        work_dir=work_dir,
        collection_mode=mode,
        fresh=args.fresh,
        search_mode=args.search_mode,
        search_only=args.search_only,
        dry_run=dry_run,
        api_call_budget=api_budget,
        max_spend_usd=args.max_spend,
        post_read_usd=args.price_post,
        user_read_usd=args.price_user,
        max_search_pages_per_run=search_pages,
        max_expansions_per_run=0 if args.search_only else args.expansions,
        min_engagement_to_expand=args.min_engagement,
        auto_expand_archive=(
            args.search_mode == "all" and not args.no_auto_expand_archive
        ),
        archive_chunk_days=max(1, args.archive_chunk_days),
        lookback_days=lookback_days if args.search_mode == "all" else 7,
    )
    collector = GraphCollector(config)

    logging.info("Query slug: %s", query_slug(query))
    logging.info("Work dir: %s | mode: %s", work_dir, mode)
    if args.max_spend is not None:
        logging.info(
            "Spend cap: ~$%.2f estimated (post=$%.4f user=$%.4f) | "
            "HTTP safety ceiling: %s | max search pages: %s",
            args.max_spend,
            args.price_post,
            args.price_user,
            api_budget,
            search_pages,
        )
    else:
        logging.info(
            "HTTP budget: %s calls | search pages: %s "
            "(no --max-spend; watch console.x.com for real $)",
            api_budget,
            search_pages,
        )

    def one_run() -> dict:
        summary = collector.run_once()
        print(json.dumps(summary, indent=2), flush=True)
        if args.export_after:
            paths = export_graph(collector.state, config.output_dir)
            print("Exported:", {k: str(v) for k, v in paths.items()}, flush=True)
        return summary

    if args.loop:
        pass_num = 0
        while True:
            pass_num += 1
            logging.info("Loop pass %s starting…", pass_num)
            summary = one_run()
            has_more = summary.get("has_more_older_posts", False)
            new_posts = summary.get("search_posts_new", 0)
            reason = summary.get("stopped_reason")
            if reason in {"max_spend_reached", "api_budget_exhausted"}:
                logging.info(
                    "Loop stopping: %s (est. spend this run $%s, total $%s)",
                    reason,
                    summary.get("estimated_spend_usd"),
                    summary.get("estimated_spend_total_usd"),
                )
                break
            if mode == "backward":
                if not has_more:
                    if config.search_mode == "recent":
                        logging.info(
                            "Backward crawl finished: %s posts in the last 7 days. "
                            "The X API cannot return older posts in recent mode. "
                            "Use --search-mode all with a bearer token for full history.",
                            summary.get("seen_posts", 0),
                        )
                    else:
                        logging.info(
                            "Backward crawl finished: %s posts total within the "
                            "%s-day lookback window.",
                            summary.get("seen_posts", 0),
                            config.lookback_days,
                        )
                    break
                logging.info(
                    "Pass %s done: %s new posts. More older posts available — "
                    "sleeping %.1f min (Ctrl+C to stop).",
                    pass_num,
                    new_posts,
                    max(args.sleep_minutes, 0.1),
                )
            else:
                logging.info(
                    "Pass %s done: %s new posts — sleeping %.1f min (Ctrl+C to stop).",
                    pass_num,
                    new_posts,
                    max(args.sleep_minutes, 0.1),
                )
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
    search_mode = state.get_meta("search_mode") or "recent"
    info = {
        "work_dir": str(work_dir),
        "query_slug": query_slug(args.query) if args.query else None,
        "stats": state.stats(),
        "query": state.get_meta("query"),
        "collection_mode": state.get_meta("collection_mode"),
        "search_mode": search_mode,
        "since_id": state.get_meta("since_id"),
        "search_pagination_token": state.get_meta("search_pagination_token"),
        "search_exhausted": state.get_meta("search_exhausted") == "1",
        "estimated_spend_total_usd": state.get_meta("estimated_spend_total_usd"),
        "archive_window_start": state.get_meta("archive_window_start"),
        "archive_window_end": state.get_meta("archive_window_end"),
        "has_more_older_posts": crawl_has_more(
            state,
            search_mode=search_mode,
            auto_expand_archive=search_mode == "all",
            lookback_days=365,
        ),
    }
    print(json.dumps(info, indent=2))
    return 0


def _cmd_enrich(args: argparse.Namespace) -> int:
    work_dir = _resolve_work_dir(args)
    output_dir = work_dir / "output"
    nodes_path = output_dir / "x_graph_nodes.csv"
    edges_path = output_dir / "x_graph_edges.csv"
    output_path = Path(args.output) if args.output else output_dir / "x_graph_nodes_enriched.csv"

    stats = enrich_nodes(
        nodes_path,
        edges_path,
        output_path,
        in_place=args.in_place,
    )
    dest = nodes_path if args.in_place else output_path
    print(
        json.dumps(
            {
                "work_dir": str(work_dir),
                "nodes_file": str(nodes_path),
                "edges_file": str(edges_path),
                "output": str(dest),
                **stats,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())