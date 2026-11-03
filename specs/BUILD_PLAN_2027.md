# 2027 rebuild: build plan and status

Written 2026-11-03 from the specs in this directory. Status is kept here, not in the specs (the specs are the source of truth for *what*; this file tracks *where we are*). Flag anything wrong to Zach.

## Where things stand

| Spec | Status | What exists | What's left |
|---|---|---|---|
| 03a Top 100 snapshot | Code done, waiting on a live run | `data_pipeline/scrape_pipeline_top100.py` (rankings page, MLB IDs, UPID match, official freeze), `scripts/bc_retention_report.py`, workflow inputs, unit tests | Run the workflow on this branch with `official=false`, check the log, then re-run with `snapshot_date=2026-11-01`, `official=true`. The Nov 1 scheduled run produced 96 rows and 0 IDs, so the file on `main` is not usable as the official list. |
| 01 Neon foundation | Built and verified on a local Postgres 16 | `db/migrations/0001_init.sql`, `scripts/db/{migrate,import_json,import_history,reconcile}.py`, `db/README.md`, `tests/db`, daily workflow step | Zach: create the Neon project, add `DATABASE_URL` and `DATABASE_URL_DIRECT` secrets, run migrate + import once against `main`, then watch the daily run pass 3 days in a row. |
| 03 Rosters, contracts, WizBucks | In progress (schema + actions + tests) | migration `0003`, `league/actions.py`, `scripts/db/cutover_contracts.py`, `tests/league` | Bot/API wiring, nightly prospect update from the MLB Stats API, phase-close and installment jobs on the bot scheduler, team page. Cutover Dec 15 needs Zach's sign-off on the conflict list and the 12 opening balances. |
| 02 Auction portal | In progress (pure rules) | `auction/rules.py`, unit tests | migration `0002`, `auction/service.py` (transactions + advisory lock), FastAPI routes made read-only on GET, Worker header `X-FBP-Team`, `js/auction.js` reading the API, delete `auction_manager.py` / `resolve-auction.yml` at go-live. Mock week on the `dev` branch in early March. |
| 05 Trade portal | Not started | | After 03. Migration `0005`, `trade_service.validate/approve`, new builder UI, admin queue, 2026 trade reshape. Live by the KAP trade window (~Feb 17). |
| 04 Draft room | Not started | | After 03 (needs `prospect_draft_slots`) and 02 (same patterns). Migration `0004`, `draft_pool()` SQL function, `draft_service.make_pick`, clock runner in the bot, website draft room, simulator. Ready to test Jan 15. |
| 07 Team page, lineups | Not started | | After 03. Migration `0007`, `position_eligibility` nightly job, `lineup_service`, team page. Feb 28. |
| 06 Preseason ops (PAD/KAP/APA) | Spec not written | | Needs a spec first. Several Spec 03 actions (`buy_draft_slot`, `keep_player`, `il_tag`, `reduce_a_tier`) are the building blocks. |
| 08 Scoring engine | Spec not written | | Needs a spec first. Spec 07's `lineups` and `mlb_games` tables are its input. |

## Order of work from here

1. **Finish 03a**: the two workflow runs above, then post the retention report in Discord.
2. **Neon live for 01** (Zach, ~10 minutes, see `db/README.md`): project `fbp`, branches `main` + `dev`, role `fbp_app`, both connection strings as repo secrets. The daily workflow does the rest. Nothing in the bot or website reads the database yet, so this is zero-risk.
3. **03 to the point of cutover readiness** (Nov 30): actions and tests passing, cutover script producing a clean conflict list, then wire the bot and API to the actions behind a feature flag so the JSON path stays the default until Dec 15.
4. **02 service + routes** (Dec 20), tested on a Neon branch with the local tests moved to run against it.
5. **05, 04, 07** in that order through January and February, each on its own worktree + Neon branch per `db/README.md`.

## How each spec gets built

Per `specs/README.md`: one worktree and one Neon branch per area, plan mapped to the spec's numbered tests, build, run the tests, PR, check against the acceptance criteria. Smaller well-bounded pieces (pure rule ports, loaders, report scripts) go to cheaper models in parallel; schema and transaction design stay with the lead.

## Decisions made while building 01 (report, not rules)

These are implementation choices where the data did not match the spec. None change league rules.

- **`players.mlb_id` is not unique** in v1. `combined_players.json` has 61 duplicated MLB IDs (one is Ohtani's two rows, the rest look like duplicate player stubs). Reconcile lists them. Add the unique constraint once they are cleaned.
- **`wb_ledger` txn ids**: 13 rows have no id and 3 ids are duplicated. Missing ids are generated (`gen_<sha1>`), duplicates get a `#n` suffix, and the original is kept in `source_txn_id`. Reported on every run.
- **`FBP_Team` is `null` on 30 rows**, not the string `"None"` the spec describes. Treated as blank and reported as `fbp_team_none`.
- **manager vs FBP_Team**: with the `Jeppie Torrs -> JEP` alias, Arroyo and Bradfield agree. The two real disagreements are Michael Brantley and Wander Franco (`manager = PLAY`, a 2024 team, `FBP_Team` blank). Both import with `team_abbr` null; reconcile prints their latest player events as evidence.
- **Historical teams** from `team_mappings_<year>.json` are inserted into `teams` with `active = false` so historical rosters and standings can reference them. Where the mapping file's abbreviation collides (`THE ` names two 2015/2016 teams) an abbreviation is synthesized from the name and flagged. 67 owner names in `transactions_history.json` match no mapping file; they stay as `owner_raw` with `owner_abbr` null.
- **2025 Yahoo roster key `I'LL`** was paired with JEP as the only unclaimed mapping team. Flagged as `history_roster_key`; Zach should confirm.
- **`data/historical/2025/top100_prospects.json`** has no distinct commit date, so it is stored as `2025-12-31` and flagged.
- The Nov 1 2025 CSV row for **Leo De Vries** did not match a UPID by name (likely an accent or spelling difference); the row is imported with `upid` null and flagged.

## Open items for Zach

1. Neon project + secrets (step 2 above).
2. Run the scraper workflow twice as described under 03a, spot-check 5 names, post the retention report.
3. Confirm the Nov 1 list governs even after MLB's January re-rank (Spec 03a open question).
4. Confirm the `I'LL -> JEP` pairing and whether the 61 duplicate MLB IDs should be cleaned in `combined_players.json` before cutover.
5. Spec 03 cutover inputs on Dec 15: the contract conflict list and the 12 opening balances.
