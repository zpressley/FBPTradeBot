# combined_players.json Data Cleanse — 2026-08-02

*Read-only integrity scan. No fixes applied — diagnosis only. Script:
`scripts/data_cleanse_combined_players.py` (re-runnable any time).*

6,820 players scanned. Full raw output has more detail than this summary;
worth keeping the script around as a recurring check (Warp's root-cause
report from two days ago proposed exactly this).

## TL;DR

| # | Issue | Count | Owned players affected | Priority |
|---|---|---|---|---|
| 1 | Rostered players with **no UPID at all** | 6 records | 5 | ~~High~~ **Fixed 2026-08-02** |
| 2 | ~~Debuted MLB players stuck at `player_type: "Farm"`~~ — **retracted, not a bug** | — | — | — |
| 3 | Literal duplicate rows sharing one UPID | 2 pairs | 1 pair (both owned, same team) | ~~Medium~~ **Fixed 2026-08-02** |
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

## 1. Rostered players with no UPID at all — Fixed 2026-08-02

Six records had `"upid": ""` (blank). Five were actively owned:

| Name | Team | Owner | yahoo_id |
|---|---|---|---|
| Ivan Herrera | STL | B2J (Btwn2Jackies) | 11836 |
| Josh Smith | TEX | JEP (Jepordizers!) | 12562 |
| Luis Robert Jr. | NYM | LFB (La Flama Blanca) | 10765 |
| Michael Harris II | ATL | LFB (La Flama Blanca) | 12056 |
| Bobby Witt Jr. | KC | DMN (The Damn Yankees) | 11771 |
| Jake Odorizzi | TB | *(unowned)* | 9310 |

**Root cause, confirmed via player_log.json:** all 5 owned players were
dropped by their respective managers at the *identical* timestamp
(2026-03-13T10:42:53, microseconds apart — one automated batch event, not
five manager decisions), then re-added by the **same manager who'd just
dropped them**. The re-add's Yahoo-name match failed against the existing
rich UPID record (Ivan vs Iván, missing Jr./II suffixes) and created a
disconnected, ownership-only stub instead of re-linking to the original
UPID. `upid_database.json`'s name index already resolved every one of
these names to the correct original UPID, so only the roster-sync's own
matching missed it — this wasn't a duplicate-identity problem the way
Garcia was, just a failed re-link.

**Fix applied** (`scripts/fix_duplicate_rows_and_upid_stubs_2026_08_02.py`):
transplanted `FBP_Team`/`manager` from each stub onto the original rich
UPID record, then deleted the stub. Zach's call: kept each record's
existing `contract_type`/`status`/`years_simple` as-is rather than
resetting — the same manager reclaiming the same player right after a
forced drop reads as a sync glitch, not a new pickup. Odorizzi (unowned
both sides) just had his `team` field updated to current (TEX → TB).
Verified: player count 6,820 → 6,812, zero unexpected diffs elsewhere.

## 2. "Debuted but still Farm" — retracted, this was not a bug

**Correction (Zach, 2026-08-02): player_type is driven by graduation, not by
whether a player has debuted.** The original version of this section flagged
177 players (27 owned) as a "graduation gap" purely because they'd appeared
in an MLB game while still `player_type: "Farm"`. That premise was wrong.

Per the FBP Constitution (Article 2, Section 05) and the actual graduation
pipeline (`data_pipeline/graduate_prospects_2025.py`), a prospect only
graduates once they exceed **FBP Prospect Limits** — 350 career PA, or 100
IP / 30 pitching appearances, or turning 26 — evaluated most strictly at
the in-season graduation deadline (MLB All-Star break). Simply debuting
(even a brief call-up) doesn't come close to those thresholds, so a player
can correctly stay a Farm/prospect-contract asset for a full season or more
after their first MLB appearance. My check conflated "has debuted" with
"graduation-eligible," which isn't the same thing.

**Verified this is a non-issue:** `data/graduation_eligible.json` is an
existing, properly-computed snapshot (per the real PA/IP/age rule, dated
2026-01-12) of every player who *actually* met the graduation threshold at
that point — 203 players. Cross-checked all 203 against current
`combined_players.json`: **every single one has already been correctly
graduated to `player_type: "MLB"`. Zero still sitting in Farm.** McGreevy's
contract fix earlier this session was unrelated to graduation eligibility
(he doesn't appear in the 203) — his `player_type` staying "Farm" was
correct, not a leftover bug, and shouldn't be changed.

No action needed here. Apologies for the bad steer in the original report —
leaving this section in place with the correction rather than deleting it,
so the reasoning is visible if this comes up again.

## 3. Literal duplicate rows sharing one UPID — Fixed 2026-08-02

Two UPIDs each have **two full array entries**, not just a naming quirk:

- **UPID 5996** — "Agustin Ramirez" (full stats: age, rank, contract,
  status) and "Agustín Ramírez" (fangraphs/bbref IDs, blank contract/status)
  — both MIA, both owned by DRO.
- **UPID 3825** — "Randy Rodriguez" (full stats, owned-looking fields) and
  "Randy Rodríguez" (fangraphs/bbref IDs, blank contract/status) — both SF,
  both unowned.

`scripts/enrich_sfbb_ids.py` (the only code that touches
`bbref_id`/`fangraphs_id`/`fangraphs_name`) turned out not to be the direct
cause — it only fills blanks on an already-matched existing record and
never appends a new one. But its own upid/mlb_id-keyed lookup dicts
silently collapse a pre-existing duplicate to whichever row comes last, so
once these duplicates existed (origin unconfirmed — git history on this
frequently-reformatted file didn't cleanly pin it down), that script only
ever "saw" and enriched one side, entrenching the split rather than fixing
or causing it.

**Why this mattered:** any `{upid: player}` dict built from this file
(which is how most of this codebase reads player data) silently keeps
whichever of the two comes later in the array and drops the other —
meaning one of these two records' data (the fuller one, in both cases
observed) was already invisible to parts of the app.

**Fix applied** (`scripts/fix_duplicate_rows_and_upid_stubs_2026_08_02.py`):
merged each pair into one row (kept the fuller record's fields, copied over
the `bbref_id`/`fangraphs_id`/`fangraphs_name` fields from the sparse one),
deleted the duplicate.

## 4. Shadow duplicates of owned players, different name spelling — Medium

At least a couple more owned players have an inactive "shadow" twin under a
slightly different name format (missing accent, punctuation, etc.), same
real person confirmed by shared `yahoo_id`/`mlb_id`, **not yet fixed**:

- Jonathon Long (CHC, owned DMN, upid 7743) vs. unowned duplicate upid 7351
- Abimelec Ortiz (TEX, owned DMN, upid 6007) vs. unowned duplicate upid 8404

(Bobby Witt's shadow pair was actually the blank-upid stub pattern from #1,
not this one — fixed as part of that batch, not still open here.)

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
