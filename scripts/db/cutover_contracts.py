"""CUTOVER-DAY SCRIPT (December 15, 2026). Only Zach runs this, once, after the final Spec 01 import.

Spec 03, "Cutover", steps 3-5. Everything is written in ONE transaction.

  python scripts/db/cutover_contracts.py --dry-run [--resolutions FILE] [--opening-balances]
      Prints every conflict, the contracts it would build and (with --opening-balances) the 12
      opening balances in team-abbr order plus the exact --confirm-balances string. Writes nothing.

  python scripts/db/cutover_contracts.py --resolutions FILE
  python scripts/db/cutover_contracts.py --resolutions FILE --opening-balances --confirm-balances "100,50,..."
      Builds `contracts` and `roster_slots` from the open `roster_assignments` rows (the shadow mirror):
        contract_type keeper      -> kind 'keeper', tier = contract_tier,  roster slot 'active'
        contract_type purchased   -> kind 'prospect', PC,                   roster slot 'farm'
        contract_type blue_chip   -> kind 'prospect', BC,                   roster slot 'farm'
        contract_type development -> kind 'prospect', DC,                   roster slot 'farm'
      and sets players.carried_tier for unowned players that still carry a tier (raw years_simple, never 'P').
      With --opening-balances the contracts, the slots and one `opening_balance` ledger row per team
      (from data/wizbucks.json) go in the same transaction, and --confirm-balances must repeat the
      12 numbers Zach checked (comma separated, team-abbr order, as --dry-run prints them).

Conflicts: there is no way to skip one. Every conflict (owned players whose fields disagree or are
missing, unowned players with an unreadable tier) needs an entry in the --resolutions JSON file, which
Zach writes by hand:
    {"<upid>": {"kind": "keeper", "tier": "VC-1"},                       # owned, keeper
     "<upid>": {"kind": "prospect", "prospect_type": "BC"},              # owned, prospect
     "<upid>": {"kind": "keeper", "tier": "TC-1", "team": "WIZ"},        # "team" only if the conflict is no_team
     "<upid>": {"tier": "TC-2"}}                                         # unowned: the tier he carries (null = none)
The script refuses to write while any conflict has no resolution, or a resolution is invalid or unused.

Not populated (nothing in the mirror to source it from): contracts.acquired_round, il_tag and its fields,
rat_count, international_rookie, bc_decision; prospect_status; and roster slots 'il' and 'na'. Every
keeper starts in 'active' and every prospect in 'farm': IL and NA must be set by hand after cutover
(move_slot), and the nightly roster check will flag teams that start over a limit.
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


def build_contracts(conn, resolutions=None):
    """Returns (rows, carried, conflicts, notes). Reads only.

    rows:      (upid, team, kind, tier, prospect_type) for every owned player.
    carried:   (tier, upid) for unowned players that carry a tier.
    conflicts: every problem found; each has "resolved" True when `resolutions` answers it.
    A conflicted player is built only from his resolution; nothing is guessed from the mirror.
    """
    resolutions = resolutions or {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""select ra.upid, ra.team_abbr, ra.contract_type, ra.contract_tier, ra.status_raw, p.name
                         from roster_assignments ra join players p using (upid)
                        where ra.valid_to is null order by ra.upid""")
        owned = cur.fetchall()
        cur.execute("""select p.upid, p.name, p.raw->>'years_simple' as ys, p.raw->>'contract_type' as ct
                         from players p
                        where not exists (select 1 from roster_assignments ra where ra.upid = p.upid and ra.valid_to is null)
                        order by p.upid""")
        unowned = cur.fetchall()
        cur.execute("select tier from contract_tiers")
        tiers = {r["tier"] for r in cur.fetchall()}
        cur.execute("select abbr from teams")
        teams = {r["abbr"] for r in cur.fetchall()}

    rows, conflicts, notes, problems = [], [], [], []

    def add_conflict(kind, r, detail, owned_player):
        conflicts.append({"kind": kind, "upid": r["upid"], "name": r["name"], "team": r.get("team_abbr"),
                          "detail": detail, "owned": owned_player, "resolved": r["upid"] in resolutions})

    def from_resolution(r):
        """Row for a conflicted owned player, from Zach's entry; problems are collected, not raised."""
        res, upid = resolutions[r["upid"]], r["upid"]
        team = res.get("team") or r["team_abbr"]
        if team not in teams:
            problems.append(f"{upid}: resolution needs a valid team (got {team!r})")
        elif res.get("kind") == "keeper":
            if res.get("tier") not in tiers or res.get("tier") == "P":
                problems.append(f"{upid}: keeper resolution needs a real tier (got {res.get('tier')!r})")
            elif res.get("prospect_type"):
                problems.append(f"{upid}: a keeper resolution can't have a prospect_type")
            else:
                return (upid, team, "keeper", res["tier"], None)
        elif res.get("kind") == "prospect":
            if res.get("prospect_type") not in ("DC", "PC", "BC"):
                problems.append(f"{upid}: prospect resolution needs prospect_type DC, PC or BC (got {res.get('prospect_type')!r})")
            elif res.get("tier"):
                problems.append(f"{upid}: a prospect resolution can't have a tier")
            else:
                return (upid, team, "prospect", None, res["prospect_type"])
        else:
            problems.append(f"{upid}: resolution kind must be 'keeper' or 'prospect' (got {res.get('kind')!r})")
        return None

    for r in owned:
        ctype, tier = r["contract_type"], r["contract_tier"]
        found = []
        if not r["team_abbr"]:
            found.append(("no_team", "manager/FBP_Team disagreement unresolved; an admin must pick the team"))
        if ctype is None or ctype == "":
            found.append(("blank_contract_type", f"contract_type blank (tier {tier!r}, status {r['status_raw']!r})"))
        elif ctype not in CONTRACT_TYPE_MAP.values():
            found.append(("unmapped_contract_type", f"contract_type {ctype!r}"))
        elif ctype == "keeper":
            if tier is None or tier == "":
                found.append(("keeper_missing_tier", "keeper contract with no tier"))
            elif tier == "P":
                found.append(("keeper_has_prospect_marker", "keeper contract whose tier is 'P'"))
            elif tier not in tiers:
                found.append(("unmapped_tier", f"tier {tier!r}"))
        elif tier not in (None, "", "P"):
            found.append(("prospect_with_keeper_tier", f"{ctype} contract carries tier {tier!r}"))
        st = status_tier(r["status_raw"])
        if st == "?":
            found.append(("unmapped_status", f"status_raw {r['status_raw']!r}"))
        elif st is not None and tier not in (None, "") and st != tier:
            found.append(("status_disagrees_with_tier", f"status_raw {r['status_raw']!r} says {st}, contract_tier says {tier}"))
        for kind, detail in found:
            add_conflict(kind, r, detail, True)
        if found:
            if r["upid"] in resolutions:
                row = from_resolution(r)
                if row:
                    rows.append(row)
        elif ctype == "keeper":
            rows.append((r["upid"], r["team_abbr"], "keeper", tier, None))
        else:
            rows.append((r["upid"], r["team_abbr"], "prospect", None, PROSPECT_TYPE[ctype]))

    carried = []
    for u in unowned:
        ys = (u["ys"] or "").strip()
        tier = CONTRACT_TIER_MAP.get(ys) if ys else None
        detail = None
        if ys and ys not in CONTRACT_TIER_MAP:
            detail = ("unmapped_tier", f"unowned years_simple {ys!r}")
        elif tier and tier != "P":
            carried.append((tier, u["upid"]))
        elif (u["ct"] or "") == "Keeper Contract":
            detail = ("unowned_keeper_without_tier", "unowned 'Keeper Contract' row with no keeper tier")
        if detail:
            add_conflict(detail[0], {**u, "team_abbr": None}, detail[1], False)
            res = resolutions.get(u["upid"])
            if res is not None:
                if "tier" not in res or (res["tier"] is not None and (res["tier"] not in tiers or res["tier"] == "P")):
                    problems.append(f"{u['upid']}: unowned resolution needs a 'tier' (a real tier, or null for none)")
                elif res["tier"] is not None:
                    carried.append((res["tier"], u["upid"]))

    conflicted = {c["upid"] for c in conflicts}
    unused = sorted(set(resolutions) - conflicted)
    problems += [f"{u}: resolution given but the player has no conflict (remove it)" for u in unused]
    notes.append(f"{len(owned)} open roster_assignments -> {len(rows)} contracts "
                 f"({sum(1 for r in rows if r[2] == 'keeper')} keeper, {sum(1 for r in rows if r[2] == 'prospect')} prospect)")
    notes.append(f"{len(carried)} unowned players keep a tier as carried_tier")
    return rows, carried, conflicts, notes, problems


def print_conflicts(conflicts):
    by_kind: dict[str, list] = {}
    for c in conflicts:
        by_kind.setdefault(c["kind"], []).append(c)
    open_ = sum(1 for c in conflicts if not c["resolved"])
    print(f"\n== {len(conflicts)} conflict(s), {open_} without a resolution ==")
    for kind, items in sorted(by_kind.items()):
        print(f"\n{kind}: {len(items)}")
        for c in items:
            mark = "ok  " if c["resolved"] else "OPEN"
            print(f"   [{mark}] upid {c['upid']:>8} {str(c['name'])[:28]:28} team {str(c['team']):4} {c['detail']}")


def roster_slot(kind):
    return "active" if kind == "keeper" else "farm"


def read_resolutions(path):
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise SystemExit("--resolutions must be a JSON object of {upid: {...}}")
    return data


def read_opening_balances(conn, repo_root):
    """({abbr: amount}, problems, season) from data/wizbucks.json."""
    wiz = json.loads((Path(repo_root) / "data" / "wizbucks.json").read_text(encoding="utf-8"))
    with conn.cursor(row_factory=dict_row) as cur:
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
        elif not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            problems.append(f"{abbr}: balance {amount!r} is not a non-negative whole number")
        else:
            balances[abbr] = amount
    problems += [f"{a}: not in wizbucks.json" for a in active if a not in balances]
    problems += [f"{a}: in wizbucks.json but not an active team" for a in balances if a not in active]
    return balances, problems, season


def cmd_cutover(conn, args) -> int:
    with conn.cursor() as cur:
        cur.execute("select (select count(*) from contracts) + (select count(*) from roster_slots)")
        if cur.fetchone()[0]:
            print("contracts or roster_slots already has rows: cutover already run? Nothing done.")
            return 1
        cur.execute("select count(*) from wb_ledger")
        if args.opening_balances and cur.fetchone()[0]:
            print("wb_ledger already has rows: opening balances already written? Nothing done.")
            return 1
    resolutions = read_resolutions(args.resolutions)
    rows, carried, conflicts, notes, problems = build_contracts(conn, resolutions)
    for n in notes:
        print(n)
    if conflicts:
        print_conflicts(conflicts)
    open_conflicts = [c for c in conflicts if not c["resolved"]]
    balances, season = {}, None
    if args.opening_balances:
        balances, bal_problems, season = read_opening_balances(conn, args.repo_root)
        order = sorted(balances)
        print("\nopening balances from data/wizbucks.json, team-abbr order (Zach confirms all of these):")
        for abbr in order:
            print(f"   {abbr:4} ${balances[abbr]}")
        confirm = ",".join(str(balances[a]) for a in order)
        print(f"\n--confirm-balances \"{confirm}\"")
        problems += bal_problems

    if open_conflicts:
        print(f"\n{len(open_conflicts)} conflict(s) have no resolution. Write them into the --resolutions file "
              "(see the top of this script for the format). Nothing written.")
        if not args.resolutions:
            template = {c["upid"]: ({"tier": None} if not c["owned"] else {"kind": None, "tier": None, "prospect_type": None})
                        for c in open_conflicts}
            print("starting point for the file:\n" + json.dumps(template, indent=1))
    if problems:
        print("\nproblems:")
        for p in problems:
            print("  ", p)
    if open_conflicts or problems:
        return 2
    if args.opening_balances and not args.dry_run:
        given = [x.strip() for x in (args.confirm_balances or "").split(",")]
        if given != [str(balances[a]) for a in sorted(balances)]:
            print("\n--confirm-balances is missing or does not match the numbers above. Nothing written.")
            return 2
    print("\nIL and NA slots are NOT set: every keeper goes to 'active' and every prospect to 'farm'. "
          "Set IL/NA by hand after cutover (move_slot).")
    if args.dry_run:
        print(f"--dry-run: would insert {len(rows)} contracts and {len(rows)} roster slots, set carried_tier on {len(carried)} players"
              + (f" and write {len(balances)} opening balances" if args.opening_balances else "") + ". Nothing written.")
        return 0
    now = datetime.now(timezone.utc)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """insert into contracts (upid, team_abbr, kind, tier, prospect_type, acquired_via, valid_from)
                   values (%s, %s, %s, %s, %s, 'import', %s)""",
                [(*r, now) for r in rows])
            cur.executemany("insert into roster_slots (upid, slot, updated_at) values (%s, %s, %s)",
                            [(r[0], roster_slot(r[2]), now) for r in rows])
            cur.executemany("update players set carried_tier = %s where upid = %s", carried)
            for abbr in sorted(balances):
                cur.execute("select wb_credit(%s, %s, 'opening_balance', 'OFFSEASON', %s, null, 'cutover', %s, %s, 'cutover')",
                            (abbr, balances[abbr], season, str(season), "opening balance from data/wizbucks.json"))
    print(f"\nwrote {len(rows)} contracts, {len(rows)} roster slots and {len(carried)} carried tiers"
          + (f", and {len(balances)} opening_balance rows for season {season}." if args.opening_balances else "."))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cutover-day: build contracts, roster slots and opening balances (Zach only).")
    ap.add_argument("--dry-run", action="store_true", help="report everything, write nothing")
    ap.add_argument("--resolutions", default=None, help="JSON file {upid: {kind, tier, prospect_type}} answering every conflict")
    ap.add_argument("--opening-balances", action="store_true", help="also write one opening_balance row per team, in the same transaction")
    ap.add_argument("--confirm-balances", default=None, help="the 12 balances, comma separated, in team-abbr order as --dry-run prints them")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)
    with connect(args.database_url) as conn:
        return cmd_cutover(conn, args)


if __name__ == "__main__":
    sys.exit(main())
