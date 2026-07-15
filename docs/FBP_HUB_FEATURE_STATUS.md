# FBP Hub Feature Status vs Original Plan

_As of 2026-01-09, with a July 2026 status update below_

> **July 2026 update:** Several items below marked 🟡/❌ as of January have since
> shipped and are confirmed live: the weekly auction portal (`auction.html`),
> KAP self-service contract purchases (`kap.html`, `kap/kap_processor.py`),
> PAD (`pad.html`), a web trade portal (`trade.html`, `api_trade.py`), a
> commissioner admin panel (`admin.html`, `api_admin_bulk.py`), and a draft
> picks page (`draft-picks.html`). Their specific sections below have been
> updated inline and marked with this same note. Everything else in this doc
> reflects January 2026 reality and has **not** been re-audited — treat
> remaining 🟡/❌ items as "last known status," not verified-current-as-of-July.

This document compares the original FBP Hub feature list (from the Claude plan) with what is actually implemented now across **fbp-hub** (website) and **fbp-trade-bot** (bot + data pipeline).

Status legend:
- ✅ **Complete** – implemented and in active use.
- 🟡 **Partial** – core pieces are in place, but flow is not fully wired or still missing polish.
- ❌ **Not implemented yet** – no meaningful implementation beyond maybe stubs.

---

## 1. "Complete Features" in the plan

### Player database with search/filters
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - `fbp-hub/players.html` + `js/players.js` implement full-text search, quick filters (keepers/prospects/my team), and advanced filters (position, MLB team, manager) over `combined_players.json`.

### Keeper roster displays
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - `rosters.html` + `js/rosters.js` render keeper depth charts by team, grouped by position buckets.
  - Uses `player_type === "MLB"` from `combined_players.json`.

### Prospect roster displays with contracts (FC/PC/DC)
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - Prospect view on `rosters.html` shows farm players with FC/PC/DC badges (derived from `years_simple`), plus summary counts.

### MLB stats integration (current season)
- **Planned status:** Complete
- **Actual:** ✅ **Complete (backend)**
  - `fbp-trade-bot` maintains `player_stats.json` from Yahoo/MLB stats.
  - Service-time and prospect database logic use current MLB stats; website isn’t a full stats front-end, but the core integration is there.

### Service time progress bars
- **Planned status:** Complete
- **Actual:** 🟡 **Partial**
  - Backend: service-time calculations and limit flags exist (`service_time/` plus `service_stats.json`).
  - Frontend: graduation logic in `transactions.js` uses stats to detect graduates, but there is not yet a fully polished, everywhere-visible “progress bar” UI.

### Daily data pipeline automation
- **Planned status:** Complete
- **Actual:** 🟡 **Partial**
  - Scripts like `data_pipeline/update_all.py` and `service_time/daily_service_tracker.py` orchestrate the full pipeline.
  - Execution is manual / via external scheduler; there’s no in-repo cron/worker wiring, but the pipeline itself is ready and in use.

### MLB ID mapping system
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - `data_pipeline/build_upid_database.py`, `mlb_id_cache.json`, `upid_database.json`, and `mlb_team_map.json` are implemented and used by `merge_players.py` to attach `mlb_id` and reconcile identities.

### Roster event logging
- **Planned status:** Complete
- **Actual:** 🟡 **Partial → Strong foundation**
  - Historical roster events are captured via the Player Log Google Sheet and the 4 History Book CSVs.
  - These are normalized into `data/transactions_history.json` and surfaced in the new `player-log.html`.
  - New JSON log `data/player_log.json` + `player_log.append_entry(...)` exist and the initial admin sync script is in place, but most bot flows are not yet writing into this log.

### Service days calculation
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - Service-day calculations and storage in `service_stats.json` are implemented and used by bot commands and graduation-eligibility checks.

### Flagging system for graduation candidates
- **Planned status:** Complete
- **Actual:** 🟡 **Partial**
  - Backend: service-time flags for exceeding MLB/FBP limits exist.
  - Frontend: `transactions.js` computes `eligibleGraduations` from service stats for the user’s prospects.
  - Still missing: end-to-end self-service action that actually updates rosters/contracts + logs the transaction through the bot.

### Player lookup command (bot)
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - Discord `/player` / `/lookup` commands are implemented and backed by `combined_players.json` via `commands/lookup.py`.

### Roster view command (bot)
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - Discord `/roster` command returns rosters per team, backed by `combined_players.json` and pipeline data.

### Standings command (bot)
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - `/standings` uses `data/standings.json` and is wired into the bot.

### Trade submission command (bot)
- **Planned status:** Complete
- **Actual:** ✅ **Complete**
  - `/trade` + `commands/trade_logic.py` implement trade submission and approval threads in Discord.

---

## 2. "Partially Complete Features" in the plan

### Transaction ledger
- **Planned status:** Partial
- **Actual:** 🟡 **Partial (two ledgers, good foundation)**
  - **WizBucks ledger**: `wizbucks.json` + `wizbucks_transactions.json` + `wizbucks.html` provide a working currency ledger with filters and balances. This part is effectively complete.
  - **Global player transaction log**: `transactions_history.json` (History Book) + `player_log.json` (new log) + `player-log.html` now give a unified historical view with season/owner/type/search filters.
  - **Still missing:** routine bot flows (trades, promotions, etc.) writing their own entries into `player_log.json` beyond the pending admin roster-sync script.

### Self-service transactions (graduations, DC slots)
- **Planned status:** Partial
- **Actual (July 2026 update):** ✅ **Complete** — `kap.html` + `kap/kap_processor.py` (backend) and `self_service/contract_purchase_processor.py` implement real contract-tier purchases (PC→BC, etc.) with WizBucks charges, fully wired through the Cloudflare Worker to `POST /api/manager/contract-purchase`, not UI-only anymore. (See `docs/KAP_BUYIN_INTEGRATION.md`.)

### 30-man roster compliance tracker
- **Planned status:** Partial
- **Actual:** ❌ **Not implemented (just a stub)**
  - `transactions.js` has a placeholder `check30ManCompliance()` and static messaging indicating Yahoo API integration is “coming soon”.
  - No real compliance logic or persistence yet.

### Trade submission (web alternative)
- **Planned status:** Partial
- **Actual (July 2026 update):** ✅ **Complete** — `trade.html` + `api_trade.py` + `trade/` implement a full web trade portal (propose/approve/execute), connected to the same Discord approval workflow. See `docs/archive/TRADE_TOOL_INTEGRATION.md` (the original build spec, now archived since the feature shipped).

---

## 3. "Not Implemented – Core Features" in the plan

### WizBucks installment tracking (PAD/PPD/APA)
- **Planned status:** Not implemented
- **Actual:** ❌ **Not implemented**
  - No `wizbucks_installments.json` or PAD/PPD/APA-specific logic exists yet.

### Keeper salary calculator with tax brackets
- **Planned status:** Not implemented
- **Actual:** ❌ **Not implemented**
  - There is a `salaries.html` page stub and some draft logic, but no full tax-bracket-aware calculator or persisted `keeper_salaries.json`.

### Draft pick tracker
- **Planned status:** Not implemented
- **Actual (July 2026 update):** ✅ **Complete** — `draft-picks.html` is live; picks are integrated into the trade portal (pick trading via `trade.html`) per `docs/KAP_DRAFT_PICK_CALCULATION.md`.

### Manager authentication (Discord OAuth)
- **Planned status:** Not implemented
- **Actual:** 🟡 **Partial → Working but can be hardened**
  - `auth.js`, `login.html`, and `callback.html` integrate with a Cloudflare Worker for Discord OAuth.
  - Auth is used to identify the manager/team in the dashboard and transactions pages.
  - Remaining work is mostly around production hardening and deeper authorization checks on sensitive actions.

### Photo upload tool (Wikimedia + manual)
- **Planned status:** Not implemented
- **Actual:** ❌ **Not implemented**
  - No `photo_queue.json`, upload UI, or pipeline exists yet.

---

## 4. "Not Implemented – Manager Actions" in the plan

### Personal draft boards
- **Planned status:** Not implemented
- **Actual:** 🟡 **Partial (backend)**
  - `data/manager_boards_2025.json` exists and is used by `draft/board_manager.py` + `/board` commands to manage per-team boards.
  - Web UI for viewing/editing personal boards is not yet built.

### Keeper deadline form (salary/IL tag decisions)
- **Planned status:** Not implemented
- **Actual:** ❌ **Not implemented**
  - No dedicated web form or JSON (`keeper_decisions_2026.json`) yet; decisions are still managed via sheets/Discord.

---

## 5. "Not Implemented – Advanced Features" in the plan

These remain broadly **not implemented** beyond low-level scaffolding:

- Weekly prospect auction portal – ✅ **Complete (July 2026 update)** — `auction.html` provides the web portal UI on top of the already-implemented `auction_manager.py`/`/auction`/`/bid` backend.

- Salary planning simulator – ❌
- IL tag management – ❌ (no `il_tags.json` yet)
- Year reduction tool (RaT) – ❌
- Live draft mode – 🟡 (draft manager and Discord flows exist; no full web “live draft” UX).
- Commissioner admin panel – ✅ **Complete (July 2026 update)** — `admin.html` + `api_admin_bulk.py` (bulk graduate, bulk contract update, bulk release, player search/CSV export) is a consolidated web panel.

---

## 6. "Not Implemented – Cool Additions" in the plan

All still outstanding:

- Team pages with history – ❌
- Player history pages (ownership timeline) – ❌ (Player Log groundwork exists; not wired into per-player pages).
- Constitution with Article search (Discord command) – ❌

---

## 7. "Not Implemented – Missing High-Priority Features" in the plan

### Service time alerts command
- **Actual:** ❌ Not implemented as a dedicated command, though data exists.

### Prospect graduation command
- **Actual:** ❌ Not implemented as a one-shot `/graduate` command; logic is partially present in UI and service-time flags.

### DC purchase command
- **Actual:** ❌ Not implemented as its own command; only UI stubs exist.

### 30-man compliance command
- **Actual:** ❌ Not implemented.

### Admin rankings upload
- **Actual:** ❌ Not implemented (no `pipeline_rankings.json`).

### AI Rules Assistant (RAG)
- **Actual:** ❌ Not implemented.

---

## 8. "Not Implemented – Website Features" in the plan

### Static website foundation
- **Planned status:** Not implemented
- **Actual:** ✅ **Complete**
  - Multi-page static site (`index`, `players`, `rosters`, `dashboard`, `wizbucks`, `auction`, `transactions`, `player-log`, etc.) is in place with consistent styling and nav.

### Authentication system
- **Planned status:** Not implemented
- **Actual:** 🟡 **Partial**
  - Discord OAuth via Cloudflare Worker and `auth.js` is implemented and used by dashboard + transactions.
  - Still needs more guardrails and integration into all sensitive flows.

### Manager dashboard
- **Planned status:** Not implemented
- **Actual:** ✅ **Complete (first version)**
  - `dashboard.html` shows team-specific stats, quick actions, and a compact roster depth chart driven by `combined_players.json` and auth.

### Web-triggered bot actions
- **Planned status:** Not implemented
- **Actual:** ❌ **Not implemented**
  - No end-to-end flow where a web action directly calls a worker/API that then drives the Discord bot. All “actions” are still Discord-first.

---

## 9. "Not Implemented – Data Files Needed" in the plan

Below compares the **named files** from the original list vs what actually exists.

- `data/wizbucks_installments.json` – ❌ **Missing**
- `data/wizbuck_transactions.json` – 🔁 **Name mismatch / superseded**
  - We have `data/wizbucks_transactions.json` (note the extra `s`), which backs the WizBucks ledger.
- `data/keeper_salaries.json` – ❌ Missing
- `data/draft_tax.json` – ❌ Missing
- `data/il_tags.json` – ❌ Missing
- `data/draft_picks.json` – 🟡 **Partial**
  - Exists in `fbp-hub/data/draft_picks.json` (frontend only); there is no authoritative `data/draft_picks.json` in `fbp-trade-bot` yet.
- `data/draft_buyins.json` – ❌ Missing
- `data/draft_boards.json` – 🟡 **Partial**
  - `data/manager_boards_2025.json` exists and is used by the draft board manager.
- `data/transactions.json` – 🔁 **Superseded**
  - Replaced by:
    - `data/transactions_history.json` (normalized history from sheets/CSVs)
    - `data/player_log.json` (new append-only log for bot-driven updates)
- `data/photo_queue.json` – ❌ Missing
- `data/auction_current.json` – 🟡 **Partial**
  - `auction_manager.py` maintains current state and FastAPI endpoints exist; a dedicated JSON in `fbp-trade-bot/data` is not yet standardized.
- `data/auction_history.json` – ❌ Missing
- `data/keeper_decisions_2026.json` – ❌ Missing
- `data/current_stats.json` – 🔁 **Functionally covered**
  - We instead maintain `player_stats.json` and service-time related stats; there is no single unified `current_stats.json` file.
- `data/26man_compliance.json` – ❌ Missing
- `data/pipeline_rankings.json` – ❌ Missing

Additional data files that now exist but were not in the original "needed" list:

- `data/combined_players.json` – robust, UPID/MLB/Yahoo-aware merged player view.
- `data/upid_database.json`, `data/mlb_team_map.json`, `data/mlb_id_cache.json` – identity/MLB mapping.
- `data/wizbucks.json`, `data/wizbucks_transactions.json` – currency state + ledger.
- `data/roster_events.json`, `data/roster_snapshots/*.json` – roster event/snapshot scaffolding.
- `data/transactions_history.json`, `data/player_log.json` – new global transaction history + log.

---

## 10. Summary

- Many items originally flagged as "Not Implemented" (static website, dashboard, transaction history, some data files) are now **fully or largely complete**.
- The biggest remaining gaps are:
  - Wiring **self-service actions** (graduations, DC, compliance) and **web-triggered workflows** all the way through the bot and logs.
  - Implementing the keeper salary/IL/tag ecosystem and draft-related financial tools.
  - Building the more advanced/"cool" UX layers (team pages, player history views, admin console, auction portal UI).
- The **data foundation** (combined players, UPID/MLB IDs, stats, WizBucks, transaction history) is in good shape; most new features can now be layered on top of this without needing large new pipelines.