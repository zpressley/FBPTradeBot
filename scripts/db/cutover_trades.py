"""CUTOVER SCRIPT for Spec 05 (trade portal). Run once, by Zach, after the final Spec 01 import.

Reshapes the 2026 trades that Spec 01 mirrored into `trades` / `trade_transfers` (95 of them) into the live
tables `trades_live`, `trade_parties`, `trade_items` and `trade_comments`, all in ONE transaction:

  python scripts/db/cutover_trades.py --dry-run     # does everything, prints the plan, then rolls back
  python scripts/db/cutover_trades.py               # the same, and commits

  * ids: T26-0001, T26-0002, ... in created_at order. The old id (WEB-TRADE-..., TRADE-..., MANUAL-...) is kept
    in raw.legacy_trade_id. trade_sequences for 2026 is set to the next free number.
  * source 'import'. raw keeps the whole 2026 record.
  * statuses: approved -> approved, rejected / admin_rejected -> rejected, withdrawn -> withdrawn,
    expired -> expired. Anything else stops the script. A mirror row that maps to pending (pending) or
    accepted (admin_review) is live work: it is NOT imported (an imported 'accepted' trade would sit in the
    admin queue and could be approved without ever being applied to 2027 data). The script lists those rows
    and Zach decides what to do with them (resend in the portal, or reject by hand). Same for 2026 rows
    that are still open at cutover: there should be none, the window closed July 31.
  * approved_by: the team abbreviation when admin_decision_by is one; "zpressley", "ADMIN", "ADMIN_BACKFILL" and
    blank all become 'ADMIN' (the original stays in raw). approved_at = processed_at, else data_applied_at, else
    manager_approved_at, else created_at (the plan lists the trades that needed the last fallback).
  * rejected: closed_by = rejected_by, close_note = rejection_reason. withdrawn: closed_by = the initiator.
    expired: closed_by 'system'.
  * items: player -> player, wizbucks -> wb, draft_pick -> pick with draft_id / overall NULL and the 2026
    transfer in trade_items.raw (the 2026 draft order is not in draft_slots, so there is no row to point at).
  * a party's accepted_at: the initiator at created_at, any other accepting team at manager_approved_at.
    On an approved trade a party that is missing from raw.acceptances still gets an accepted_at (every party
    had to agree for it to be approved): manager_approved_at, else approved_at. The plan counts those.

Refuses to write if trades_live already has rows. Does not touch the mirror tables or any data/ file.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from scripts.db.common import connect, parse_ts  # noqa: E402

SEASON = 2026
STATUS_MAP = {"approved": "approved", "rejected": "rejected", "admin_rejected": "rejected", "withdrawn": "withdrawn",
              "expired": "expired", "admin_review": "accepted", "pending": "pending"}
OPEN_STATUSES = {"pending", "accepted"}      # mapped statuses that are live work: reported, never imported
EXPIRY_DAYS = 7


class CutoverError(Exception):
    pass


def clean_admin(value, teams):
    """One approved_by value: a team abbreviation, or 'ADMIN' for everything else."""
    v = (value or "").strip()
    return v if v in teams else "ADMIN"


def build_plan(conn):
    """Read the mirror and return (plan, notes, skipped). plan is a list of dicts, one per imported trade, in id
    order. skipped lists the open (pending / admin_review) mirror rows that were not imported. Reads only."""
    teams = {r[0] for r in conn.execute("select abbr from teams")}
    cur = conn.cursor(row_factory=dict_row)
    mirror = cur.execute("select * from trades order by created_at, trade_id").fetchall()
    xfers = {}
    for x in cur.execute("select * from trade_transfers order by id").fetchall():
        xfers.setdefault(x["trade_id"], []).append(x)
    plan, notes, skipped = [], [], []
    n = 0
    for m in mirror:
        raw = m["raw"] or {}
        if m["status"] not in STATUS_MAP:
            raise CutoverError(f"{m['trade_id']}: don't know how to map status {m['status']!r}")
        status = STATUS_MAP[m["status"]]
        if status in OPEN_STATUSES:
            skipped.append({"legacy": m["trade_id"], "mirror_status": m["status"], "teams": list(m["teams"] or []),
                            "created_at": m["created_at"]})
            continue
        n += 1
        created = m["created_at"]
        initiator = m["initiator_team"]
        parties = list(m["teams"] or [])
        if initiator not in parties:
            raise CutoverError(f"{m['trade_id']}: initiator {initiator!r} is not one of {parties}")
        t = {"trade_id": f"T{SEASON % 100:02d}-{n:04d}", "legacy": m["trade_id"], "status": status, "created_by": initiator,
             "created_at": created, "parties": parties, "source": "import", "approved_by": None, "approved_at": None,
             "closed_by": None, "close_kind": None, "close_note": None, "expires_at": None}
        if status == "approved":
            t["approved_by"] = clean_admin(m["admin_decision_by"], teams)
            t["approved_at"] = m["processed_at"] or m["data_applied_at"] or m["manager_approved_at"] or created
            if not (m["processed_at"] or m["data_applied_at"] or m["manager_approved_at"]):
                notes.append(f"{m['trade_id']} ({t['trade_id']}): approved with no timestamp at all; approved_at = created_at")
        elif status == "rejected":
            t.update(close_kind="reject", closed_by=m["rejected_by"] or "ADMIN",
                     close_note=(m["rejection_reason"] or "").strip() or "No reason recorded.")
        elif status == "withdrawn":
            t.update(close_kind="withdraw", closed_by=initiator)
        elif status == "expired":
            t.update(close_kind="expire", closed_by="system")
        accepted = set(raw.get("acceptances") or [])
        t["accepted_at"] = {}
        for p in parties:
            if p in accepted:
                t["accepted_at"][p] = created if p == initiator else (m["manager_approved_at"] or created)
            elif status == "approved":
                t["accepted_at"][p] = m["manager_approved_at"] or t["approved_at"]
                notes.append(f"{m['trade_id']} ({t['trade_id']}): {p} is not in raw.acceptances but the trade was approved; accepted_at synthesized")
        t["raw"] = {**raw, "legacy_trade_id": m["trade_id"]}
        t["items"] = []
        for x in xfers.get(m["trade_id"], []):
            kind = {"player": "player", "wizbucks": "wb", "draft_pick": "pick"}.get(x["type"])
            if kind is None:
                raise CutoverError(f"{m['trade_id']}: unknown transfer type {x['type']!r}")
            t["items"].append({"kind": kind, "upid": x["upid"] if kind == "player" else None,
                               "amount": x["amount"] if kind == "wb" else None, "from_team": x["from_team"],
                               "to_team": x["to_team"], "raw": x["raw"] if kind == "pick" else None})
        plan.append(t)
    return plan, notes, skipped


def write_plan(conn, plan):
    if conn.execute("select count(*) from trades_live").fetchone()[0]:
        raise CutoverError("trades_live already has rows; refusing to write")
    for t in plan:
        conn.execute("""insert into trades_live (trade_id, season, status, created_by, created_at, expires_at, approved_by,
                                                 approved_at, closed_by, close_kind, close_note, source, raw)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (t["trade_id"], SEASON, t["status"], t["created_by"], t["created_at"], t["expires_at"], t["approved_by"],
                      t["approved_at"], t["closed_by"], t["close_kind"], t["close_note"], t["source"], Jsonb(t["raw"])))
        for p in t["parties"]:
            conn.execute("insert into trade_parties (trade_id, team_abbr, accepted_at) values (%s, %s, %s)",
                         (t["trade_id"], p, t["accepted_at"].get(p)))
        for i in t["items"]:
            conn.execute("""insert into trade_items (trade_id, kind, upid, amount, from_team, to_team, raw)
                            values (%s, %s, %s, %s, %s, %s, %s)""",
                         (t["trade_id"], i["kind"], i["upid"], i["amount"], i["from_team"], i["to_team"],
                          Jsonb(i["raw"]) if i["raw"] is not None else None))
    conn.execute("insert into trade_sequences (season, next) values (%s, %s) on conflict (season) do update set next = excluded.next",
                 (SEASON, len(plan) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reshape the 2026 mirror trades into the Spec 05 live tables (Zach only).")
    ap.add_argument("--dry-run", action="store_true", help="do everything, print the plan, then roll back")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args(argv)
    with connect(args.database_url) as conn:
        try:
            plan, notes, skipped = build_plan(conn)
            write_plan(conn, plan)        # inside the transaction, so a dry run also proves every constraint accepts the rows
            # deferred constraints (sender is a party, 2 or 3 parties) are checked here, before anything is committed
            conn.execute("set constraints all immediate")
        except CutoverError as e:
            conn.rollback()
            print(f"REFUSED: {e}")
            return 2
        counts = {}
        for t in plan:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        items = sum(len(t["items"]) for t in plan)
        print(f"{len(plan)} trades, {items} items: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        print(f"ids {plan[0]['trade_id']} .. {plan[-1]['trade_id']}; sequence for {SEASON} set to {len(plan) + 1}" if plan else "no trades")
        for n in notes:
            print("note:", n)
        for k in skipped:
            print(f"NOT IMPORTED (open work, decide by hand): {k['legacy']} mirror status {k['mirror_status']!r}, "
                  f"teams {k['teams']}, created {k['created_at']}")
        if args.dry_run:
            conn.rollback()
            print("dry run: nothing written")
        else:
            conn.commit()
            print("committed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
