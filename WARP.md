# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## What this repo is
A Python Discord bot for an FBP fantasy league with:
- Trade submission + manager approvals (private thread workflow)
- Roster + player lookup powered by locally cached JSON data
- A draft system (stateful, persisted to `data/`)
- Service-days tracking commands backed by a generated stats JSON

## Rules
### Data Identity
For fbp-trade-bot and fbp-hub repos make sure to always run player data through UPID first to ensure that there is a unique ID for players that connects it with all other players in the system.
### Coding Guidelines (Karpathy Behavioral Rules)
These guidelines apply to all code changes in this repo.
#### 1. Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them instead of picking silently.
- If a simpler approach exists, say so.
- If something is unclear, stop and name what is confusing.
#### 2. Simplicity First
- Write the minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No configurability that was not requested.
- No error handling for impossible scenarios.
- If a solution is overcomplicated, simplify it.
#### 3. Surgical Changes
- Touch only what you must.
- Do not improve adjacent code, comments, or formatting unless required.
- Do not refactor things that are not broken.
- Match existing style.
- If unrelated dead code is noticed, mention it instead of deleting it.
- Remove imports, variables, and functions made unused by your own changes, but do not remove pre-existing dead code unless asked.
- Every changed line should trace directly to the request.
#### 4. Goal-Driven Execution
- Turn tasks into verifiable goals before implementing.
- For bug fixes, reproduce the bug first and verify the fix.
- For refactors, verify behavior before and after.
- For multi-step tasks, use a brief plan with a verification step for each stage.

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

### Service-time tracking (archived)
The old service-time daily pipeline (`service_time/daily_service_tracker.py` and its helpers) has been retired to `archive/service_time/` — it's no longer part of the live pipeline. Service-day tracking now happens via `scripts/archive/count_service_days.py` and `scripts/archive/update_combined_with_service_days.py` (also archived; check whether these are still invoked by `data_pipeline/smart_update_all.py` before assuming they run).

Important: several commands load JSON at import time (notably `commands/roster.py`), so after updating `data/*.json` you typically need to restart the bot process for changes to be reflected.

Note: the canonical Yahoo OAuth implementation lives in `data_pipeline/token_manager.py` (previously duplicated three ways — here, `data_pipeline/`, and `random/` — consolidated July 2026). The root-level `token_manager.py` is a thin compatibility shim that re-exports from `data_pipeline/token_manager.py` for scripts that import it from the repo root.

### Lint / typecheck
No linter/typechecker is configured in-repo (no Ruff/Black/Mypy config files).

### Tests
No automated test runner is configured. `tests/test_robust_parser.py` (CSV rank parser) and `scripts/test_auction.py` (full auction-week simulation against real `auction_manager`/`wb_ledger` modules) are the only real test-like scripts; a handful of other ad-hoc one-off scripts live in `scripts/archive/` (retired dev/debug utilities, not a test suite — e.g. the old `random/test_trade_logic.py`).

Examples (run one at a time):
```bash
source venv/bin/activate
python tests/test_robust_parser.py          # CSV rank parser
python scripts/test_auction.py              # full auction-week simulation
```

Note: as of July 2026 there's no automated coverage for the trade, KAP, PAD, or buy-in modules — the money/roster-moving business logic — which is a known gap, not an oversight.

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

Shared constants/helpers:
- `commands/utils.py`: maps manager/team <-> Discord IDs and contains trade timestamp helpers (`get_trade_dates`).

### Draft subsystem
The draft feature is split between Discord cogs and pure “state manager” code.

- `commands/draft.py`: Discord-facing draft orchestration.
  - Handles `/draft start|pause|continue|status|undo|order`.
  - Also listens to messages in the draft channel (picks are typed as plain messages).
  - Uses persisted state and a timer task (autopick fallback).
- `draft/draft_manager.py`: core draft state machine + persistence.
  - Reads draft order from `data/draft_order_2026.json`.
  - Persists per-draft state to `data/draft_state_{draft_type}_2026.json`.
- `commands/board.py` + `draft/board_manager.py`: manager draft boards.
  - Stores per-team ordered target lists in `data/manager_boards_2026.json`.
  - Draft autopick prefers the team’s board (`BoardManager.get_next_available(...)`).
- `draft/pick_validator.py`: rule validation logic (wired for use, but the Discord draft flow currently uses a lightweight confirmation flow).
- Additional narrative documentation for the draft system lives in `docs/archive/DRAFT_SYSTEM_HANDOFF.md` (historical, Dec 2024 snapshot) and the `docs/` directory.

### Prospect database & live pick tracking
Historically this used a dedicated Discord database channel, but that flow is
now deprecated in favor of the website’s prospect pages and the FastAPI draft
validation/state endpoints.

- `prospect_stats_repository.py`: pulls MLB Stats API data and combines it with
  ownership data and cached MLB IDs under `data/prospect_stats/`, which the
  website uses when building prospect lists.

### Prospect auction subsystem & web API
The prospect auction flow centralizes business rules in `auction_manager.py` and exposes them via both Discord slash commands and HTTP endpoints.

- `auction_manager.AuctionManager`: single source of truth for auction phases, bid validation, match/forfeit decisions, and persistence to `data/auction_current.json`. Also reads from `data/combined_players.json`, `data/wizbucks.json`, `data/standings.json`, and `config/season_dates.json`.
- `commands/auction.py`: Discord cog that wires `/auction` and `/bid` to `AuctionManager` and runs a background `auction_tick` task to send scheduled alerts based on the current auction phase.
- `health.py`: in addition to the health check, hosts the auction FastAPI endpoints (`/api/auction/current`, `/api/auction/bid`, `/api/auction/match`) protected by `BOT_API_KEY`. Successful bid/match calls both log to Discord and attempt a best-effort `git add/commit/push` of `data/auction_current.json` so downstream consumers (such as the web portal) can sync state.

### Data model: `data/` as the contract between scripts and bot
The bot expects JSON files under `data/` to exist and match the schema produced by pipeline scripts.

Most important files:
- `data/combined_players.json`: merged roster/contract/prospect view (Yahoo rosters + player metadata). Used by trade/roster/player lookup, KAP, and graduation logic.
- `data/wizbucks.json`: WizBucks balances. As of the in-season bot ledger becoming the source of truth, this is rebuilt from `data/wizbucks_transactions.json` via `wb_ledger.py`, not pulled from Google Sheets — see `data/README.md`-equivalent doc `docs/WIZBUCKS_WALLET_SYSTEM.md`.
- `data/standings.json`: standings snapshot used by `/standings`.
- `data/draft_order_2026.json`, `data/draft_state_*_2026.json`, `data/manager_boards_2026.json`: draft system persistence.
- `data/service_stats.json`: service-days snapshot used by `/service`/`/prospects`/`/alerts`.

### Data pipeline
Scripts in `data_pipeline/` populate the `data/` directory. `data_pipeline/smart_update_all.py` is the actual orchestrator that runs daily (via `.github/workflows/daily-update.yml`) — it picks a season-phase-aware execution path (see `docs/DATA_ORCHESTRATION.md`) rather than always running the same fixed sequence. The older `data_pipeline/update_all.py` still exists but is not what runs in production.

Notably, legacy Google Sheets-based WizBucks sync is disabled in-season — the bot's own transaction ledger (`wb_ledger.py` + `data/wizbucks_transactions.json`) is the source of truth, not a Sheets pull.

Other directories:
- `archive/`, `scripts/archive/`: retired one-off scripts and the old service-time tracking system — not part of the live pipeline. If you're trying to find where old `random/` or `service_time/` scripts went, they're in `scripts/archive/` and `archive/service_time/` respectively (both directories were cleaned up and consolidated July 2026).
