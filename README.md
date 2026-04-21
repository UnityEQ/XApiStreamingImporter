# Gephi X API Streaming Importer

> A [Gephi](https://gephi.org) plugin that builds a live **user-interaction network** from the X (Twitter) API v2. Type a keyword, paste a bearer token, hit Start — nodes and edges accumulate in the graph as matching tweets arrive.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Java 11+](https://img.shields.io/badge/Java-11%2B-blue.svg)](https://adoptium.net/)
[![Gephi 0.10](https://img.shields.io/badge/Gephi-0.10-green.svg)](https://gephi.org)

---

## What it does

Give the plugin a keyword or an [X API search query](https://docs.x.com/x-api/posts/search/integrate/build-a-query) such as `nasa -is:retweet lang:en`. It polls (or streams) tweets matching that query and, for every tweet, mutates the currently open Gephi graph in real time:

- **Nodes** = X users (the tweet author, plus anyone they mention, reply to, retweet, or quote).
- **Edges** = directed user → user interactions of type `MENTIONS`, `REPLY`, `RETWEET`, or `QUOTE`. Repeat interactions don't create duplicate edges — the edge weight increments.

The result is a directed, weighted social graph centered on whatever topic you're tracking, ready for community detection, layout, filtering, and everything else Gephi does best.

### Why this exists

There was a [totetmatt/gephi-plugins `twitter_v2`](https://github.com/totetmatt/gephi-plugins/tree/twitter_v2) plugin that did something similar, but it hasn't been maintained since January 2023 and relies on a stale Twitter SDK and deprecated auth assumptions. This project is a ground-up rewrite that:

- Targets the current Gephi plugin parent (`0.10.0`).
- Uses the stdlib `java.net.http.HttpClient` — no third-party Twitter SDK.
- Handles modern X API v2 error codes (401 / 402 / 403 / 429) with actionable, clickable error messages.
- Never persists your bearer token to disk.
- Optionally delegates auth to [xdevplatform/xurl](https://github.com/xdevplatform/xurl) for OAuth 2.0 users.

---

## ⚠️ X API access: this requires a paid tier

As of 2024 the X API Free tier is **write-only** (1,500 posts/month, no read access). Both data paths in this plugin use read endpoints, so the Free tier will return `HTTP 402 CreditsDepleted` or `HTTP 403`.

| Tier  | Monthly cost | `search/recent` | `search/stream` |
| ----- | -----------: | :-------------: | :-------------: |
| Free  |       $0     |       ❌         |       ❌         |
| Basic |     ~$200    |       ✅         |       ✅         |
| Pro   |    ~$5,000   |       ✅         |   ✅ (higher limits)  |

Upgrade at [developer.x.com/en/portal/products](https://developer.x.com/en/portal/products). The plugin surfaces these errors with a clickable upgrade link in its status panel, so it's obvious when you've hit a tier gate rather than a bug.

---

## Features

- **Two data paths**, switchable at runtime:
  - **Polling** — `GET /2/tweets/search/recent`, on an interval you choose (15–3600 s), with `since_id` pagination so each poll only returns new tweets.
  - **Filtered stream** — `GET /2/tweets/search/stream`, long-lived connection with a server-side rule managed automatically by the plugin.
- **Two transport implementations**:
  - **Java HTTP** (default) — stdlib `java.net.http`, authenticate with a pasted bearer token.
  - **xurl subprocess** — shells out to the `xurl` CLI for users who have it installed and authenticated. Lets power users use OAuth 2.0 user-context without the plugin implementing a PKCE flow.
- **User-interaction graph**:
  - Nodes are keyed by X user id, labeled `@username`, with `name` and `profile_image_url` attributes.
  - Edges are directed, weighted, and tagged with an `interaction` column (`MENTIONS` / `REPLY` / `RETWEET` / `QUOTE`).
  - Repeat interactions increment `edge.weight` rather than creating duplicates.
- **Thread-safe graph writes**: one `graph.writeLock()` per batch, not per tweet.
- **Clear error UX**: 401 / 402 / 403 / 429 each get a specific, human-readable message with a clickable link (to the X dashboard for token issues or the product page for tier upgrades). Errors do not get clobbered by a trailing "Stopped" message.
- **No token persistence**: the bearer token lives only in memory for the current Gephi session. Close Gephi and it's gone.

---

## Install

### Option 1 — Drop the prebuilt NBM into Gephi

1. Grab the `gephi-xapi-importer-1.0.0.nbm` file (from a [release](../../releases), or build it yourself — see below).
2. In Gephi, open **Tools → Plugins → Downloaded → Add Plugins…**, select the `.nbm`, click **Install**, and restart when prompted.

### Option 2 — Build from source

Prerequisites:

- **JDK 11 or later** — verify with `java -version`. ([Eclipse Adoptium](https://adoptium.net/) builds work fine.)
- **Apache Maven 3.6+** — verify with `mvn -v`. Install from [maven.apache.org](https://maven.apache.org/download.cgi) or `winget install Apache.Maven` on Windows.

```bash
git clone https://github.com/<you>/gephi-xapi-importer.git
cd gephi-xapi-importer
mvn clean install
```

First build downloads the Gephi parent POM and the NetBeans platform dependencies from Maven Central (several minutes). Subsequent builds are ~10 seconds.

Output:

```
modules/XApiStreamingImporter/target/nbm/gephi-xapi-importer-1.0.0.nbm
```

Install that NBM into Gephi as in Option 1.

### Option 3 — Run a sandboxed Gephi from Maven

The Gephi plugin parent POM provides a `run` profile that downloads a matching Gephi distribution and launches it with this plugin already loaded. Handy for iterative development.

```bash
mvn -Prun
```

---

## Use

1. Open Gephi. Create or open a project so there's a Workspace with a graph. (An empty graph is fine; the plugin creates nodes and edges into whatever's active.)
2. **Window → X API Streaming** — the plugin panel docks on the left.
3. Paste an **X API v2 Bearer Token**.
   - Don't have one? Click **"How to get a bearer token?"** in the panel, or go directly to [developer.x.com/en/portal/dashboard](https://developer.x.com/en/portal/dashboard) → create a Project and App → **Keys and tokens** tab → copy the **Bearer Token**.
   - Make sure your app is subscribed to a tier that includes read access (see the table above).
4. Enter a **keyword**. You can use the [X query operators](https://docs.x.com/x-api/posts/search/integrate/build-a-query) — e.g. `(climate OR carbon) lang:en -is:retweet`.
5. Pick **Polling** (default; recommended for first-time use) and set an interval (default 30 s, minimum 15 s).
6. Click **Start**. The status line turns green and shows "Polled N tweets. Next in …s." Nodes and edges appear in the Overview.
7. **Stop** ends the session; the graph is preserved. **Clear graph** empties it.

### Using `xurl` instead of a pasted token

1. Install `xurl`:
   - macOS: `brew install --cask xdevplatform/tap/xurl`
   - Anywhere: `npm install -g @xdevplatform/xurl`
   - Or download a binary from [github.com/xdevplatform/xurl/releases](https://github.com/xdevplatform/xurl/releases)
2. Authenticate once: `xurl auth oauth2` and complete the browser flow. Credentials land at `~/.xurl`.
3. In the plugin, select **Transport: xurl subprocess**. The bearer token field is ignored; every request is shelled out to `xurl`.

Billing tier still applies — xurl routes the request differently, but the endpoint gate is the same.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  MainXApiWindow (TopComponent) ─ form + status pane              │
│         │                                                        │
│         │  SessionConfig (keyword, transport, interval, …)       │
│         ▼                                                        │
│  XApiSession (singleton, owns a daemon Thread)                   │
│         │                                                        │
│         ├─ PollingTask  (loop: search/recent → batch → sleep)    │
│         └─ StreamTask   (rule sync → long-lived stream → flush)  │
│                 │                                                │
│                 ▼                                                │
│  XApiTransport  ◄── JavaHttpTransport  (java.net.http + Bearer)  │
│                 ◄── XurlTransport      (ProcessBuilder "xurl …") │
│                 │                                                │
│                 ▼                                                │
│  Jackson DTOs (Tweet, User, Entities, ReferencedTweet, …)        │
│                 │                                                │
│                 ▼                                                │
│  GraphWriter  ─── write lock per batch ───► Gephi GraphModel     │
│       ▲                                                          │
│       │ processTweet(…)                                          │
│       │                                                          │
│  UserNetworkLogic (tweet → author/mentions/referenced → edges)   │
└──────────────────────────────────────────────────────────────────┘
```

### Key files

| File | Role |
|---|---|
| `modules/XApiStreamingImporter/src/main/java/org/gephi/plugins/xapi/ui/MainXApiWindow.java` | TopComponent, form layout, Start/Stop lifecycle, clickable HTML status pane |
| `core/XApiSession.java` | Singleton session controller, owns the worker thread |
| `core/PollingTask.java` | `search/recent` loop with `since_id` + 429 back-off + tier-aware error handling |
| `core/StreamTask.java` | Stream-rule management and long-lived stream consumer |
| `transport/XApiTransport.java` + impls | Pluggable HTTP layer (Java HTTP / xurl subprocess) |
| `graph/UserNetworkLogic.java` | Tweet → node/edge mapping for the user-interaction network |
| `graph/GraphWriter.java` | Lazy column creation, idempotent node/edge writes, write-lock management |
| `model/*` | Jackson DTOs mirroring the minimal subset of X API v2 response shape |

### Thread safety

- The worker thread is a single daemon `Thread` per session. It's the only writer to the Gephi graph.
- `GraphWriter.processBatch` acquires `graph.writeLock()` **once per batch** (not per tweet) and releases in a `finally`.
- UI updates from the worker flow through `SwingUtilities.invokeLater` via `StatusListener`.
- `cancelled` is an `AtomicBoolean`; the worker checks it between polls, and the session also calls `thread.interrupt()` + `transport.close()` on Stop so any in-flight HTTP read is unblocked immediately.

### Security posture

- **No token is ever written to disk.** `XApiPreferences` persists only non-sensitive UI state (keyword, interval, transport choice, data-path choice). An earlier build optionally stored the bearer token plaintext; the current build scrubs any such leftover on first open.
- The token lives in a `JPasswordField` and a local `String` in the running `SessionConfig`. When the JVM exits, it's gone.
- This repository contains **no secrets**. `.claude/` (local tooling config) is gitignored, and `.env` / `*bearer*.txt` patterns are pre-blocked in `.gitignore` as a defense in depth.
- If you want durable storage of your bearer token, use OS-level secret storage (e.g. Windows Credential Manager, macOS Keychain, Linux Secret Service) externally and paste into the panel each session.

---

## Development

### Project layout

```
gephi/
├── pom.xml                  # parent, inherits org.gephi:gephi-plugin-parent:0.10.0
├── README.md
├── LICENSE
├── .gitignore
└── modules/
    └── XApiStreamingImporter/
        ├── pom.xml                      # nbm module
        └── src/main/
            ├── java/org/gephi/plugins/xapi/
            │   ├── ui/                  # Swing TopComponent
            │   ├── core/                # session + tasks
            │   ├── transport/           # HTTP + xurl transports
            │   ├── graph/               # graph writer + network logic
            │   ├── model/               # Jackson DTOs
            │   └── prefs/               # NbPreferences wrapper
            └── resources/org/gephi/plugins/xapi/
                ├── Bundle.properties    # module display name/description
                └── ui/Bundle.properties # action display names
```

### Handy commands

```bash
# Full clean build, run tests, produce NBM
mvn clean install

# Fast incremental rebuild of just the module
mvn -pl modules/XApiStreamingImporter -am install -B -ntp

# Run a sandboxed Gephi with the plugin preloaded
mvn -Prun
```

### Adding more graph modes

`graph/NetworkLogic.java` is an interface. Drop in a new implementation — e.g. `HashtagNetworkLogic` for hashtag co-occurrence, `FullNetworkLogic` for the richer tweet/user/hashtag/URL graph the old plugin supported — and expose a selector in the UI. The `GraphWriter` / `ensureColumns` / locking plumbing stays the same.

---

## Known limitations / roadmap

- **Paid tier required.** There is no workaround for the X API tier gate; nothing the plugin does can call endpoints your app isn't entitled to.
- **User Network is the only mode shipped.** Hashtag and full (tweet + user + hashtag + URL) modes from the old plugin are intentionally deferred. `NetworkLogic` is ready for them.
- **Rate-limit adaptation is minimal.** On 429 the interval doubles up to 15 min; it does not un-halve after a successful poll beyond resetting to the user's configured interval.
- **No graph-size cap.** Long-running sessions balloon the graph. A "max N nodes" or rolling-window eviction is on the wishlist.
- **No temporal/dynamic columns.** The old plugin wrote tweet timestamps for Gephi's timeline replay; this one drops that for MVP.
- **Bearer tokens are plaintext in memory** (by necessity — they're sent in HTTP headers). If process memory on your machine is exposed to an attacker, they can read it; that's a general desktop-app caveat, not specific to this plugin.

---

## Contributing

Issues and PRs welcome. Before opening a PR:

1. `mvn clean install` passes.
2. New graph modes go behind `NetworkLogic`; don't special-case `GraphWriter`.
3. Any new runtime options get persisted through `XApiPreferences` **unless** they're secrets — secrets stay in memory only.
4. Keep the status panel's error messages specific and actionable. Generic "something went wrong" is a regression.

---

## Acknowledgements

- Inspired by [totetmatt/gephi-plugins](https://github.com/totetmatt/gephi-plugins) `twitter_v2` branch, which walked so this could run.
- Built against patterns from the [Gephi plugin bootcamp](https://github.com/gephi/gephi-plugins-bootcamp).
- Optional integration with [xdevplatform/xurl](https://github.com/xdevplatform/xurl).

## License

[MIT](LICENSE).
