# Spec 03a: November 1 Top 100 Snapshot

**Status:** Ready to build
**Hard deadline:** Saturday, October 31, 2026 (the snapshot is taken November 1)
**Why it matters:** A Blue Chip prospect who is in MLB Pipeline's Top 100 on November 1 keeps his BC contract for free at the next PAD (Constitution Art. III). This list decides real WizBucks, so it has to be complete and right.
**Read first:** `WARP.md`, `data_pipeline/scrape_pipeline_top100.py`

## What exists today

- `monthly-pipeline-top100.yml` runs `scrape_pipeline_top100.py` at 6:00 AM UTC on the 1st of every month (2:00 AM ET), so it will fire on November 1 on its own.
- It scrapes the **stats** page (`mlb.com/prospects/stats/top-prospects`) and saves `data/pipeline_snapshots/YYYY-MM-DD.json`.

## Problems found in the September 1 snapshot

1. **94 of 100 players.** The stats page skips ranked prospects with no games this season (injured, not yet stateside). Any of them could be a BC prospect.
2. **Zero MLB IDs.** Every row has `mlb_id: null`, so the headshot ID extraction isn't working. Without IDs, matching to our players falls back to names, and we have duplicate names.
3. **No team.** The org column is a logo with no text, so it isn't captured.
4. Last year's November 1 list was built by hand (`data/historical/2025/Nov 1 2025 MLB Top 100 Prospects.csv`).

## What to build

1. **Scrape the rankings page, not the stats page.** `mlb.com/prospects` (the Top 100 list itself) has all 100 players, including those with no stats. Keep the stats scrape as a secondary source for the stats columns.
2. **Fix MLB ID extraction.** Pull the ID from the player link or headshot on the rankings page. Fail the run if fewer than 95 of 100 rows have an ID.
3. **Match to UPID by MLB ID**, falling back to name plus org only when the ID is missing. List anything unmatched.
4. **Save the snapshot in two places:**
   - `data/pipeline_snapshots/2026-11-01.json` as today.
   - The `top100_snapshots` table from Spec 01 (`snapshot_date = 2026-11-01`), if Neon is live by then. If not, the Spec 01 import picks it up from the JSON file.
5. **BC retention report** (`scripts/bc_retention_report.py`): every owned BC prospect, whether he's in the November 1 list, and the result: **Retained (free)**, **Must repurchase at PAD**, or **Already graduated**. Output as a CSV and a short Discord-ready text block.
6. **Freeze it.** The November 1 snapshot is marked `official = true` and can't be overwritten by a later run or by the monthly job. Corrections go through an admin override with a reason.

## November 1 checklist (Zach, about 15 minutes)

1. Confirm the workflow ran and the snapshot has 100 rows with IDs.
2. Spot-check 5 names against mlb.com/prospects, including #1, #100 and one prospect who hasn't played.
3. Run the BC retention report and skim the "Must repurchase" list for surprises.
4. Post the retention list in Discord so managers know before PAD.

**Fallback if the scrape fails:** copy the list from mlb.com/prospects into a CSV in the 2025 file's format and run `scripts/update_top100_prospects_from_csv.py` (it already exists), then the retention report.

## Acceptance criteria

- [ ] A manual run of the workflow before October 31 produces 100 rows, at least 95 with MLB IDs, all matched to UPIDs or listed as unmatched
- [ ] Retention report runs against that test snapshot
- [ ] The November 1 snapshot is marked official and the December 1 run doesn't overwrite it

## Open question

The Constitution says Top 100 on November 1. If MLB updates its list during the offseason (they usually re-rank in late January), the November 1 list still governs. Confirm that's the intent.
