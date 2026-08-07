# gephi-x-mcp

Turn X (Twitter) search results into **directed, weighted interaction graphs** for [Gephi](https://gephi.org/).

| | |
|---|---|
| **Nodes** | Users — `username`, display name, profile image |
| **Edges** | `MENTION` · `REPLY` · `RETWEET` · `QUOTE` · `LIKE` (weighted) |
| **Gephi** | GEXF + CSV export; `enrich` adds `primary_interaction` for node coloring |
| **Transport** | [`xurl`](https://github.com/xdevplatform/xurl) — same OAuth bridge as the [xapi MCP server](https://docs.x.com/tools/mcp) |

Built for researchers who jump between topics: **one folder per query**, backward crawls from recent posts, and hard guards so a bad run cannot silently burn through pay-per-use credits.

## Quick start

After [setup](#setup-one-time), from the repo root:

### Topic search (keyword / phrase)

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --search-only --api-budget 10 --export-after
```

### User ego graph (all interactions involving one account)

For a force-directed graph of **everything involving one person** (their posts + replies to them + mentions of them), use `--user`:

```bash
python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --api-budget 10 --export-after
```

That expands to the search query:

```text
from:eqbrowser OR to:eqbrowser OR @eqbrowser
```

| Operator | Captures |
|----------|----------|
| `from:user` | Posts they write (outbound mentions, replies, RTs, quotes) |
| `to:user` | Replies directed at them |
| `@user` | Posts that mention them |

**Stay on `--search-only`** for max nodes/edges per credit: each search page returns up to ~100 posts and inline edges are free. Expansions (likers) cost extra API calls for `LIKE` edges only.

Either path will:

1. Search posts matching the query (newest first)
2. Build mention / reply / RT / quote edges from the results (no extra API calls)
3. Save state under `data/queries/<slug>/`
4. Export `x_graph.gexf` + CSV when you pass `--export-after`
5. Enrich nodes for Gephi coloring (free, no API):

```bash
python -m x_graph.cli enrich -q "digital circus lang:en"
# or: python -m x_graph.cli enrich -q "from:eqbrowser OR to:eqbrowser OR @eqbrowser"
# → output/x_graph_nodes_enriched.csv (adds primary_interaction per user)
```

Re-run the **same command** to page backward in time. Check progress for free:

```bash
python -m x_graph.cli status -q "digital circus lang:en"
```

---

## How it works

```
X search query
    → fetch recent posts (newest first, page backward on re-runs)
    → extract inline edges from post payloads        ← free, no extra API calls
    → optionally expand high-engagement posts        ← likers / reposters / quoters (capped)
    → SQLite dedupe + resume
    → export GEXF + CSV
    → enrich nodes (optional)        ← add primary_interaction for Gephi node colors
```

**Default mode is backward crawl** — each run starts at the most recent matching posts and pages older. Re-run the same query to keep going back in time (last 7 days with `--search-mode recent`). Each run fetches **one search page by default** (~100 posts); use `--search-pages 3` to go deeper in a single run (more API calls, higher rate-limit risk).

### Edge types: free vs paid

| Edge | With `--search-only` | Needs expansion (extra API) |
|------|----------------------|---------------------------|
| `MENTION` | Yes — `@user` in post text | — |
| `REPLY` | Yes — if the post is a reply | — |
| `RETWEET` | Yes — if the post is a retweet | Also: likers-of-post style reposter lists |
| `QUOTE` | Yes — if the post is a quote tweet | Also: separate quoter lists |
| `LIKE` | No | Yes — likers endpoint |

`--search-only` still captures RT/REPLY/QUOTE when those posts appear in search results. It skips **expansion** calls that fetch who liked / reposted / quoted a high-engagement tweet.

---

## Prerequisites

- **Python 3.10+** (stdlib only — no `pip install` needed)
- **Node.js** — for `npx @xdevplatform/xurl`
- **X Developer account** — [developer.x.com](https://developer.x.com), Pay-per-use / Production
- **Gephi 0.11+** (optional) — for visualization

---

## Setup (one time)

### 1. X Developer app

1. Create an app at [developer.x.com](https://developer.x.com).
2. Enable **OAuth 2.0** with scopes: `tweet.read`, `users.read`, `like.read`, `offline.access`.
3. Add redirect URI: `http://localhost:8080/callback`
4. Save `CLIENT_ID` and `CLIENT_SECRET` locally — **never commit them**.

### 2. Authenticate xurl

```bash
npx -y @xdevplatform/xurl auth oauth2
```

Tokens cache in `~/.xurl` on your machine. Verify:

```bash
npx -y @xdevplatform/xurl /2/users/me
```

### 3. Clone

```bash
git clone <your-repo-url>
cd gephi-x-mcp
```

---

## Example commands (copy-paste)

Run from the repo root (`gephi-x-mcp/`).  
**Live `collect` always needs `--confirm-spend`** — without it the CLI refuses to call the API.

### Free — no API credits

```bash
python -m x_graph.cli status -q "digital circus lang:en"

python -m x_graph.cli collect -q "digital circus lang:en" --dry-run

python -m x_graph.cli export -q "digital circus lang:en"

python -m x_graph.cli enrich -q "digital circus lang:en"
```

### Enrich nodes for Gephi colors (free)

Adds `primary_interaction` to nodes — each user's most common **outgoing** edge type (`MENTION`, `REPLY`, etc.). Use this so nodes can be colored by interaction in Gephi (edges already have `Interaction`; nodes do not until enriched). On fan/search topics, this often skews to `MENTION` (one RT post can still add several `@mention` edges). Users with only incoming edges get `none`. For RT/REPLY/QUOTE visibility, color **edges** by `interaction`, not nodes only.

```bash
python -m x_graph.cli enrich -q "digital circus lang:en"
# → output/x_graph_nodes_enriched.csv

python -m x_graph.cli enrich -q "digital circus lang:en" --in-place
# → overwrites output/x_graph_nodes.csv
```

### Cheapest live collect (recommended)

Posts + inline edges only (`MENTION`, `REPLY`, `RETWEET`, `QUOTE`). No liker/RT/quote expansion calls.

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --search-only --api-budget 10 --export-after
```

### User ego graph (recommended recipe)

```bash
# Dollar-capped one-shot (recommended) — no --api-budget needed
python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --max-spend 1.00 --export-after

# Re-run same command later: resumes older posts (does not re-fetch the old pages)
python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --max-spend 1.00 --export-after

# Full history (costs more; needs app-only bearer token)
python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --search-mode all --max-spend 2.00 --export-after
```

### How spending works (read this)

X pay-per-use bills **per resource returned**, not a flat fee per HTTP request:

| Resource | Pilot rate (override with `--price-post` / `--price-user` if X changes it) |
|----------|----------------------------------------|
| Post read | ~$0.005 each |
| User read | ~$0.01 each |

One search page of ~100 posts (plus included users / referenced tweets) often lands roughly **$0.50–$2** depending on includes. Check [console.x.com](https://console.x.com) for your real rates and balance.

| Flag | What it actually limits |
|------|-------------------------|
| `--api-budget N` | Max **HTTP attempts** this run (retries count). Hard stop. |
| `--max-spend D` | Stop when **estimated** $ from posts/users returned hits `D`. Soft planning cap — **not** X's official bill. |
| `--search-pages N` | How many search pages to try. With `--max-spend`, defaults to `--api-budget` so you don't re-run by hand. |

**You do not have to re-run forever.** Prefer a dollar cap:

```bash
python -m x_graph.cli collect -u eqbrowser --confirm-spend --search-only --max-spend 1.00 --export-after
```

That single command pages backward until the crawl is done, the HTTP budget is hit, or estimated spend reaches ~$1. The summary prints `estimated_spend_usd` and cumulative `estimated_spend_total_usd` (also free via `status`). Always confirm real charges on X's console — estimates can under/over-count if pricing or billable includes change.

**Token density tips for ego graphs**

| Do | Avoid |
|----|--------|
| `--search-only` | Expansions unless you need `LIKE` edges |
| `--search-pages 1` and re-run | Huge single-run page budgets on sparse archive windows |
| `--search-mode recent` first | Jumping straight to `all` unless you need history |
| Re-run only while `has_more_older_posts: true` | Blind re-runs after the crawl is exhausted |
| Prefer `@user OR from:user OR to:user` (via `-u`) | Bare `@user` alone (misses their outbound posts) |

Completed crawls now stop cleanly (`stopped_reason: search_exhausted`) instead of re-fetching the same newest page. Use `--fresh` to restart from newest, or `--incremental` to watch for new posts only.

### Same topic — keep paging backward

Re-run to go older. Data lives in `data/queries/digital-circus-lang-en/`.

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --search-only --api-budget 10 --export-after

python -m x_graph.cli status -q "digital circus lang:en"
```

`has_more_older_posts: true` in status → run `collect` again.

### New topic — separate graph folder

```bash
python -m x_graph.cli collect -q "bbbyq lang:en" --confirm-spend --search-only --api-budget 10 --export-after
```

### With expansions (costs more)

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --api-budget 20 --expansions 3 --export-after
```

### Restart cursor from newest posts (keeps graph)

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --fresh --search-only --export-after
```

### Live monitoring loop (new posts only)

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --incremental --loop --sleep-minutes 15 --search-only --export-after
```

### Exact phrase (PowerShell quoting)

```powershell
python -m x_graph.cli collect -q '"digital circus" lang:en' --confirm-spend --search-only --api-budget 10 --export-after
```

### Exact phrase (bash / macOS / Linux)

```bash
python -m x_graph.cli collect -q '"digital circus" lang:en' --confirm-spend --search-only --api-budget 10 --export-after
```

---

## Python API (optional)

Use from a script or notebook instead of the CLI:

```python
from pathlib import Path
from x_graph.collector import GraphCollector
from x_graph.config import CollectorConfig
from x_graph.export import export_graph
from x_graph.paths import default_work_dir

query = "digital circus lang:en"
config = CollectorConfig(
    query=query,
    work_dir=default_work_dir(query),   # data/queries/digital-circus-lang-en/
    search_only=True,                   # cheapest — no expansion API calls
    api_call_budget=10,
    max_search_pages_per_run=1,
)

collector = GraphCollector(config)
summary = collector.run_once()
print(summary)

export_graph(collector.state, config.output_dir)
# → data/queries/digital-circus-lang-en/output/x_graph.gexf
```

Dry-run equivalent (zero API calls):

```python
config = CollectorConfig(query=query, work_dir=default_work_dir(query), dry_run=True)
summary = GraphCollector(config).run_once()
```

From an MCP host (Cursor / Grok with xapi connected):

```python
from x_graph.mcp_adapter import collect_with_mcp

summary = collect_with_mcp(
    "digital circus lang:en",
    call_mcp_tool=your_mcp_call_function,
    work_dir="data/queries/digital-circus-lang-en",
    api_budget=10,
    search_pages=1,
    expansions=0,
)
```

Enrich nodes after export (no API):

```python
from pathlib import Path
from x_graph.enrich import enrich_nodes
from x_graph.paths import default_work_dir

work = default_work_dir("digital circus lang:en")
out = work / "output"

enrich_nodes(
    out / "x_graph_nodes.csv",
    out / "x_graph_edges.csv",
    out / "x_graph_nodes_enriched.csv",
)
```

---

## CLI reference

### `collect`

| Flag | Default | Description |
|------|---------|-------------|
| `-q`, `--query` | — | X search query (required unless `--user`) |
| `-u`, `--user` | — | Ego graph: expands to `from:U OR to:U OR @U` |
| `--confirm-spend` | off | **Required** for any live API call |
| `--dry-run` | off | Plan only — zero API calls |
| `--search-only` | off | Skip expansions (cheapest live mode) |
| `--max-spend` | off | **Primary $ control** — stop near this many estimated dollars |
| `--api-budget` | `30` / `500`* | HTTP safety ceiling (*`500` when `--max-spend` is set and you omit this) |
| `--price-post` | `0.005` | $/post used only for estimates + `--max-spend` |
| `--price-user` | `0.01` | $/user used only for estimates + `--max-spend` |
| `--search-pages` | `1`* | Pages per run (*auto = HTTP ceiling when `--max-spend` is set) |
| `--expansions` | `5` | Posts to expand per run |
| `--min-engagement` | `25` | Min score to queue expansion |
| `--search-mode` | `recent` | `recent` (7-day) or `all` (full archive, app-only) |
| `--work-dir` | auto | Override path; default `data/queries/<slug>/` |
| `--fresh` | off | Reset search cursor to newest (keeps graph data) |
| `--incremental` | off | Only new posts since last run |
| `--loop` | off | Repeat with `--sleep-minutes` between passes |
| `--export-after` | off | Write GEXF + CSV when done |

**Engagement score** = `likes + 2×retweets + 3×quotes + replies`

### `enrich` (free — no API)

| Flag | Default | Description |
|------|---------|-------------|
| `-q`, `--query` | — | Resolve `data/queries/<slug>/` (or use `--work-dir`) |
| `--work-dir` | auto | Folder containing `output/x_graph_nodes.csv` |
| `--output` | auto | Output path; default `output/x_graph_nodes_enriched.csv` |
| `--in-place` | off | Overwrite `x_graph_nodes.csv` instead of a new file |

Reads `x_graph_nodes.csv` + `x_graph_edges.csv`, writes nodes with added column:

| Column | Meaning |
|--------|---------|
| `primary_interaction` | Most frequent outgoing `Interaction` for that user |
| `none` | User has no outgoing edges in the graph |

Prints JSON stats on completion: `nodes`, `with_primary_interaction`, `interaction_types`.

Also available as `x-graph enrich` if installed via `pip install -e .`.

### `status` / `export` (free)

```bash
python -m x_graph.cli status -q "digital circus lang:en"
python -m x_graph.cli export -q "digital circus lang:en"
```

`status` returns `nodes`, `edges`, `seen_posts`, pagination cursor, and `has_more_older_posts`. Post timestamps (`created_at`) live in `state.db` → `seen_posts`, not in the exported CSVs — query them for crawl window stats:

```bash
python -c "import sqlite3; db=sqlite3.connect('data/queries/digital-circus-lang-en/state.db'); print(db.execute('SELECT MIN(created_at), MAX(created_at), COUNT(*) FROM seen_posts').fetchone())"
```

Per-run API usage is logged in `state.db` → `run_log` (`api_calls_attempted`, `api_calls_ok`). The tool counts **HTTP attempts**, not dollars — check [developer.x.com](https://developer.x.com) billing for actual spend.

---

## Credit safety

| Protection | Behavior |
|------------|----------|
| `--confirm-spend` | Blocks live API unless you explicitly opt in |
| `--dry-run` / `X_GRAPH_OFFLINE=1` | Zero API calls |
| Default `--search-pages 1` | One search request per run — re-run to page backward |
| Skip when crawl exhausted | Re-runs after completion do not re-hit the API |
| Stop on all-duplicate page | Won't walk already-seen history page-by-page |
| Cap empty archive windows | At most 3 empty full-archive window steps per run |
| No generic retries (`max_retries=0`) | Auth/404 errors don't retry in a loop |
| Rate-limit backoff | Up to 2 retries, 60s → 120s wait on 429 / throttle |
| Transient backoff | 1 retry, 20s wait on `request failed` / 502 / 503 |
| Search fail → skip expansions | Won't expand after a failed search |
| One expansion error → stop | Won't walk the whole queue on errors |
| Run lock (`.collect.lock`) | Blocks concurrent collects on same query |
| `api_calls_attempted` | Counts every HTTP attempt (retries count too) |
| 2.5s delay after successful calls | Reduces rate-limit hits between pages |
| Retweet expansion | Uses **original** tweet ID, not RT wrapper |
| Deleted posts | Skipped gracefully — no crash, no retry loop |

Block all API calls globally:

```powershell
set X_GRAPH_OFFLINE=1          # Windows
export X_GRAPH_OFFLINE=1       # macOS / Linux
```

### Reading the run summary

```json
{
  "api_calls_attempted": 1,
  "api_calls_ok": 1,
  "stopped_reason": "completed",
  "search_posts_new": 99,
  "nodes": 898,
  "edges": 1062
}
```

| `stopped_reason` | Meaning |
|------------------|---------|
| `completed` | Normal finish |
| `dry_run` | No API calls made |
| `search_exhausted` | Crawl already finished — **zero API calls** (use `--fresh` / `--incremental`) |
| `max_spend_reached` | Hit `--max-spend` estimated dollar cap |
| `api_budget_exhausted` | Hit `--api-budget` |
| `ApiRateLimitError` | Rate limited after backoff retries exhausted |
| `ApiFatalError` | Fatal error after retries (auth, etc.) |
| `search_failed` | Search failed — expansions were skipped |

---

## Output layout

Each query gets its own directory:

```
data/queries/digital-circus-lang-en/
├── state.db                 # posts seen, edges, pagination cursor, expansion queue
└── output/
    ├── x_graph.gexf              # ← open this in Gephi
    ├── x_graph_nodes.csv
    ├── x_graph_nodes_enriched.csv   # after `enrich` (includes primary_interaction)
    └── x_graph_edges.csv
```

| File | Key columns |
|------|-------------|
| `x_graph_nodes.csv` | `Id, Label, username, name, profile_image_url` |
| `x_graph_nodes_enriched.csv` | above + `primary_interaction` |
| `x_graph_edges.csv` | `Source, Target, Weight, Interaction, post_id` |

The legacy flat layout (`data/output/`, `data/state.db`) is no longer used.

---

## Gephi

### Open the graph

1. **File → Open** → `data/queries/<slug>/output/x_graph.gexf`
2. **Overview** → **Layout** → **ForceAtlas 2** → Run ~30–60s → Stop

### CSV import (alternative)

1. Import `x_graph_nodes.csv` as **Nodes** (`Id` → ID, `Label` → Label)
2. Import `x_graph_edges.csv` as **Edges** (`Source`, `Target`, `Weight`, `Interaction`)
3. Switch to Overview → ForceAtlas 2

### Color nodes by interaction type

Gephi can only color **nodes** from node attributes. Edges have `interaction`; nodes do not until you enrich them:

```bash
python -m x_graph.cli enrich -q "digital circus lang:en"
```

Writes `data/queries/digital-circus-lang-en/output/x_graph_nodes_enriched.csv` with a `primary_interaction` column (most common outgoing edge type per user).

In Gephi:

1. **Data Laboratory → Import Spreadsheet** → `x_graph_nodes_enriched.csv` → **Nodes table** (merge on `Id`)
2. **Appearance → Nodes → Color → Partition → `primary_interaction`**
3. **Appearance → Edges → Color → Partition → `interaction`** — use matching colors

Overwrite the original nodes file instead:

```bash
python -m x_graph.cli enrich -q "digital circus lang:en" --in-place
```

### Styling

- **Edge colors:** Appearance → Edges → Color → Partition → `interaction`
- **Node colors:** Appearance → Nodes → Color → Partition → `primary_interaction` (after `enrich`)
- **Node size:** Statistics → Degree → Run → Appearance → Nodes → Size → Ranking → Degree
- **Hub labels only:** Appearance → Labels → Size → Ranking → `degree` (raise max threshold until low-degree labels disappear)

### Move a single node (Overview)

Use the **arrow / selection** tool on the left of the graph — not the **hand** tool (hand pans the whole view). Stop ForceAtlas 2 before dragging. Zoom in so you grab the node circle, not empty space.

### Wrong Gephi version opens?

Uninstall Gephi 0.10. Right-click `.gexf` → **Open with** → `C:\Program Files\Gephi-0.11.2\bin\gephi64.exe` → **Always**.

### Example output

Screenshots in the repo root: `example-graph.png`, `example-graph-2.png`, `digital-circus-test.png`.

---

## MCP / Cursor (optional)

The CLI only needs `xurl` auth. For agent workflows, add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "xapi": {
      "command": "npx",
      "args": ["-y", "@xdevplatform/xurl", "mcp", "https://api.x.com/mcp"],
      "env": {
        "CLIENT_ID": "YOUR_X_APP_CLIENT_ID",
        "CLIENT_SECRET": "YOUR_X_APP_CLIENT_SECRET"
      }
    }
  }
}
```

Programmatic MCP integration: `x_graph/mcp_adapter.py`  
Tool schemas (reference): `mcps/xapi/tools/`

---

## Repo layout

```
gephi-x-mcp/
├── x_graph/                 # Python package
│   ├── cli.py               # Entry point
│   ├── collector.py         # Search + expand loop
│   ├── x_client.py          # xurl transport (UTF-8 safe, fail-fast)
│   ├── offline.py           # Dry-run stub
│   ├── run_lock.py          # Concurrent-run guard
│   ├── paths.py             # Query → folder slug
│   ├── state.py             # SQLite incremental state
│   ├── export.py            # GEXF + CSV
│   ├── enrich.py            # primary_interaction for Gephi node colors
│   └── ...
├── mcps/xapi/tools/         # xapi MCP tool schemas (no secrets)
├── data/queries/            # Per-topic graphs (see digital-circus-lang-en/)
├── example-graph*.png       # Sample visualizations
├── README.md
└── pyproject.toml
```

---

## Do not commit

| OK | Never |
|----|-------|
| Source code | `~/.xurl` (OAuth tokens) |
| `mcps/xapi/tools/` schemas | `CLIENT_ID` / `CLIENT_SECRET` |
| Example graph output / PNGs | `.env` with credentials |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Refusing live X API calls without --confirm-spend` | Add `--confirm-spend`, or use `--dry-run` |
| Credits burned unexpectedly | Use `--search-only`, `--search-pages 1`, lower `--api-budget`, never run two collects at once |
| `Error: request failed` / `ApiFatalError` on page 2 | Often rate limit or stale pagination — auto-retries once; if it persists, wait 1+ min and retry with `--search-pages 1`, or `--fresh` to reset cursor |
| `ApiRateLimitError` after long waits | Wait several minutes, then collect again with `--search-pages 1` |
| Only `MENTION` in `primary_interaction` | Expected on mention-heavy queries — partition **edges** by `interaction`; RT/REPLY/QUOTE are still in `x_graph_edges.csv` |
| Dragging moves whole graph in Gephi | Switch from hand tool to selection (arrow); stop layout first |
| `Another collect run is already active` | Wait, or delete stale `data/queries/<slug>/.collect.lock` |
| `UnicodeDecodeError` on Windows | Fixed — ensure you have the latest `x_client.py` |
| Expansion "post not found" | Deleted tweet — skipped automatically |
| OAuth 401 | `npx -y @xdevplatform/xurl auth oauth2` |
| `search_posts_all` 403 | Use default `--search-mode recent` |
| GEXF opens Gephi 0.10 | Re-associate file type with Gephi 0.11 |

---

## License

MIT