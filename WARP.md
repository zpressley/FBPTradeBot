# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## What this repo is
A Python Discord bot for an FBP fantasy league with:
- Trade submission + manager approvals (private thread workflow)
- Roster + player lookup powered by locally cached JSON data
- A draft system (stateful, persisted to `data/`)
- Service-days tracking commands backed by a generated stats JSON

Rules: 
For fbp-trade-bot and fbp-hub repos make sure to always run player data through UPID first to ensure that there is a unique ID for players that connects it with all other players in the system. 


## Common commands
This repo does not include a formal task runner (no `Makefile`, `pyproject.toml`, `pytest.ini`, etc.). The commands below reflect what exists in the code today.

### Install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

### Run the Discord bot
Entry point: `bot.py`

```bash
source venv/bin/activate
python bot.py
```

Notes:
- `bot.py` expects `DISCORD_TOKEN` in the environment (it also calls `dotenv.load_dotenv()` for local `.env` usage).
- On startup, `bot.py` writes `google_creds.json` from `GOOGLE_CREDS_JSON` (if present) and writes `token.json` from `YAHOO_TOKEN_JSON` (if present).

### Run bot + health endpoint (FastAPI + Uvicorn)
Entry point: `health.py`

```bash
source venv/bin/activate
python health.py
```

This starts:
- the Discord bot (same `DISCORD_TOKEN` expectation)
- a FastAPI server (defaults to port `8000`, or uses `$PORT`)
- the auction web API endpoints (`/api/auction/current`, `/api/auction/bid`, `/api/auction/match`), which require `BOT_API_KEY` and delegate to `auction_manager.AuctionManager`

### Update the local data cache (data pipeline)
Most bot commands rely on JSON files under `data/`. The pipeline scripts are in `data_pipeline/`.

Run everything:
```bash
source venv/bin/activate
python data_pipeline/update_all.py
```

Run individual steps:
```bash
source venv/bin/activate
python data_pipeline/update_yahoo_players.py
python data_pipeline/update_hub_players.py
python data_pipeline/update_wizbucks.py
python data_pipeline/merge_players.py
python data_pipeline/save_standings.py
```

### Service-time daily pipeline
The `service_time/daily_service_tracker.py` script orchestrates the fuller daily pipeline for prospect service-time tracking (Yahoo/Hub updates, roster events, service-day calculations, and merge back into `data/`).

```bash
source venv/bin/activate
python service_time/daily_service_tracker.py
```

This wrapper will call the underlying `service_time/*.py` and `random/*.py` helpers referenced inside that script.

Important: several commands load JSON at import time (notably `commands/roster.py`), so after updating `data/*.json` you typically need to restart the bot process for changes to be reflected.

Note: the Yahoo-related pipeline scripts import `token_manager` (e.g. `from token_manager import get_access_token`). In this repo, the implementation currently lives in `random/token_manager.py`, so running the pipeline may require fixing the import path (e.g., moving/duplicating the module or adjusting imports).

### Lint / typecheck
No linter/typechecker is configured in-repo (no Ruff/Black/Mypy config files).

### Tests
No automated test runner is configured. The repo contains ad-hoc scripts under `random/`, `service_time/`, and the `tests/` directory that are used like manual tests.

Examples (run one at a time):
```bash
source venv/bin/activate
python random/test_trade_logic.py           # trade workflow checks
python service_time/test_service-commands.py  # service command data sanity checks
python tests/test_robust_parser.py          # CSV rank parser
```

### One-off scripts
For any future one-off or maintenance workflows (e.g., reading or manipulating data with standalone Python processes), prefer placing them under the `scripts/` directory.

- Use `scripts/` for reusable but non-core tooling.
- Use `scripts/archive/` for one-time or historical cleanup scripts that are kept only for reference.

This keeps the repository root and `data_pipeline/` focused on core, recurring processes.

## Code architecture (big picture)

### Runtime entry points
- `bot.py`: primary bot process.
  - Configures Discord intents, creates a `commands.Bot`, loads cogs from `commands.*`, and syncs slash commands.
  - Also writes credential files (`google_creds.json`, `token.json`) from env vars if provided.
- `health.py`: combined bot + FastAPI server.
  - Useful for hosting environments that expect an HTTP health check.

### Discord bot structure
Cogs live in `commands/` and are loaded via `bot.load_extension(...)`.

Key cogs/modules:
- `commands/trade.py`: `/trade` slash command.
  - Validates:
    - manager identity via `commands/utils.py` mapping (`DISCORD_ID_TO_TEAM`)
    - team abbreviations
    - Wiz Bucks amounts vs `data/wizbucks.json`
    - player ownership vs `data/combined_players.json`
  - Produces a “preview + confirm” interaction; confirmation hands off to `commands/trade_logic.py`.
- `commands/trade_logic.py`: trade thread + approval workflow.
  - Creates a private thread in a “pending trades” channel and posts the final approved trade in a “trades” channel.
  - Channel IDs are hardcoded at the top of this file.
- `commands/roster.py`: `/roster` command.
  - Reads `data/combined_players.json` once at import time into `combined_data`.
- `commands/player.py` + `commands/lookup.py`: `/player` lookup + fuzzy matching utilities.
  - `commands/lookup.py` loads `data/combined_players.json` and exposes `fuzzy_lookup_all`.
- `commands/standings.py`: `/standings` reads from `data/standings.json`.
- `commands/auction.py`: prospect auction portal Discord interface (`/auction`, `/bid`) backed by `auction_manager.AuctionManager` and a background `auction_tick` task for scheduled alerts.
- `commands/service.py`: `/service`, `/prospects`, `/alerts` read from `data/service_stats.json`.
- `commands/database_commands.py`: admin commands (`/db_setup`, `/db_status`, `/db_refresh`, `/db_find`) that manage a dedicated prospect database Discord channel, backed by `draft/database_channel_manager.py` and `draft/database_tracker.py`.

Shared constants/helpers:
- `commands/utils.py`: maps manager/team <-> Discord IDs and contains trade timestamp helpers (`get_trade_dates`).

### Draft subsystem
The draft feature is split between Discord cogs and pure “state manager” code.

- `commands/draft.py`: Discord-facing draft orchestration.
  - Handles `/draft start|pause|continue|status|undo|order`.
  - Also listens to messages in the draft channel (picks are typed as plain messages).
  - Uses persisted state and a timer task (autopick fallback).
- `draft/draft_manager.py`: core draft state machine + persistence.
  - Reads draft order from `data/draft_order_2025.json`.
  - Persists per-draft state to `data/draft_state_{draft_type}_2025.json`.
- `commands/board.py` + `draft/board_manager.py`: manager draft boards.
  - Stores per-team ordered target lists in `data/manager_boards_2025.json`.
  - Draft autopick prefers the team’s board (`BoardManager.get_next_available(...)`).
- `draft/pick_validator.py`: rule validation logic (wired for use, but the Discord draft flow currently uses a lightweight confirmation flow).
- Additional narrative documentation for the draft system lives in `DRAFT_SYSTEM_HANDOFF.md` and the `docs/` directory.

### Prospect database & live pick tracking
The prospect database subsystem ties together Discord commands, a dedicated database channel, and MLB stats/ownership data.

- `draft/database_channel_manager.py` + `draft/database_tracker.py`: manage a “prospect database” channel and per-position threads, posting ranked prospect lists and tracking the location of each player’s status line in Discord messages for later edits.
- `commands/database_commands.py`: admin slash commands that create/refresh the database channel, check status, and look up player locations, persisting config to `data/database_config.json` and tracker state to `data/database_tracker.json`.
- `draft/draft_database_integration.py`: optional integration layer that lets the draft flow enqueue pick updates into the database so tracked prospect lines are marked as picked in place.
- `prospect_stats_repository.py`: pulls MLB Stats API data and combines it with ownership data and cached MLB IDs under `data/prospect_stats/`, which the database channel manager uses when building prospect lists.

### Prospect auction subsystem & web API
The prospect auction flow centralizes business rules in `auction_manager.py` and exposes them via both Discord slash commands and HTTP endpoints.

- `auction_manager.AuctionManager`: single source of truth for auction phases, bid validation, match/forfeit decisions, and persistence to `data/auction_current.json`. Also reads from `data/combined_players.json`, `data/wizbucks.json`, `data/standings.json`, and `config/season_dates.json`.
- `commands/auction.py`: Discord cog that wires `/auction` and `/bid` to `AuctionManager` and runs a background `auction_tick` task to send scheduled alerts based on the current auction phase.
- `health.py`: in addition to the health check, hosts the auction FastAPI endpoints (`/api/auction/current`, `/api/auction/bid`, `/api/auction/match`) protected by `BOT_API_KEY`. Successful bid/match calls both log to Discord and attempt a best-effort `git add/commit/push` of `data/auction_current.json` so downstream consumers (such as the web portal) can sync state.

### Data model: `data/` as the contract between scripts and bot
The bot expects JSON files under `data/` to exist and match the schema produced by pipeline scripts.

Most important files:
- `data/combined_players.json`: merged roster view (Yahoo rosters + Google Sheet metadata). Used by trade/roster/player lookup.
- `data/wizbucks.json`: Wiz Bucks balances from Google Sheets. Used by `/trade` validation.
- `data/standings.json`: standings snapshot used by `/standings`.
- `data/draft_order_2025.json`, `data/draft_state_*_2025.json`, `data/manager_boards_2025.json`: draft system persistence.
- `data/service_stats.json`: service-days snapshot used by `/service`/`/prospects`/`/alerts`.

### Data pipeline
Scripts in `data_pipeline/` populate the `data/` directory.

High-level flow (see `data_pipeline/update_all.py`):
1) Pull Yahoo rosters -> `data/yahoo_players.json`
2) Pull Google Sheet “Player Data” -> `data/sheet_players.json`
3) Pull Google Sheet Wiz Bucks -> `data/wizbucks.json`
4) Merge Yahoo + sheet data -> `data/combined_players.json`
5) Pull standings/scoreboard -> `data/standings.json`

Other directories:
- `service_time/`: additional scripts for service-day tracking and Google Sheets updates.
- `random/`: one-off utilities and experiments (includes a `token_manager.py` used by some scripts).
