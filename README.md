# x-graph

Build **directed, weighted interaction graphs** from X (Twitter) search results and export them for [Gephi](https://gephi.org/) force-directed layout.

Nodes are **users** (username, display name, profile image). Edges are **interactions**: `MENTION`, `REPLY`, `RETWEET`, `QUOTE`, `LIKE`.

Designed for **incremental, cost-aware** collection: run repeatedly over hours or days without re-fetching the same posts or blowing through API credits.

## How it works

```
Search query → recent posts
      ↓
Extract inline edges (mentions, replies, RTs, quotes) — no extra API calls
      ↓
Queue high-engagement posts → expand likers / reposters / quoters (capped)
      ↓
SQLite state (dedupe + resume) → export GEXF + CSV
```

Uses the same X API endpoints as the official **[xapi MCP server](https://docs.x.com/tools/mcp)**. By default it calls the API through [`xurl`](https://github.com/xdevplatform/xurl) (the same OAuth bridge MCP uses).

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.10+** | No extra pip dependencies |
| **Node.js + npm** | For `npx @xdevplatform/xurl` |
| **X Developer account** | [developer.x.com](https://developer.x.com) — Pay-per-use / Production recommended |
| **Gephi** (optional) | To visualize the exported graph |

## 1. Create an X app

1. Go to the [X Developer Portal](https://developer.x.com) and create an app.
2. Enable **OAuth 2.0** with scopes: `tweet.read`, `users.read`, `like.read` (and `offline.access` for refresh).
3. Register redirect URI: `http://localhost:8080/callback`
4. Copy your **`CLIENT_ID`** and **`CLIENT_SECRET`**.

## 2. Authenticate with xurl

One-time login (opens browser):

```bash
npx -y @xdevplatform/xurl auth oauth2
```

Or with explicit credentials:

```bash
npx -y @xdevplatform/xurl auth apps add my-app --client-id YOUR_ID --client-secret YOUR_SECRET
npx -y @xdevplatform/xurl auth oauth2 --app my-app
```

Tokens are cached in `~/.xurl` on your machine — **never commit that folder**.

Verify:

```bash
npx -y @xdevplatform/xurl /2/users/me
```

## 3. Clone and run

```bash
git clone <your-repo-url>
cd gephi-x-mcp

python -m x_graph.cli collect --query "AI OR grok lang:en" --api-budget 100 --export-after
```

### CLI commands

**Collect one pass** (safe to repeat — resumes from SQLite state):

```bash
python -m x_graph.cli collect --query "bbbyq lang:en" --api-budget 100 --export-after
```

**Long-running loop** (e.g. every 15 minutes):

```bash
python -m x_graph.cli collect --query "bbbyq lang:en" --loop --sleep-minutes 15 --api-budget 50 --export-after
```

**Check progress:**

```bash
python -m x_graph.cli status
```

**Export without collecting:**

```bash
python -m x_graph.cli export
```

### Useful flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--api-budget` | `200` | Max API **calls** per run (not dollars) |
| `--search-pages` | `5` | Search result pages per run |
| `--expansions` | `50` | Posts to expand (likers/RTs/quotes) per run |
| `--min-engagement` | `10` | Min engagement score to queue expansion |
| `--search-mode` | `recent` | `recent` (7-day) or `all` (full archive, app-only) |
| `--work-dir` | `data` | State DB + output folder |

**Engagement score** = `likes + 2×retweets + 3×quotes + replies`

## Output files

Written to `data/output/`:

| File | Description |
|------|-------------|
| `x_graph.gexf` | Single file — **easiest Gephi import** |
| `x_graph_nodes.csv` | `Id, Label, username, name, profile_image_url` |
| `x_graph_edges.csv` | `Source, Target, Weight, Interaction, post_id` |

State is stored in `data/state.db` (seen posts, edges, expansion queue, pagination cursors).

## Import into Gephi

### Option A — GEXF (recommended)

1. **File → Open** → `data/output/x_graph.gexf`
2. **Overview** → Layout → **ForceAtlas 2** → Run

### Option B — CSV (nodes + edges)

1. **Data Laboratory → Import Spreadsheet** → `x_graph_nodes.csv` → **Nodes table**  
   Map `Id` → ID, `Label` → Label
2. **Import Spreadsheet** → `x_graph_edges.csv` → **Edges table**  
   Map `Source`, `Target`, `Weight`, `Interaction`
3. Switch to **Overview** → run **ForceAtlas 2**

**Style tips:** Appearance → Edges → Color → Partition → `interaction`  
Statistics → Degree → Run → Appearance → Nodes → Size → Ranking → Degree

## MCP / Cursor setup (optional)

To use xapi MCP tools from Cursor or Grok instead of CLI-only workflows, add to `~/.cursor/mcp.json` or `.cursor/mcp.json`:

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

**Do not commit real `CLIENT_ID` / `CLIENT_SECRET` values.** Use env vars or a local config file listed in `.gitignore`.

The CLI does not require MCP — `xurl` auth alone is enough for `python -m x_graph.cli collect`.

For programmatic use inside an MCP host, see `x_graph/mcp_adapter.py`.

## Cost control

- **`--api-budget`** caps HTTP calls per run.
- **`since_id`** incremental search — only new posts after the first run.
- **Inline edges** (mention/reply/RT/quote) cost zero extra calls.
- **Expansion sampling** — high-engagement posts always expand; medium posts sampled at 25%.
- **Per-post caps** on likers, reposters, and quotes.
- Deleted/unavailable posts are skipped without crashing.

Dollar cost depends on your X API plan; this tool only counts calls.

## Project layout

```
gephi-x-mcp/
├── x_graph/
│   ├── cli.py          # Command-line entry point
│   ├── collector.py    # Search + expand + graph build loop
│   ├── config.py       # Tunable settings
│   ├── export.py       # GEXF + CSV export
│   ├── graph.py        # Node/edge model
│   ├── state.py        # SQLite incremental state
│   ├── x_client.py     # X API client (xurl transport)
│   └── mcp_adapter.py  # Optional MCP host integration
├── mcps/xapi/tools/    # MCP tool schemas (reference only)
├── data/
│   ├── state.db        # Collection checkpoint (safe to delete to reset)
│   └── output/         # GEXF + CSV exports
└── pyproject.toml
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `xurl` / `npx` not found | Install [Node.js](https://nodejs.org/); verify with `npx -y @xdevplatform/xurl /2/users/me` |
| OAuth / 401 errors | Re-run `xurl auth oauth2`; check app is in Production + Pay-per-use |
| `search_posts_all` 403 | Full-archive search needs app-only auth; use default `--search-mode recent` |
| Expansion "post not found" | Normal for deleted tweets — skipped automatically |
| GEXF opens old Gephi | Re-associate `.gexf` with Gephi 0.11; uninstall 0.10 |

## License

PHREEEEEEEEEEEEEEEE