# combined_players.json Data Cleanse — 2026-08-02

*Read-only integrity scan. No fixes applied — diagnosis only. Script:
`scripts/data_cleanse_combined_players.py` (re-runnable any time).*

6,820 players scanned. Full raw output has more detail than this summary;
worth keeping the script around as a recurring check (Warp's root-cause
report from two days ago proposed exactly this).

## TL;DR

| # | Issue | Count | Owned players affected | Priority |
|---|---|---|---|---|
| 1 | Rostered players with **no UPID at all** | 6 records | 5 | **High** |
| 2 | Debuted MLB players stuck at `player_type: "Farm"` | 177 | 27 | **High** |
| 3 | Literal duplicate rows sharing one UPID | 2 pairs | 1 pair (both owned, same team) | Medium |
| 4 | "Shadow" duplicate of an owned player under a different name spelling | ~5 | 5 | Medium |
| 5 | `FBP_Team`/`manager` name inconsistencies | 3 | 3 | Low |
| 6 | Owned players missing `contract_type` | 15 | 15 | Low |
| 7 | Owned TC1 players with blank `status` field | 16 | 16 | Low (cosmetic) |
| 8 | `upid_database.json` team field stale vs. live data | 744 | unknown | Low / informational |
| 9 | Harmless duplicate rows, both sides unowned | ~13 | 0 | Cosmetic, optional cleanup |

Good news up front: no *new* instance of the "manager set but FBP_Team key
missing" bug (the exact defect that caused the Luis Garcia Jr. mess) turned
up anywhere else in the file, and no two teams currently both claim to own
the same real player — the ownership layer is otherwise clean.

---

## 1. Rostered players with no UPID at all — High priority

Six records have `"upid": ""` (blank). Five of them are actively owned:

| Name | Team | Owner | yahoo_id |
|---|---|---|---|
| Ivan Herrera | STL | B2J (Btwn2Jackies) | 11836 |
| Josh Smith | TEX | JEP (Jepordizers!) | 12562 |
| Luis Robert Jr. | NYM | LFB (La Flama Blanca) | 10765 |
| Michael Harris II | ATL | LFB (La Flama Blanca) | 12056 |
| Bobby Witt Jr. | KC | DMN (The Damn Yankees) | 11771 |
| Jake Odorizzi | TB | *(unowned)* | 9310 |

**Why this matters:** every trade, player-log entry, and lookup in this
codebase is keyed by UPID. A rostered player with no UPID can't be traded
through the normal portal (no ID to reference), and any code that builds a
`upid -> player` dict (which is most of it) silently drops or collides on
these. Each of the 5 owned ones also has a normal, fully-populated duplicate
row elsewhere under a slightly different name spelling (see #4 below) — e.g.
Ivan Herrera (blank upid, owned) vs. **upid 3513, "Iván Herrera"** (unowned).
The blank-upid row appears to be the one actually carrying current
ownership, which is backwards from every other player in the file.

**Recommended fix:** for each of the 5, assign the correct UPID (very likely
the sibling record's UPID, after confirming which row Yahoo/the roster sync
is actually treating as "the" owned one — same investigative pattern as the
Garcia 8696/8697 fix), then retire or merge the duplicate. Worth doing
carefully, one at a time, rather than in bulk.

## 2. Debuted MLB players stuck in Farm status — High priority

177 players have `player_type: "Farm"` despite having debuted in the majors
(`debuted: true` and/or a real `debut_date`). This is the exact pattern
McGreevy hit, but McGreevy was one of 177, not an isolated case. **27 of the
177 are on live rosters right now**, spread across 9 teams:

DRO (Nick Yorke, Ryan Ritter, C.J. Kayfus, Andrew Walters, Jonah Tong, Rece
Hinds), RV (Jordan Lawlar, Kristian Campbell), B2J (Moises Ballesteros, Luis
Morales, Denzer Guzman), HAM (Hunter Barco, Logan Henderson, Hurston
Waldrep, Jhostynxon Garcia), JEP (Owen Caissie, Carson Whisenhunt, Bryce
Eldridge, Dylan Beavers), LFB (Zac Veen), WIZ (Harry Ford), TBB (Mick Abel),
WAR (Jacob Melton, Chase DeLauter, Trey Yesavage), SAD (Troy Melton), plus
McGreevy (B2J, already fixed).

**Why this matters:** if graduation drives contract-type eligibility or
keeper cost the way it did for McGreevy, these 27 managers are likely
carrying the wrong contract terms on real roster spots right now. The other
150 are unowned prospects who've debuted but nobody's rostered — lower
urgency, but confirms Warp's suspicion that the graduation sweep is scoped
to owned players only (or has some other gap), not a one-off miss.

**Recommended fix:** worth Zach's sign-off on scope (matches the open item
in `TRADE_DATA_ISSUES_ROOT_CAUSE_FIXES_2026_07_31.md`), but the 27 owned
ones are a clear, bounded, high-value batch to fix first.

## 3. Literal duplicate rows sharing one UPID — Medium priority

Two UPIDs each have **two full array entries**, not just a naming quirk:

- **UPID 5996** — "Agustin Ramirez" (full stats: age, rank, contract,
  status) and "Agustín Ramírez" (fangraphs/bbref IDs, blank contract/status)
  — both MIA, both owned by DRO.
- **UPID 3825** — "Randy Rodriguez" (full stats, owned-looking fields) and
  "Randy Rodríguez" (fangraphs/bbref IDs, blank contract/status) — both SF,
  both unowned.

Looks like an enrichment pass (the one that adds `fangraphs_id`/`bbref_id`)
created a second row instead of updating the existing one, keyed by a
name-lookup that didn't recognize the accented spelling as the same person.

**Why this matters:** any `{upid: player}` dict built from this file (which
is how most of this codebase reads player data) silently keeps whichever of
the two comes later in the array and drops the other — meaning one of these
two records' data (the fuller one, in both cases observed) may already be
invisible to parts of the app.

**Recommended fix:** merge each pair into one row (keep the fuller record's
fields, add the fangraphs_id/bbref_id from the sparse one), delete the
duplicate.

## 4. Shadow duplicates of owned players, different name spelling — Medium

Beyond the 5 blank-upid cases in #1, at least a few more owned players have
an inactive "shadow" twin under a slightly different name format (missing
accent, missing Jr./II suffix, or punctuation), same real person confirmed
by shared `yahoo_id`/`mlb_id`:

- Jonathon Long (CHC, owned DMN, upid 7743) vs. unowned duplicate upid 7351
- Bobby Witt (KC, blank upid, owned DMN) vs. unowned "Bobby Witt Jr." dup
- Abimelec Ortiz (TEX, owned DMN, upid 6007) vs. unowned duplicate upid 8404

These don't currently cause a live conflict (the shadow side is always
unowned), but they're exactly the shape of bug that turned into the Garcia
mess once someone touches the wrong copy — worth cleaning up preventively
rather than waiting for the next one to bite.

## 5. FBP_Team / manager name inconsistencies — Low priority

Built an empirical FBP_Team → manager map from the other ~6,800 records and
found 3 exceptions:

- **UPID 7199, Josue Briceño** — `manager` is literally `"CFL"` (the team
  abbreviation) instead of `"Country Fried Lamb"`.
- **UPID 3816 (Michael Arroyo) and 6580 (Enrique Bradfield Jr.)** —
  `manager` is `"Jeppie Torrs"` instead of the other 42 JEP records'
  `"Jepordizers!"`. Ownership itself is correct either way (both map to
  team JEP); this is purely a display-name inconsistency, possibly a
  manager rename that didn't fully propagate.

## 6 & 7. Contract/status gaps on owned players — Low priority

15 owned players have `years_simple` set (mostly "TC 2") but blank
`contract_type` — full list in the raw script output. 16 owned players with
`contract_type: "Keeper Contract"` + `years_simple: "TC 1"` have a blank
`status` field instead of the usual `"[5] TC1"` — likely cosmetic (display
label), but flagging in case status feeds any scoring/UI logic.

*(Note: a much larger-looking number — 3,226 records with `years_simple`
set but no `contract_type` — is not a real issue. All but 15 of those are
unowned free-agent-pool players, where `years_simple` appears to just be a
pre-assigned draft/keeper tier, unrelated to actual contract status until
someone acquires them.)*

## 8. upid_database.json team field drift — Informational, likely low priority

744 UPIDs where `upid_database.json`'s `team` field disagrees with
`combined_players.json`'s `team` field (e.g., Juan Soto shows `NYM` in the
live data but `NYY` in the identity file). This is almost certainly just
staleness — `combined_players.json` gets updated by the daily roster sync,
while `upid_database.json` looks like a slower-moving identity/name-index
sheet (Warp's earlier note: it's meant to be regenerated from an external
master sheet, not hand-edited). Not touching it, but flagging because a
stale `team` field in an identity-matching table is the same general shape
of risk that contributed to the Garcia mismatch — worth a look if anyone
ever revisits `roster_sync.py`'s matching logic.

## 9. Harmless shadow duplicates, both sides unowned — cosmetic only

About 13 more name-spelling duplicate pairs where neither side is owned by
anyone (TJ White, JB Bukauskas, Marcos Castanon, Rafael Flores, Rodney
Green, Jiman Choi, Brady Ebel, Ethan Frey, Omar Serna, Mike Wright, and the
Luis Garcia/2898-2899 pitcher pair). No functional impact today — optional
cleanup, not urgent.

---

## Files

- `scripts/data_cleanse_combined_players.py` — the scan itself, safe to
  re-run any time (read-only, no writes).
- `data/combined_players.json`, `data/upid_database.json` — scanned, not
  modified.
