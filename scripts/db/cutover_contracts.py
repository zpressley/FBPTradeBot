"""CUTOVER-DAY SCRIPT (December 15, 2026). Only Zach runs this, once, after the final Spec 01 import.

Spec 03, "Cutover", steps 3-5. Two modes, each in ONE transaction:

  python scripts/db/cutover_contracts.py --dry-run
  python scripts/db/cutover_contracts.py [--allow-conflicts]
      Builds `contracts` from the open `roster_assignments` rows (the shadow mirror):
        contract_type keeper      -> kind 'keeper', tier = contract_tier
        contract_type purchased   -> kind 'prospect', PC
        contract_type blue_chip   -> kind 'prospect', BC
        contract_type development -> kind 'prospect', DC
      and sets players.carried_tier for unowned players that still carry a tier
      (raw years_simple, never 'P').
      Every conflict is PRINTED and nothing is written while any exist, unless
      --allow-conflicts: then conflicts that can't be built without guessing are skipped
      (and listed), and rows whose only problem is the status_raw label disagreeing with
      the tier are built from contract_tier (years_simple).
      Refuses to run if `contracts` already has rows.

  python scripts/db/cutover_contracts.py --opening-balances [--dry-run]
      Writes one `opening_balance` ledger row per team from data/wizbucks.json (the numbers
      Zach confirms). Refuses if `wb_ledger` already has rows.

Nothing here guesses a rule: a conflict is a question for Zach.
roster_slots are NOT built here (the spec doesn't say where slots come from); see db/README.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from psycopg.rows import dict_row  # noqa: E402

from scripts.db.common import CONTRACT_TIER_MAP, CONTRACT_TYPE_MAP, connect  # noqa: E402

PROSPECT_TYPE = {"purchased": "PC", "blue_chip": "BC", "development": "DC"}
# status_raw label (after the Sheets bracket) -> tier
STATUS_LABEL = {
    "TC1": "TC-1", "TC2": "TC-2", "VC1": "VC-1", "VC2": "VC-2", "FC1": "FC-1", "FC2": "FC-2",
    "TCR": "TC-R", "TCBC1": "TC-BC-1", "TCBC2": "TC-BC-2", "P": "P",
}
STATUS_RE = re.compile(r"^\s*(?:\[(\d+)\])?\s*(.*?)\s*$")


def status_tier(status_raw):
    """The tier label inside status_raw ('[5] TC1' -> 'TC-1'); None if blank, '?' if unrecognised."""
    if not status_raw or not status_raw.strip():
        return None
    label = STATUS_RE.match(status_raw).group(2).replace(" ", "").replace("-", "").upper()
    return STATUS_LABEL.get(label, "?")


def build_contracts(conn):
    """Returns (rows_to_insert, carried, conflicts, notes). Reads only."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""select ra.upid, ra.team_abbr, ra.contract_type, ra.contract_tier, ra.status_raw, p.name
                         from roster_assignments ra join players p using (upid)
                        where ra.valid_to is null order by ra.upid""")
        owned = cur.fetchall()
        cur.execute("""select p.upid, p.name, p.raw->>'years_simple' as ys, p.raw->>'contract_type' as ct
                         from players p
                        where not exists (select 1 from roster_assignments ra where ra.upid = p.upid and ra.valid_to is null)""")
        unowned = cur.fetchall()
        cur.execute("select tier from contract_tiers")
        tiers = {r["tier"] for r in cur.fetchall()}

    rows, conflicts, notes = [], [], []

    def conflict(kind, r, detail, blocking=True):
        conflicts.append({"kind": kind, "upid": r["upid"], "name": r["name"], "team": r.get("team_abbr"),
                          "detail": detail, "blocking": blocking})

    for r in owned:
        ctype, tier = r["contract_type"], r["contract_tier"]
        blocked = False
        if not r["team_abbr"]:
            conflict("no_team", r, "manager/FBP_Team disagreement unresolved; an admin must pick the team")
            blocked = True
        if ctype is None or ctype == "":
            conflict("blank_contract_type", r, f"contract_type blank (tier {tier!r}, status {r['status_raw']!r})")
            blocked = True
        elif ctype not in CONTRACT_TYPE_MAP.values():
            conflict("unmapped_contract_type", r, f"contract_type {ctype!r}")
            blocked = True
        elif ctype == "keeper":
            if tier is None or tier == "":
                conflict("keeper_missing_tier", r, "keeper contract with no tier")
                blocked = True
            elif tier == "P":
                conflict("keeper_has_prospect_marker", r, "keeper contract whose tier is 'P'")
                blocked = True
            elif tier not in tiers:
                conflict("unmapped_tier", r, f"tier {tier!r}")
                blocked = True
        else:
            if tier not in (None, "", "P"):
                conflict("prospect_with_keeper_tier", r, f"{ctype} contract carries tier {tier!r}")
                blocked = True
        st = status_tier(r["status_raw"])
        if st == "?":
            conflict("unmapped_status", r, f"status_raw {r['status_raw']!r}", blocking=False)
        elif st is not None and tier not in (None, "") and st != tier:
            conflict("status_disagrees_with_tier", r,
                     f"status_raw {r['status_raw']!r} says {st}, contract_tier says {tier} (built from contract_tier)",
                     blocking=False)
        if blocked:
            continue
        if ctype == "keeper":
            rows.append((r["upid"], r["team_abbr"], "keeper", tier, None))
        else:
            rows.append((r["upid"], r["team_abbr"], "prospect", None, PROSPECT_TYPE[ctype]))

    carried = []
    for u in unowned:
        ys = (u["ys"] or "").strip()
        tier = CONTRACT_TIER_MAP.get(ys) if ys else None
        if ys and ys not in CONTRACT_TIER_MAP:
            conflict("unmapped_tier", {**u, "team_abbr": None}, f"unowned years_simple {ys!r}")
        elif tier and tier != "P":
            carried.append((tier, u["upid"]))
        elif (u["ct"] or "") == "Keeper Contract":
            conflict("unowned_keeper_without_tier", {**u, "team_abbr": None},
                     "unowned 'Keeper Contract' row with no keeper tier (will come in at the free-agent default)")
    notes.append(f"{len(owned)} open roster_assignments -> {len(rows)} contracts "
                 f"({sum(1 for r in rows if r[2] == 'keeper')} keeper, {sum(1 for r in rows if r[2] == 'prospect')} prospect)")
    notes.append(f"{len(carried)} unowned players keep a tier as carried_tier")
    return rows, carried, conflicts, notes


def print_conflicts(conflicts):
    by_kind: dict[str, list] = {}
    for c in conflicts:
        by_kind.setdefault(c["kind"], []).append(c)
    print(f"\n== {len(conflicts)} conflict(s) ==")
    for kind, items in sorted(by_kind.items()):
        flag = "BLOCKS" if items[0]["blocking"] else "info"
        print(f"\n[{flag}] {kind}: {len(items)}")
        for c in items:
            print(f"   upid {c['upid']:>8} {str(c['name'])[:28]:28} team {str(c['team']):4} {c['detail']}")


def cmd_contracts(conn, args) -> int:
    with conn.cursor() as cur:
        cur.execute("select count(*) from contracts")
        if cur.fetchone()[0]:
            print("contracts already has rows: cutover already run? Nothing done.")
            return 1
    rows, carried, conflicts, notes = build_contracts(conn)
    for n in notes:
        print(n)
    if conflicts:
        print_conflicts(conflicts)
    blocking = [c for c in conflicts if c["blocking"]]
    if conflicts and not args.allow_conflicts:
        print("\nConflicts exist: nothing written. Resolve them with Zach, or re-run with --allow-conflicts "
              "(blocking rows are skipped, not guessed).")
        return 2
    if args.dry_run:
        print(f"\n--dry-run: would insert {len(rows)} contracts, set carried_tier on {len(carried)} players "
              f"and skip {len(blocking)} blocked row(s). Nothing written.")
        return 0
    now = datetime.now(timezone.utc)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """insert into contracts (upid, team_abbr, kind, tier, prospect_type, acquired_via, valid_from)
                   values (%s, %s, %s, %s, %s, 'import', %s)""",
                [(*r, now) for r in rows])
            cur.executemany("update players set carried_tier = %s where upid = %s", carried)
    print(f"\nwrote {len(rows)} contracts and {len(carried)} carried tiers; skipped {len(blocking)} blocked row(s).")
    return 0


def cmd_opening_balances(conn, args) -> int:
    wiz = json.loads((Path(args.repo_root) / "data" / "wizbucks.json").read_text(encoding="utf-8"))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select count(*) as n from wb_ledger")
        if cur.fetchone()["n"]:
            print("wb_ledger already has rows: opening balances already written? Nothing done.")
            return 1
        cur.execute("select alias, team_abbr from team_name_aliases")
        aliases = {r["alias"].casefold(): r["team_abbr"] for r in cur.fetchall()}
        cur.execute("select abbr from teams where active order by abbr")
        active = [r["abbr"] for r in cur.fetchall()]
        cur.execute("select max(season) as s from roster_limits")
        season = cur.fetchone()["s"]
    balances, problems = {}, []
    for name, amount in wiz.items():
        abbr = aliases.get(str(name).casefold())
        if abbr is None:
            problems.append(f"cannot resolve team {name!r}")
        elif not isinstance(amount, int) or amount < 0:
            problems.append(f"{abbr}: balance {amount!r} is not a non-negative whole number")
        else:
            balances[abbr] = amount
    problems += [f"{a}: not in wizbucks.json" for a in active if a not in balances]
    print("opening balances from data/wizbucks.json (Zach confirms all of these):")
    for abbr in sorted(balances):
        print(f"   {abbr:4} ${balances[abbr]}")
    if problems:
        print("\nproblems, nothing written:")
        for p in problems:
            print("  ", p)
        return 2
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    with conn.transaction():
        with conn.cursor() as cur:
            for abbr, amount in sorted(balances.items()):
                cur.execute("select wb_credit(%s, %s, 'opening_balance', 'OFFSEASON', %s, null, 'cutover', %s, %s, 'cutover')",
                            (abbr, amount, season, str(season), "opening balance from data/wizbucks.json"))
    print(f"\nwrote {len(balances)} opening_balance rows for season {season}.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cutover-day: build contracts / opening balances (Zach only).")
    ap.add_argument("--dry-run", action="store_true", help="report everything, write nothing")
    ap.add_argument("--allow-conflicts", action="store_true", help="write despite conflicts (blocked rows are skipped)")
    ap.add_argument("--opening-balances", action="store_true", help="write opening_balance ledger rows instead")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)
    with connect(args.database_url) as conn:
        return cmd_opening_balances(conn, args) if args.opening_balances else cmd_contracts(conn, args)


if __name__ == "__main__":
    sys.exit(main())
