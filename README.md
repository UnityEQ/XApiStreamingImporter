# gephi-x-mcp

Turn X (Twitter) search results into **directed, weighted interaction graphs** for [Gephi](https://gephi.org/).

| | |
|---|---|
| **Nodes** | Users — `username`, display name, profile image |
| **Edges** | `MENTION` · `REPLY` · `RETWEET` · `QUOTE` · `LIKE` (weighted) |
| **Transport** | [`xurl`](https://github.com/xdevplatform/xurl) — same OAuth bridge as the [xapi MCP server](https://docs.x.com/tools/mcp) |

Built for researchers who jump between topics: **one folder per query**, backward crawls from recent posts, and hard guards so a bad run cannot silently burn through pay-per-use credits.

## Quick start

After [setup](#setup-one-time), from the repo root:

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --search-only --api-budget 10 --export-after
```

That will:

1. Search recent posts matching `digital circus lang:en` (newest first)
2. Build mention / reply / RT / quote edges from the results (no extra API calls)
3. Save state to `data/queries/digital-circus-lang-en/`
4. Export `x_graph.gexf` + CSV to `data/queries/digital-circus-lang-en/output/`

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
```

**Default mode is backward crawl** — each run starts at the most recent matching posts and pages older. Re-run the same query to keep going back in time (last 7 days with `--search-mode recent`).

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
```

### Cheapest live collect (recommended)

Posts + inline edges only (`MENTION`, `REPLY`, `RETWEET`, `QUOTE`). No liker/RT/quote expansion calls.

```bash
python -m x_graph.cli collect -q "digital circus lang:en" --confirm-spend --search-only --api-budget 10 --export-after
```

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

---

## CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `-q`, `--query` | required | X search query |
| `--confirm-spend` | off | **Required** for any live API call |
| `--dry-run` | off | Plan only — zero API calls |
| `--search-only` | off | Skip expansions (cheapest live mode) |
| `--api-budget` | `30` | Max HTTP attempts per run (failures count) |
| `--search-pages` | `3` | Pages per run × 100 posts, going older |
| `--expansions` | `5` | Posts to expand per run |
| `--min-engagement` | `25` | Min score to queue expansion |
| `--search-mode` | `recent` | `recent` (7-day) or `all` (full archive, app-only) |
| `--work-dir` | auto | Override path; default `data/queries/<slug>/` |
| `--fresh` | off | Reset search cursor to newest (keeps graph data) |
| `--incremental` | off | Only new posts since last run |
| `--loop` | off | Repeat with `--sleep-minutes` between passes |
| `--export-after` | off | Write GEXF + CSV when done |

**Engagement score** = `likes + 2×retweets + 3×quotes + replies`

---

## Credit safety

| Protection | Behavior |
|------------|----------|
| `--confirm-spend` | Blocks live API unless you explicitly opt in |
| `--dry-run` / `X_GRAPH_OFFLINE=1` | Zero API calls |
| No retries (`max_retries=0`) | One failure → stop (no 3× retry storm) |
| Search fail → skip expansions | Won't expand after a failed search |
| One expansion error → stop | Won't walk the whole queue on errors |
| Run lock (`.collect.lock`) | Blocks concurrent collects on same query |
| `api_calls_attempted` | Counts every HTTP attempt in run summary |
| 1s delay between calls | Reduces rate-limit hits |
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
| `api_budget_exhausted` | Hit `--api-budget` |
| `ApiRateLimitError` | Rate limited — stopped immediately |
| `ApiFatalError` | Auth/network error — stopped immediately |
| `search_failed` | Search failed — expansions were skipped |

---

## Output layout

Each query gets its own directory:

```
data/queries/digital-circus-lang-en/
├── state.db                 # posts seen, edges, pagination cursor, expansion queue
└── output/
    ├── x_graph.gexf         # ← open this in Gephi
    ├── x_graph_nodes.csv
    └── x_graph_edges.csv
```

| File | Key columns |
|------|-------------|
| `x_graph_nodes.csv` | `Id, Label, username, name, profile_image_url` |
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

### Styling

- **Edge colors:** Appearance → Edges → Color → Partition → `interaction`
- **Node size:** Statistics → Degree → Run → Appearance → Nodes → Size → Ranking → Degree
- **Hub labels only:** Appearance → Labels → Size → Ranking → `degree` (raise max threshold until low-degree labels disappear)

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
| Credits burned unexpectedly | Use `--search-only`, lower `--api-budget`, never run two collects at once |
| `Error: request failed` | Rate limit or outage — wait 1+ min, retry with `--search-only --api-budget 5` |
| `Another collect run is already active` | Wait, or delete stale `data/queries/<slug>/.collect.lock` |
| `UnicodeDecodeError` on Windows | Fixed — ensure you have the latest `x_client.py` |
| Expansion "post not found" | Deleted tweet — skipped automatically |
| OAuth 401 | `npx -y @xdevplatform/xurl auth oauth2` |
| `search_posts_all` 403 | Use default `--search-mode recent` |
| GEXF opens Gephi 0.10 | Re-associate file type with Gephi 0.11 |

---

## License

MIT