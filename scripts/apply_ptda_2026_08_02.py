#!/usr/bin/env python3
"""Apply the 2026 Post-Trade Deadline Allotment (PTDA), per the FBP
Constitution (Article I, Section 02.1.4): "Installment 4 - PTDA
(Post-Trade Deadline Allotment, In-Season) - Based on Current Bracket
(Championship = $15, Consolation = $25, Elimination = $35)."

Bracket assignments (current standings, per Zach, 2026-08-02):
  Championship ($15): LFB, SAD, DMN, HAM
  Consolation  ($25): WIZ, TBB, JEP, B2J
  Elimination  ($35): CFL, WAR, RV, DRO

Credits data/wizbucks.json (keyed by full manager name) and appends one
ledger entry per team to data/wizbucks_transactions.json, using the
transaction_type="admin_adjustment" convention (per Zach's instruction --
matches the schema of the 22 existing admin_adjustment entries exactly:
same field shape, same metadata keys, same txn_id pattern
"wb_{TEAM}_admin_adjustment_{unix_ts}"). Bracket/installment context goes
in the human-readable `description`, not in `metadata`, so the entry stays
byte-for-byte structurally consistent with every prior admin_adjustment.

Idempotent: skips any team that already has an admin_adjustment entry
whose description contains "Post-Trade Deadline Allotment" (guards against
double-crediting on a re-run).

fbp-hub's copy of these files is a synced, read-only mirror (refreshed by
the existing scheduled/auto-sync workflow) -- intentionally not touched
here.

Run:
    python3 apply_ptda_2026_08_02.py --dry-run
    python3 apply_ptda_2026_08_02.py
"""

import json
import sys
import time
from datetime import datetime, timezone

WIZBUCKS_FILE = "data/wizbucks.json"
TRANSACTIONS_FILE = "data/wizbucks_transactions.json"
ADMIN = "zpressley"
SEASON = 2026

ABBR_TO_FULL = {
    "WAR": "Weekend Warriors",
    "WIZ": "Whiz Kids",
    "B2J": "Btwn2Jackies",
    "SAD": "not much of a donkey",
    "CFL": "Country Fried Lamb",
    "HAM": "Hammers",
    "LFB": "La Flama Blanca",
    "DMN": "The Damn Yankees",
    "DRO": "Andromedans",
    "JEP": "Jepordizers!",
    "RV": "Rick Vaughn",
    "TBB": "The Bluke Blokes",
}

BRACKETS = [
    ("Championship", 15, ["LFB", "SAD", "DMN", "HAM"]),
    ("Consolation", 25, ["WIZ", "TBB", "JEP", "B2J"]),
    ("Elimination", 35, ["CFL", "WAR", "RV", "DRO"]),
]

DESCRIPTION_MARKER = "Post-Trade Deadline Allotment"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    # NB: wizbucks_transactions.json's existing entries are stored with
    # escaped unicode (ensure_ascii=True) -- confirmed empirically against
    # HEAD (e.g. "DC → BC"), contra this session's earlier notes on the
    # other data files. Must match or every existing arrow/accent character
    # gets rewritten to a literal glyph, producing a huge spurious diff.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Apply 2026 PTDA (Post-Trade Deadline Allotment)\n" + "=" * 78)

    balances = _load(WIZBUCKS_FILE)
    transactions = _load(TRANSACTIONS_FILE)

    # Idempotency guard: which teams already have a PTDA entry?
    already_applied = {
        e.get("team") for e in transactions
        if e.get("transaction_type") == "admin_adjustment"
        and DESCRIPTION_MARKER in (e.get("description") or "")
    }

    # Verify every abbreviation covers exactly the 12 teams in wizbucks.json,
    # and every team appears in exactly one bracket -- fail loudly if not.
    all_abbrs = [a for _, _, abbrs in BRACKETS for a in abbrs]
    if len(all_abbrs) != 12 or len(set(all_abbrs)) != 12:
        print(f"ERROR: expected exactly 12 unique team abbreviations across brackets, got {len(all_abbrs)} "
              f"({len(set(all_abbrs))} unique). Aborting.")
        sys.exit(1)
    full_names_expected = set(balances.keys())
    full_names_from_abbrs = {ABBR_TO_FULL[a] for a in all_abbrs}
    if full_names_from_abbrs != full_names_expected:
        print(f"ERROR: bracket team list doesn't match wizbucks.json's team list.")
        print(f"  In wizbucks.json but not covered: {full_names_expected - full_names_from_abbrs}")
        print(f"  Covered but not in wizbucks.json: {full_names_from_abbrs - full_names_expected}")
        sys.exit(1)
    print("Guard OK: all 12 teams covered exactly once, matches wizbucks.json's team list.\n")

    applied, skipped = 0, 0
    new_entries = []
    base_ts = int(time.time())

    for bracket_name, amount, abbrs in BRACKETS:
        print(f"-- {bracket_name} bracket (+${amount}) --")
        for i, abbr in enumerate(abbrs):
            full_name = ABBR_TO_FULL[abbr]

            if abbr in already_applied:
                print(f"  {abbr:5} ({full_name}): PTDA entry already exists  [SKIP]")
                skipped += 1
                continue

            before = balances.get(full_name)
            if before is None:
                print(f"  {abbr:5} ({full_name}): NOT FOUND in {WIZBUCKS_FILE} — skipping  [SKIP]")
                skipped += 1
                continue

            after = before + amount
            print(f"  {abbr:5} ({full_name}): {before} -> {after}  [APPLY]")

            if not dry_run:
                balances[full_name] = after

            ts_unix = base_ts + applied + skipped  # simple uniqueness, matches existing txn_id convention
            ts_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            entry = {
                "txn_id": f"wb_{abbr}_admin_adjustment_{ts_unix}",
                "timestamp": ts_iso,
                "team": abbr,
                "amount": amount,
                "balance_before": before,
                "balance_after": after,
                "transaction_type": "admin_adjustment",
                "description": f"{DESCRIPTION_MARKER} (PTDA) - {bracket_name} bracket",
                "related_player": None,
                "metadata": {
                    "season": SEASON,
                    "installment": "admin",
                    "admin": ADMIN,
                    "source": "admin_portal",
                },
            }
            new_entries.append(entry)
            applied += 1

    print(f"\n{'=' * 78}")
    print(f"Teams credited: {applied}  |  skipped (already applied / not found): {skipped}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    transactions.extend(new_entries)
    _save(WIZBUCKS_FILE, balances)
    print(f"\nWrote {WIZBUCKS_FILE}")
    _save(TRANSACTIONS_FILE, transactions)
    print(f"Wrote {TRANSACTIONS_FILE}")


if __name__ == "__main__":
    main()
