# 2027 rebuild: build plan and status

Written 2026-11-03 from the specs in this directory. Status is kept here, not in the specs (the specs are the source of truth for *what*; this file tracks *where we are*). Flag anything wrong to Zach.

## Where things stand

| Spec | Status | What exists | What's left |
|---|---|---|---|
| 03a Top 100 snapshot | Done. `data/pipeline_snapshots/2026-11-01.json` is the official list (100 rows, 100 MLB IDs, 89 matched to UPIDs, scraped 2026-11-03 since the Nov 1 run had 96 rows and no IDs; `scraped_at` and the override reason are inside the file) | scraper (rankings table, MLB IDs, UPID match by ID then name+org then unique name), `scripts/bc_retention_report.py`, workflow inputs, unit tests, `scripts/output/bc_retention_2026-11-01.*` | Zach: spot-check 5 names, add the 11 unmatched prospects (this year's draft class plus a duplicate Brady Ebel row) to `combined_players.json` if any is owned, post the Discord block. Retention result: 49 retained, 25 must repurchase, 18 already graduated. |
| 01 Neon foundation | Built and verified on a local Postgres 16 | `db/migrations/0001_init.sql`, `scripts/db/{migrate,import_json,import_history,reconcile}.py`, `db/README.md`, `tests/db`, daily workflow step | Zach: create the Neon project, add `DATABASE_URL` and `DATABASE_URL_DIRECT` secrets, run migrate + import once against `main`, then watch the daily run pass 3 days in a row. |
| 03 Rosters, contracts, WizBucks | Database layer built, reviewed and hardened; tests 1-12 plus hardening tests pass | migration `0003` (live ledger with append-only trigger, sign-by-kind check and a deferred no-overdraw trigger; rules tables; contracts; `league_windows` gating KAP/PAD/BC-finalization actions), `league/actions.py`, `scripts/db/cutover_contracts.py` (resolutions file, no guessing), `tests/league` | Bot/API wiring, nightly prospect update from the MLB Stats API, phase-close and installment jobs on the bot scheduler, roster-violation admin queue, team page. Cutover Dec 15 needs Zach's resolutions file for every contract conflict and the 12 opening balances (`--confirm-balances`). |
| 02 Auction portal | Rules and service built; service tests 1-7 pass against a local Postgres | migration `0002`, `auction/rules.py`, `auction/service.py`, tests | FastAPI routes made read-only on GET, Worker header `X-FBP-Team`, `js/auction.js` reading the API, scheduler calling `resolve_week`, delete `auction_manager.py` / `resolve-auction.yml` at go-live. Mock week on the `dev` branch in early March. |
| 05 Trade portal | Not started | | After 03. Migration `0005`, `trade_service.validate/approve`, new builder UI, admin queue, 2026 trade reshape. Live by the KAP trade window (~Feb 17). |
| 04 Draft room | Not started | | After 03 (needs `prospect_draft_slots`) and 02 (same patterns). Migration `0004`, `draft_pool()` SQL function, `draft_service.make_pick`, clock runner in the bot, website draft room, simulator. Ready to test Jan 15. |
| 07 Team page, lineups | Database layer built; spec tests 1-11 pass | migration `0007` (slot types, position eligibility, lineups with versions and locks, mlb_games, mlb_roster_status, league_settings), `lineups/service.py`, `lineups/mlb_api.py` (never run live from here), `tests/lineups` | Scheduler jobs (carryover at midnight ET, game locks, eligibility and roster-status refresh from the Stats API), team page UI, switch `league.actions.roster_violations` to read `position_eligibility`. Feb 28. |
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

Rule questions raised by the Spec 03 / Spec 02 code review (the code does the simplest reading; none of these were guessed silently):

6. **Season-boundary rollover.** The Constitution says "up to $25 per installment from the prior season (max $75)". The current `wb_rollover_caps` rows cap the whole carried balance at $25 into each of PAD, KAP and APA, which cannot express "per installment". How should leftover money be attributed to installments?
7. **DC to PC after an MLB debut.** `upgrade_prospect` allows DC to PC for $5 at any time; after a debut the same move is the $15 debut fee. Should the $5 upgrade be refused once `mlb_debut` is set?
8. **Auction affordability drop order.** When several teams are over budget at resolution, the code handles the worst-standings team first and drops its latest win (the OB's time for OB wins, the winning CB's time for CB wins). Confirm, since it can change who wins.
9. **Auction Friday 9pm to Saturday midnight.** The spec's phase table has no phase there; the code treats it as closed (no bids).
10. **Auction win contract.** Spec 02 says tier `P`; Spec 03 says auction wins are PC prospect contracts. The code opens a PC prospect contract.
11. **Two-way players** (one contract, two roster spots) are not modelled yet; `classify()` counts a two-way player as a pitcher.
12. **IL and NA at cutover.** The mirror has no slot data, so every keeper starts `active` and every prospect `farm`; IL/NA get set by hand after cutover.
