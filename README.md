# FBP Trade Bot

Backend for **Fantasy Baseball Pantheon (FBP)**, a Yahoo Fantasy Sports dynasty
league. This repo is the Discord bot + API that runs the league's economy —
keeper contracts, prospect auctions, trades, buy-ins, and the WizBucks
in-league currency — and syncs data to the [fbp-hub](../fbp-hub) website.

## What this is

A single Python process (`health.py`) that runs two things together:

- A **discord.py bot** — slash commands for trades, auctions, KAP (keeper
  contract purchases), PAD, rosters, standings, and admin tools. Cogs live in
  `commands/`.
- A **FastAPI server**, running in a background thread inside the same
  process — the API that [fbp-hub](../fbp-hub) (the website) calls for
  everything from viewing rosters to submitting a trade. Route handlers are
  split across `api_*.py` files at the repo root and included into `health.py`
  as routers.

`bot.py` also exists as a **Discord-only entrypoint** for local development
without the API server. `health.py` is what actually runs in production.

## Data model

There's no database — league state lives entirely in flat JSON files under
`data/` (rosters, contracts, WizBucks balances, transaction history, auction
state, etc.), and **git commits are the persistence layer**: every write goes
through a load → mutate → save cycle, then gets committed and pushed. All
of those read-modify-write cycles are expected to hold `data_lock.py`'s
`DATA_LOCK` for their full duration, since the bot and API share the same
process and can race on the same files.

Real MLB roster/stats data comes from Yahoo Fantasy Sports' API via OAuth2
(`data_pipeline/token_manager.py` is the canonical token handler; the
root-level `token_manager.py` is a compatibility shim for scripts that import
it from the repo root). `data_pipeline/smart_update_all.py` is the daily
pipeline that keeps rosters, stats, and standings current.

## Key subsystems

| Directory | What it does |
|---|---|
| `commands/` | Discord bot cogs (trade, auction, draft, roster, standings, lookup, etc.) |
| `trade/` | Trade proposal/approval/execution logic |
| `kap/` | Keeper contract tiers and graduation/advancement rules |
| `pad/` | PAD (prospect add/drop) processing |
| `buyin/` | Season buy-in cost calculation and tracking |
| `self_service/` | Manager-initiated actions from the website (contract purchases, etc.) |
| `draft/` | Keeper/prospect draft board and pick management |
| `data_pipeline/` | Daily/scheduled jobs — Yahoo sync, standings, backups |
| `admin/`, `api_admin_bulk.py` | Admin tools (bulk graduate, bulk contract updates, releases) |
| `docs/` | Deeper reference docs, including the league Constitution |

The league's actual rules (contract types, graduation thresholds, keeper
tiers, draft structure) are documented in
`docs/{Master} FBP Constitution 2026 (1).md` — that file is the source of
truth for *why* the code does what it does, and is fetched live by
fbp-hub's `constitution.html` rather than copy-pasted, so it can't drift out
of sync with what managers see on the site.

## Running locally

```bash
pip install -r requirements.txt
python3 health.py     # bot + API together (matches production)
# or
python3 bot.py         # Discord bot only, no API server
```

Requires a `.env` with `DISCORD_TOKEN`, `BOT_API_KEY`, and the Yahoo OAuth
credentials (`YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REDIRECT_URI`).

## Deployment

Runs on **Railway**, configured via `nixpacks.toml` (`python3 health.py` as
the start command). Railway redeploys automatically on every push to `main`
— which also means every automated git-commit-and-push from the bot itself
(hourly standings, daily data updates, etc.) triggers a redeploy.

## Where to look next

- `docs/` — architecture, data orchestration, KAP/WizBucks/buy-in flows, and
  `docs/archive/` for point-in-time build/incident logs that are historical
  rather than living documentation.
- `WARP.md` — a more detailed structural walkthrough of the codebase.
- [`fbp-hub`](../fbp-hub) — the website this backend serves.
