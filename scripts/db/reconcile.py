"""Compare the database with data/*.json and config/*.json (Spec 01, section 5).

Prints PASS / FAIL (or WARN) for each of the spec's 7 checks, then the
"known data issues" section, then a summary. Exit code 1 on any FAIL.
Read-only: it never writes to the database or to the repo.

Usage:
    python scripts/db/reconcile.py [--database-url URL] [--repo-root DIR]
                                   [--sample N] [--seed S] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.db.common import (  # noqa: E402
    CONTRACT_TIER_MAP,
    CONTRACT_TYPE_MAP,
    blank,
    connect,
    parse_date,
    to_bool,
    to_int,
)
from scripts.db.import_json import APP_STATE_FILES  # noqa: E402

# Team references in these files must resolve (check 5). Anything else
# (historical logs, player_log owners) is a WARN.
CURRENT_FILE_PREFIXES = ("combined_players", "wizbucks_transactions", "trades", "draft_order_2026")


class Results:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []  # (status, text)
        self.details: list[str] = []

    def add(self, status: str, text: str, *details: str) -> None:
        self.lines.append((status, text))
        self.details.append("\n".join(details))

    @property
    def failed(self) -> bool:
        return any(s == "FAIL" for s, _ in self.lines)


def load(root: Path, rel: str):
    with open(root / rel, encoding="utf-8") as f:
        return json.load(f)


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def scalar(conn, sql, params=None):
    return q(conn, sql, params)[0][0]


def players_by_first_upid(rows):
    """First row per non-blank upid, mirroring the importer's skip rules."""
    seen, out = set(), []
    for r in rows:
        upid = str(r.get("upid")).strip() if r.get("upid") not in (None, "") else ""
        if not upid or upid in seen:
            continue
        seen.add(upid)
        out.append((upid, r))
    return out


def owned(r) -> bool:
    return not (blank(r.get("FBP_Team")) and blank(r.get("manager")))


class Aliases:
    def __init__(self, conn):
        self.map = {a.casefold(): t for a, t in q(conn, "select alias, team_abbr from team_name_aliases")}

    def get(self, value):
        return None if blank(value) else self.map.get(str(value).strip().casefold())


# ------------------------------------------------------------------- checks

def check_row_counts(conn, root, report, res, verbose):
    skipped = report.get("skipped_detail", {})

    def n_skips(table, reason=None):
        return sum(1 for s in skipped.get(table, []) if reason is None or s.get("reason") == reason)

    combined = load(root, "data/combined_players.json")
    uniq = players_by_first_upid(combined)
    managers = load(root, "config/managers.json")["teams"]
    wb = load(root, "data/wizbucks_transactions.json")
    trades_raw = load(root, "data/trades.json")
    trades = list(trades_raw.values()) if isinstance(trades_raw, dict) else list(trades_raw)
    pending_path = root / "data/pending_trades.json"
    if pending_path.exists():
        p = load(root, "data/pending_trades.json")
        trades += list(p.values()) if isinstance(p, dict) else list(p)
    seen_t, n_trades, n_transfers = set(), 0, 0
    for t in trades:
        tid = t.get("trade_id")
        if blank(tid) or tid in seen_t:
            continue
        seen_t.add(tid)
        n_trades += 1
        n_transfers += len(t.get("transfers") or [])
    ev_hist = load(root, "data/transactions_history.json")
    ev_log = load(root, "data/player_log.json")
    draft = load(root, "data/draft_order_2026.json")
    stats = load(root, "data/player_stats.json")

    # (table, expected, actual sql, note)
    expected = [
        ("teams (active)", len(managers), "select count(*) from teams where active"),
        ("players", len(uniq), "select count(*) from players"),
        ("roster_assignments (open)", sum(1 for _, r in uniq if owned(r)),
         "select count(*) from roster_assignments where valid_to is null"),
        ("wb_ledger_history", len(wb) - n_skips("wb_ledger_history"), "select count(*) from wb_ledger_history"),
        ("trades", n_trades, "select count(*) from trades"),
        ("trade_transfers", n_transfers, "select count(*) from trade_transfers"),
        ("player_events", len(ev_hist) + len(ev_log) - n_skips("player_events"),
         "select count(*) from player_events where source_file in ('transactions_history','player_log')"),
        ("draft_picks (2026)", sum(1 for r in draft if "draft" in r) - n_skips("draft_picks", "duplicate pick"),
         "select count(*) from draft_picks where season = 2026"),
        ("player_season_stats", len(stats) - n_skips("player_season_stats"),
         "select count(*) from player_season_stats"),
        ("app_state", sum(1 for k in APP_STATE_FILES if (root / "data" / f"{k}.json").exists()),
         "select count(*) from app_state"),
    ]
    bad, lines = [], []
    for name, exp, sql in expected:
        got = scalar(conn, sql)
        lines.append(f"{name:28s} source {exp:>7d}  db {got:>7d}")
        if got != exp:
            bad.append(f"{name}: source {exp}, db {got}")
    # cross-check: the import's own skip counts must match what the files say
    if n_skips("players") != len(combined) - len(uniq):
        bad.append(f"players: report skipped {n_skips('players')} but files imply {len(combined) - len(uniq)}")
    hist = scalar(conn, "select count(*) from teams where not active")
    lines.append(f"{'teams (historical, extra)':28s} db {hist:>7d}  (not compared)")
    extra = [s for s in lines] if verbose else []
    if bad:
        res.add("FAIL", f"1. Row counts: {len(bad)} table(s) differ", *[f"     {b}" for b in bad], *["     " + l for l in extra])
    else:
        res.add("PASS", f"1. Row counts: {len(expected)} tables match their source files", *["     " + l for l in extra])


def check_wizbucks(conn, root, aliases, res):
    wiz = load(root, "data/wizbucks.json")
    expected, bad = {}, []
    for name, bal in wiz.items():
        abbr = aliases.get(name)
        if abbr is None:
            bad.append(f"cannot resolve team name {name!r}")
        else:
            expected[abbr] = bal
    got = dict(q(conn, "select team_abbr, balance from wb_balances_history"))
    for abbr, bal in sorted(expected.items()):
        if got.get(abbr) != bal:
            bad.append(f"{abbr}: wizbucks.json {bal}, wb_balances_history {got.get(abbr)}")
    for abbr in sorted(set(got) - set(expected)):
        bad.append(f"{abbr}: in wb_balances_history ({got[abbr]}) but not in wizbucks.json")
    if bad:
        res.add("FAIL", f"2. WizBucks: {len(bad)} team(s) differ", *[f"     {b}" for b in bad])
    else:
        res.add("PASS", f"2. WizBucks: wb_balances_history equals wizbucks.json for all {len(expected)} teams")


def check_rosters(conn, root, aliases, res):
    uniq = players_by_first_upid(load(root, "data/combined_players.json"))
    null_rows = q(conn, """select ra.upid, p.name from roster_assignments ra join players p using (upid)
                           where ra.valid_to is null and ra.team_abbr is null order by ra.upid""")
    null_upids = {u for u, _ in null_rows}
    expected: dict[str, int] = {}
    unresolved = []
    for upid, r in uniq:
        if upid in null_upids or blank(r.get("FBP_Team")):
            continue
        abbr = aliases.get(r["FBP_Team"])
        if abbr is None:
            unresolved.append(f"{upid} {r.get('name')}: FBP_Team {r['FBP_Team']!r}")
            continue
        expected[abbr] = expected.get(abbr, 0) + 1
    got = {t: n for t, n in q(conn, """select team_abbr, count(*) from roster_assignments
                                         where valid_to is null and team_abbr is not null group by 1""")}
    bad = [f"{t}: combined_players {expected.get(t, 0)}, db {got.get(t, 0)}"
           for t in sorted(set(expected) | set(got)) if expected.get(t, 0) != got.get(t, 0)]
    bad += [f"unresolved FBP_Team: {u}" for u in unresolved]
    notes = [f"     owned but team_abbr is null (unresolved disagreement): {u} {n}" for u, n in null_rows]
    total = sum(got.values())
    if bad:
        res.add("FAIL", f"3. Rosters: {len(bad)} difference(s)", *[f"     {b}" for b in bad], *notes)
    else:
        res.add("PASS", f"3. Rosters: owned counts match for {len(got)} teams ({total} players); "
                        f"{len(null_rows)} owned row(s) have no team yet", *notes)


def check_roster_fk(conn, res):
    n = scalar(conn, """select count(*) from roster_assignments ra
                         left join players p using (upid) where ra.valid_to is null and p.upid is null""")
    total = scalar(conn, "select count(*) from roster_assignments where valid_to is null")
    if n:
        res.add("FAIL", f"4. Roster upids: {n} of {total} open assignments have no player")
    else:
        res.add("PASS", f"4. Roster upids: 0 of {total} open assignments are missing from players")


def check_team_refs(conn, root, aliases, report, res):
    refs: dict[str, int] = {}

    def ref(where, value):
        if blank(value):
            return
        if aliases.get(value) is None:
            k = f"{where}: {str(value).strip()}"
            refs[k] = refs.get(k, 0) + 1

    for r in load(root, "data/combined_players.json"):
        ref("combined_players.FBP_Team", r.get("FBP_Team"))
        ref("combined_players.manager", r.get("manager"))
    for r in load(root, "data/wizbucks_transactions.json"):
        ref("wizbucks_transactions.team", r.get("team") or r.get("team_name"))
    tr = load(root, "data/trades.json")
    trades = list(tr.values()) if isinstance(tr, dict) else list(tr)
    if (root / "data/pending_trades.json").exists():
        p = load(root, "data/pending_trades.json")
        trades += list(p.values()) if isinstance(p, dict) else list(p)
    for t in trades:
        ref("trades.initiator_team", t.get("initiator_team"))
        for n in t.get("teams") or []:
            ref("trades.teams", n)
        for x in t.get("transfers") or []:
            ref("trades.from_team", x.get("from_team"))
            ref("trades.to_team", x.get("to_team"))
    for r in load(root, "data/draft_order_2026.json"):
        if "draft" in r:
            ref("draft_order_2026.team", r.get("current_owner") or r.get("team"))
    flagged = [f for f in report.get("flag_detail", {}).get("unresolved_team_reference", [])
               if f["where_value"].startswith(CURRENT_FILE_PREFIXES)]
    bad = [f"{k} ({n} rows)" for k, n in sorted(refs.items())]
    bad += [f"import flagged {f['where_value']} ({f['rows']} rows)" for f in flagged if f["where_value"] not in refs]
    if bad:
        res.add("FAIL", f"5. Team references: {len(bad)} unresolved in current files", *[f"     {b}" for b in bad])
    else:
        res.add("PASS", "5. Team references: all resolved in combined_players, wizbucks_transactions, "
                        "trades, draft_order_2026")
    # everything else the importer could not resolve is expected (historical names)
    other = [f for f in report.get("flag_detail", {}).get("unresolved_team_reference", [])
             if not f["where_value"].startswith(CURRENT_FILE_PREFIXES)]
    return other


def check_trade_upids(conn, res, verbose):
    rows = q(conn, """select t.trade_id, t.upid from trade_transfers t left join players p using (upid)
                       where t.upid is not null and p.upid is null order by 1, 2""")
    if rows:
        shown = rows if verbose else rows[:10]
        res.add("WARN", f"6. Trade transfers: {len(rows)} transfer(s) point at a upid that is not in players",
                *[f"     {t} upid {u}" for t, u in shown],
                *([f"     ... {len(rows) - 10} more (--verbose)"] if len(rows) > len(shown) else []))
    else:
        n = scalar(conn, "select count(*) from trade_transfers where upid is not null")
        res.add("PASS", f"6. Trade transfers: all {n} transfers with a upid point at a real player")


def expected_player(r):
    return {
        "name": r.get("name") or r.get("player_name") or f"UPID {r.get('upid')}",
        "mlb_id": to_int(r.get("mlb_id")),
        "yahoo_id": None if blank(r.get("yahoo_id")) else str(r["yahoo_id"]).strip(),
        "bbref_id": None if blank(r.get("bbref_id")) else str(r["bbref_id"]),
        "fangraphs_id": None if blank(r.get("fangraphs_id")) else str(r["fangraphs_id"]),
        "birth_date": parse_date(r.get("birth_date")),
        "bats": None if blank(r.get("bats")) else r["bats"],
        "throws": None if blank(r.get("throws")) else r["throws"],
        "height": None if blank(r.get("height")) else r["height"],
        "weight": to_int(r.get("weight")),
        "mlb_team": None if blank(r.get("team")) else r["team"],
        "positions": None if blank(r.get("position")) else r["position"],
        "mlb_primary_position": None if blank(r.get("mlb_primary_position")) else r["mlb_primary_position"],
        "player_type": None if blank(r.get("player_type")) else r["player_type"],
        "fypd": to_bool(r.get("fypd")),
        "rank": to_int(r.get("rank")),
        "raw": r,
    }


def expected_assignment(r, aliases):
    """None if unowned, else the expected open roster_assignments fields."""
    if not owned(r):
        return None
    fbp = None if blank(r.get("FBP_Team")) else aliases.get(r["FBP_Team"])
    mgr = None if blank(r.get("manager")) else aliases.get(r["manager"])
    ctype, tier = r.get("contract_type"), r.get("years_simple")
    return {
        "team_abbr": fbp if (fbp and fbp == mgr) else None,
        "contract_type": None if blank(ctype) else CONTRACT_TYPE_MAP.get(ctype, ctype),
        "contract_tier": None if blank(tier) else CONTRACT_TIER_MAP.get(str(tier).strip(), str(tier).strip()),
        "status_raw": None if blank(r.get("status")) else r.get("status"),
    }


def check_spot(conn, root, aliases, n, seed, res):
    uniq = players_by_first_upid(load(root, "data/combined_players.json"))
    rng = random.Random(seed)
    # half owned, half not, so contract/team mapping is exercised (most players are unowned)
    own = [x for x in uniq if owned(x[1])]
    rest = [x for x in uniq if not owned(x[1])]
    k_own = min(len(own), (n + 1) // 2)
    picks = rng.sample(own, k_own) + rng.sample(rest, min(len(rest), n - k_own))
    pcols = list(expected_player({}).keys())
    acols = ["team_abbr", "contract_type", "contract_tier", "status_raw"]
    mismatch, n_owned = None, 0
    for upid, r in picks:
        row = q(conn, f"select {', '.join(pcols)} from players where upid = %s", (upid,))
        if not row:
            mismatch = f"upid {upid} ({r.get('name')}): no players row"
            break
        got = dict(zip(pcols, row[0]))
        exp = expected_player(r)
        diffs = [f"players.{c}: json {exp[c]!r}, db {got[c]!r}" for c in pcols if exp[c] != got[c]]
        ea = expected_assignment(r, aliases)
        arow = q(conn, f"select {', '.join(acols)} from roster_assignments where upid = %s and valid_to is null", (upid,))
        if ea is None:
            if arow:
                diffs.append(f"roster_assignments: json unowned, db has open row {arow[0]}")
        else:
            n_owned += 1
            if not arow:
                diffs.append("roster_assignments: json owned, db has no open row")
            else:
                ga = dict(zip(acols, arow[0]))
                diffs += [f"roster_assignments.{c}: json {ea[c]!r}, db {ga[c]!r}" for c in acols if ea[c] != ga[c]]
        if diffs:
            mismatch = f"upid {upid} ({r.get('name')}):\n" + "\n".join("       " + d for d in diffs)
            break
    if mismatch:
        res.add("FAIL", f"7. Spot check: mismatch (seed {seed}, {len(picks)} players)", "     first mismatch: " + mismatch)
    else:
        res.add("PASS", f"7. Spot check: {len(picks)} random players (seed {seed}, {n_owned} owned) match "
                        f"on every field")


# ----------------------------------------------------------- known issues

def show(items, verbose, fmt=str, limit=10):
    for it in (items if verbose else items[:limit]):
        print("   ", fmt(it))
    if not verbose and len(items) > limit:
        print(f"    ... {len(items) - limit} more (--verbose)")


def known_issues(conn, report, verbose):
    flags = report.get("flag_detail", {})
    skipped = report.get("skipped_detail", {})
    print("\n== Known data issues (informational, never fail the run) ==")

    no_upid = [s for s in skipped.get("players", []) if s.get("reason") == "blank upid"]
    print(f"\nPlayers without a UPID (not imported): {len(no_upid)}")
    show(no_upid, True, lambda s: f"{s.get('name')} (mlb_id {s.get('mlb_id')})")

    dis = flags.get("team_disagreement", [])
    print(f"\nmanager / FBP_Team disagreements (imported with team_abbr null): {len(dis)}")
    for d in dis:
        print(f"    upid {d['upid']} {d['name']}: FBP_Team {d['FBP_Team']!r} -> {d['FBP_Team_resolved']}, "
              f"manager {d['manager']!r} -> {d['manager_resolved']}")
        ev = q(conn, """select ts, update_type, owner_raw, event from player_events where upid = %s
                         order by ts desc nulls last, id desc limit 3""", (d["upid"],))
        by = "upid"
        if not ev:  # old history rows often carry no upid: fall back to the player name
            by = "player_name"
            ev = q(conn, """select ts, update_type, owner_raw, event from player_events where player_name = %s
                             order by ts desc nulls last, id desc limit 3""", (d["name"],))
        if not ev:
            print("        evidence: no player_events rows (by upid or name)")
        else:
            print(f"        latest 3 player_events (matched by {by}):")
        for ts, ut, owner, event in ev:
            print(f"          {ts:%Y-%m-%d %H:%M} {ut} owner={owner!r} {str(event or '')[:100]}")

    none = flags.get("fbp_team_none", [])
    print(f"\nFBP_Team = \"None\"/null: {len(none)} rows")
    show(none, verbose, lambda d: f"upid {d['upid']} {d['name']} FBP_Team={d['FBP_Team']!r} manager={d['manager']!r}")

    dup = flags.get("duplicate_mlb_id", [])
    print(f"\nDuplicate MLB IDs in combined_players (players.mlb_id is not unique): {len(dup)}")
    show(dup, verbose, lambda d: f"mlb_id {d['mlb_id']} {d['name']}: upids {d['upids']}")

    dw = flags.get("duplicate_wb_txn_id", [])
    print(f"\nDuplicate wb txn_ids (suffixed #n in wb_ledger_history): {len(dw)}")
    show(dw, True, lambda d: f"row {d['row']} txn_id {d['txn_id']}")

    print("\nGenerated ids")
    print(f"    player_events: {scalar(conn, 'select count(*) from player_events where id_generated')}")
    print(f"    wb_ledger_history: {scalar(conn, 'select count(*) from wb_ledger_history where txn_id like %s', ('gen\\_%',))}")
    for n in report.get("notes", []):
        if "generated" in n or "duplicate" in n:
            print("    import note:", n)

    hist = flags.get("history_file_rows", [])
    if hist:
        print(f"\nHistorical files imported (history_file_rows): {len(hist)}")
        show(hist, verbose, lambda d: f"{d['file']}: {d['rows']} rows -> {d['table']}")
    others = {k: v for k, v in report.get("flags", {}).items()}
    if others:
        print("\nAll import flags: " + ", ".join(f"{k}={v}" for k, v in sorted(others.items())))


# --------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare the database with data/*.json (Spec 01, section 5).")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--sample", type=int, default=20, help="players in the spot check (default 20)")
    ap.add_argument("--seed", type=int, default=None, help="seed for the spot check (default: random, printed)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.repo_root)
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1_000_000)

    res = Results()
    with connect(args.database_url) as conn:
        conn.read_only = True
        run = q(conn, "select id, finished_at, report from import_runs order by id desc limit 1")
        if not run:
            print("FAIL  no import_runs row: run scripts/db/import_json.py first")
            return 1
        run_id, finished, report = run[0]
        print(f"reconcile: import_runs.id = {run_id} (finished {finished:%Y-%m-%d %H:%M} UTC), repo root {root}")
        aliases = Aliases(conn)

        check_row_counts(conn, root, report, res, args.verbose)
        check_wizbucks(conn, root, aliases, res)
        check_rosters(conn, root, aliases, res)
        check_roster_fk(conn, res)
        historical = check_team_refs(conn, root, aliases, report, res)
        check_trade_upids(conn, res, args.verbose)
        check_spot(conn, root, aliases, args.sample, seed, res)

        print()
        for (status, text), detail in zip(res.lines, res.details):
            print(f"{status:5s} {text}")
            if detail:
                print(detail)
        if historical:
            total = sum(f["rows"] for f in historical)
            print(f"WARN  5b. {len(historical)} historical team names no mapping file covers ({total} rows), "
                  "expected for old transactions_history / player_log owners:")
            show(sorted(historical, key=lambda f: -f["rows"]), args.verbose,
                 lambda f: f"{f['where_value']} ({f['rows']} rows)")

        known_issues(conn, report, args.verbose)

    statuses = [s for s, _ in res.lines]
    summary = f"{statuses.count('PASS')} PASS, {statuses.count('WARN')} WARN, {statuses.count('FAIL')} FAIL"
    code = 1 if res.failed else 0
    print(f"\nreconcile: {summary}; exit code {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
