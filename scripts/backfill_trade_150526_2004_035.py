"""Backfill for TRADE-150526_2004-035 (CFL / LFB), stuck since 2026-05-16.

Background
----------
CFL proposed: LFB sends $15 WizBucks to CFL; CFL sends Blake Snell
(upid 1887) to LFB. Both teams accepted the same day (manager_approved_at
2026-05-16), but the trade never got past admin_review — the admin-approve
click never completed (predates the Discord-message-tracking feature, so
there's no trackable card, just a trade stuck in admin_review).

Investigation turned up that BOTH legs already got resolved independently,
outside the formal trade portal, well before this backfill:

  1. Player leg: Snell (upid 1887) already shows LFB as owner. Git history
     confirms a legitimate player_log "In Season Add" entry
     (source=yahoo_roster_sync) on 2026-05-20 — the real Yahoo trade/roster
     move happened four days after both sides accepted here.

  2. WizBucks leg: data/wizbucks_transactions.json already has a manual
     admin_adjustment entry dated 2026-06-23 ("5/17/26 Trade w/ LFB for
     Blake Snell. WB did not update.", admin "_dunnce", +$15 to CFL) — a
     human admin already noticed the stuck WB transfer and manually
     credited CFL's side. There is no corresponding LFB debit anywhere in
     the ledger.

Given CFL has already been paid, applying the trade's original $15
LFB -> CFL transfer now would double-pay CFL. And debiting LFB $15 to
"balance the books" isn't this script's call to make unilaterally — LFB's
balance dropped to $5 in the ~2 months since, entirely through unrelated,
legitimate spending, so a debit now would just be an artifact of when this
backfill happens to run, not a reflection of what LFB actually owed at the
time. The prior admin's one-sided fix is left as-is.

So there is nothing left to actually apply — this script only marks the
trade record itself resolved (status "approved") with a data_applied_summary
documenting why, so it stops showing as stuck and the audit trail explains
the resolution.

Idempotent: guarded by the trade rec's data_applied_at.

Run:
    python3 scripts/backfill_trade_150526_2004_035.py --dry-run
    python3 scripts/backfill_trade_150526_2004_035.py
"""

import json
import sys
from datetime import datetime, timezone

TRADE_ID = "TRADE-150526_2004-035"
TRADES_FILE = "data/trades.json"
COMBINED_FILE = "data/combined_players.json"
WIZBUCKS_TXNS_FILE = "data/wizbucks_transactions.json"

ADMIN = "ADMIN_BACKFILL"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Backfill {TRADE_ID}\n" + "=" * 70)

    trades = _load(TRADES_FILE)
    rec = trades.get(TRADE_ID)
    if not rec:
        print(f"ERROR: {TRADE_ID} not found in {TRADES_FILE}")
        sys.exit(1)

    if rec.get("data_applied_at"):
        print(f"SKIP: {TRADE_ID} already has data_applied_at={rec['data_applied_at']} — nothing to do.")
        return

    if rec.get("status") != "admin_review":
        print(f"ERROR: {TRADE_ID} status is {rec.get('status')!r}, expected 'admin_review'. Aborting.")
        sys.exit(1)

    # Re-verify the player leg is really already resolved before touching anything.
    players = _load(COMBINED_FILE)
    snell = next((p for p in players if str(p.get("upid")) == "1887"), None)
    if not snell:
        print("ERROR: upid 1887 (Blake Snell) not found — aborting for manual review.")
        sys.exit(1)
    owner = str(snell.get("FBP_Team") or "").strip().upper()
    if owner != "LFB":
        print(f"ERROR: expected Snell already owned by LFB, found {owner!r} — reality has changed. Aborting.")
        sys.exit(1)
    print(f"  Blake Snell already LFB via Yahoo roster sync (2026-05-20)  [SKIP — no player move needed]")

    # Re-verify CFL's WizBucks side was already manually fixed.
    txns = _load(WIZBUCKS_TXNS_FILE)
    already_fixed = any(
        str(t.get("team", "")).upper() == "CFL"
        and t.get("transaction_type") == "admin_adjustment"
        and "snell" in str(t.get("description", "")).lower()
        for t in txns
    )
    if not already_fixed:
        print("ERROR: expected a prior CFL admin_adjustment referencing Snell — not found. Aborting for manual review.")
        sys.exit(1)
    print("  CFL WizBucks side already manually credited +$15 on 2026-06-23 (admin _dunnce)  [SKIP — no WB move needed]")
    print("  LFB was never debited the offsetting $15, and won't be by this script — LFB's balance has since")
    print("  dropped to $5 through unrelated spending; debiting now would just be an artifact of timing, not")
    print("  a reflection of what was owed in May. Leaving the prior admin's one-sided fix as-is.")

    now = _iso_now()
    rec["status"] = "approved"
    rec["admin_decision_by"] = ADMIN
    rec["processed_at"] = now
    rec["data_applied_at"] = now
    rec["data_applied_by"] = ADMIN
    rec["data_applied_summary"] = {
        "player_moves": 0,
        "player_log_entries": 0,
        "pick_moves": 0,
        "wb_transfers": 0,
        "buyins_purchased": 0,
        "warnings": [
            "Player leg (Snell, upid 1887) already resolved via Yahoo roster sync on 2026-05-20; not re-applied.",
            "WizBucks leg already one-sided-resolved via manual admin_adjustment to CFL on 2026-06-23 "
            "(admin _dunnce, +$15, 'WB did not update'); LFB was never debited and is not debited by this "
            "backfill either, since LFB's balance has since dropped below $15 through unrelated activity.",
            "Manually marked resolved 2026-07-22 after the fire-and-forget git-commit bug likely contributed "
            "to admin_approve never completing; see trade_store._maybe_commit fix.",
        ],
    }

    print("\n" + "=" * 70)
    print(f"Trade status -> approved (data_applied_at={now}), no data file changes beyond trades.json.")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    trades[TRADE_ID] = rec
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)

    print(f"\nWrote {TRADES_FILE}.")


if __name__ == "__main__":
    main()
