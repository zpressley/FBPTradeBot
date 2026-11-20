"""Load everything under data/historical/ (Spec 01, "History: everything comes over").

Called at the end of the build phase of import_json.py. Rows go into
team_seasons (standings), roster_assignments (closed ranges from the Yahoo
roster pulls), draft_picks, top100_snapshots; whatever does not fit lands in
history_raw keyed (source_file, row_num) so nothing is lost.
"""

from __future__ import annotations

import csv
import json
from datetime import date

from scripts.db.common import blank, normalize_name, parse_date, parse_ts, to_int
from scripts.db.import_json import TABLE_COLUMNS, J, git_commit_date

HIST = "data/historical"
FALLBACK_TOP100_DATE = date(2025, 12, 31)
# CSV already loaded into top100_snapshots by import_json
TOP100_CSV = f"{HIST}/2025/Nov 1 2025 MLB Top 100 Prospects.csv"


class _History:
    def __init__(self, ctx, b):
        self.ctx, self.b = ctx, b
        self.raw_rows: dict[str, int] = {}   # source_file -> next row_num
        self.handled: set[str] = set()       # repo-relative files already loaded

    def raw(self, rel: str, row) -> None:
        n = self.raw_rows.get(rel, 0)
        self.raw_rows[rel] = n + 1
        self.b.add("history_raw", {"source_file": rel, "row_num": n, "row": J(row)})

    def count(self, rel: str, table: str, rows: int) -> None:
        self.b.report.flag("history_file_rows", {"file": rel, "rows": rows, "table": table})

    def update(self, table: str, index: int, **values) -> None:
        cols = TABLE_COLUMNS[table]
        row = list(self.b.rows[table][index])
        for k, v in values.items():
            row[cols.index(k)] = v
        self.b.rows[table][index] = tuple(row)


def _load(ctx, rel):
    with open(ctx.repo_root / rel, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- standings

def _load_standings(h: _History, rel: str, year: int) -> None:
    ctx, b = h.ctx, h.b
    data = _load(ctx, rel)
    index = {(r[0], r[1]): i for i, r in enumerate(b.rows["team_seasons"])}
    cols = TABLE_COLUMNS["team_seasons"]
    updated = 0
    entries = data.get("standings", [])
    resolved = [ctx.resolve_team(e.get("team_name"), where=f"standings_{year}.team_name") for e in entries]
    for entry, abbr in zip(entries, resolved):
        rank = to_int(entry.get("rank"))
        if abbr is None:
            h.raw(rel, entry)
            b.report.flag("history_standings_unresolved_team", {"file": rel, "team_name": entry.get("team_name")})
            continue
        if resolved.count(abbr) > 1:
            # two standings rows resolve to one team: cannot tell which is which
            h.raw(rel, entry)
            b.report.flag("history_standings_ambiguous_team", {
                "file": rel, "team_name": entry.get("team_name"), "rank": rank, "resolved_to": abbr})
            continue
        i = index.get((abbr, year))
        if i is None:
            b.add("team_seasons", {"team_abbr": abbr, "season": year, "standing": rank, "final_rank": rank,
                                   "raw": J(entry)})
            index[(abbr, year)] = len(b.rows["team_seasons"]) - 1
        else:
            old = b.rows["team_seasons"][i][cols.index("final_rank")]
            if old is not None and old != rank:
                b.report.flag("history_final_rank_disagrees", {"team": abbr, "season": year,
                                                               "final_rank": old, "standings_rank": rank})
            h.update("team_seasons", i, standing=rank, final_rank=old if old is not None else rank)
        updated += 1
    h.count(rel, "team_seasons", updated)
    if len(entries) > updated:
        h.count(rel, "history_raw", len(entries) - updated)


# ------------------------------------------------------------------ rosters

def _roster_team(ctx, key: str, mappings: list[dict], year: int):
    """Resolve a (sometimes mangled) yahoo_players key to a team abbr.

    Returns (abbr, how) where abbr is None if unresolved/ambiguous and how
    is None for a plain alias hit, otherwise a short reason string.
    """
    same_key = [m for m in mappings if (m.get("fbp_abbr") or "").strip().casefold() == key.strip().casefold()]
    if len(same_key) > 1:
        return None, "ambiguous: mapping file has several teams with this abbreviation"
    abbr = ctx.resolve_team(key, where=f"yahoo_players_{year}.key")
    if abbr:
        return abbr, None
    if same_key:
        abbr = ctx.resolve_team(same_key[0].get("team_name"), where=f"yahoo_players_{year}.mapping_name")
        if abbr:
            return abbr, "via team_mappings name"
    return None, "no match"


def _match_player(ctx, p: dict) -> str | None:
    yid = None if blank(p.get("yahoo_id")) else str(p["yahoo_id"]).strip()
    upid = ctx.players_by_yahoo_id.get(yid) if yid else None
    if upid:
        return upid
    candidates = ctx.players_by_norm_name.get(normalize_name(p.get("name")), [])
    return candidates[0] if len(candidates) == 1 else None


def _load_rosters(h: _History, rel: str, year: int, valid_to) -> None:
    ctx, b = h.ctx, h.b
    data = _load(ctx, rel)
    mapping_rel = f"{HIST}/{year}/team_mappings_{year}.json"
    mappings = _load(ctx, mapping_rel) if (ctx.repo_root / mapping_rel).exists() else []
    source = f"historical/yahoo_players_{year}.json"
    valid_from = parse_ts(f"{year}-01-01T00:00:00")

    resolved = {key: _roster_team(ctx, key, mappings, year) for key in data}
    # One leftover key and one leftover mapping team ("I'LL" vs JEP in 2025):
    # the files list teams in the same order, so pair them and say so.
    used = {a for a, _ in resolved.values() if a}
    leftover_keys = [k for k, (a, why) in resolved.items() if a is None and why == "no match"]
    leftover_maps = []
    for m in mappings:
        abbr = ctx.resolve_team(m.get("team_name"), where=f"yahoo_players_{year}.mapping_name")
        if abbr and abbr not in used and (m.get("fbp_abbr") or "").strip().casefold() not in {
            k.strip().casefold() for k in data
        }:
            leftover_maps.append(abbr)
    if len(leftover_keys) == 1 and len(set(leftover_maps)) == 1:
        resolved[leftover_keys[0]] = (leftover_maps[0], "paired with the only unclaimed mapping team")

    matched = unmatched = 0
    for key, players in data.items():
        abbr, how = resolved[key]
        if how:
            b.report.flag("history_roster_key", {"file": rel, "key": key, "team": abbr, "how": how})
        for p in players:
            upid = _match_player(ctx, p) if abbr else None
            if upid is None:
                h.raw(rel, {"team_key": key, "team_abbr": abbr, **p})
                unmatched += 1
                continue
            b.add("roster_assignments", {
                "upid": upid, "team_abbr": abbr, "contract_type": None, "contract_tier": None,
                "status_raw": None, "valid_from": valid_from, "valid_to": valid_to, "source": source,
            })
            matched += 1
    b.report.flag("history_roster_unmatched", {"file": rel, "matched": matched, "to_history_raw": unmatched})
    h.count(rel, "roster_assignments", matched)
    h.count(rel, "history_raw", unmatched)


# -------------------------------------------------------------------- drafts

def _load_draft_order(h: _History, rel: str) -> None:
    ctx, b = h.ctx, h.b
    data = _load(ctx, rel)
    if data.get("notes"):
        b.report.notes.append(f"{rel}: {data['notes']}")
    existing = {(r[0], r[1], r[2], r[3]) for r in b.rows["draft_picks"]}
    added = 0
    for pick in data.get("picks", []):
        key = (data.get("draft_type"), to_int(data.get("season")), to_int(pick.get("round")), to_int(pick.get("pick")))
        if key in existing:
            b.report.skip("draft_picks", "duplicate pick", file=rel, key=key)
            continue
        existing.add(key)
        b.add("draft_picks", {
            "draft": key[0], "season": key[1], "round": key[2], "pick": key[3],
            "team_abbr": ctx.resolve_team(pick.get("team"), where="draft_order_2025.team"),
            "round_type": pick.get("round_type"), "result": None, "raw": J(pick),
        })
        added += 1
    h.count(rel, "draft_picks", added)


def _load_draft_state(h: _History, rel: str) -> None:
    ctx, b = h.ctx, h.b
    data = _load(ctx, rel)
    h.raw(rel, data)
    h.count(rel, "history_raw", 1)
    draft, season = data.get("draft_type"), to_int(data.get("season"))
    index = {(r[0], r[1], r[2], r[3]): i for i, r in enumerate(b.rows["draft_picks"])}
    filled = added = 0
    for pick in data.get("picks_made", []):
        key = (draft, season, to_int(pick.get("round")), to_int(pick.get("pick")))
        names = ctx.players_by_norm_name.get(normalize_name(pick.get("player")), [])
        result = J({**pick, "upid": names[0] if len(names) == 1 else None})
        i = index.get(key)
        if i is not None:
            h.update("draft_picks", i, result=result)
            filled += 1
        else:
            b.add("draft_picks", {
                "draft": draft, "season": season, "round": key[2], "pick": key[3],
                "team_abbr": ctx.resolve_team(pick.get("team"), where=f"draft_state_{draft}_{season}.team"),
                "round_type": pick.get("round_type"), "result": result, "raw": J(pick),
            })
            index[key] = len(b.rows["draft_picks"]) - 1
            added += 1
    b.report.flag("history_draft_state", {"file": rel, "results_filled": filled, "picks_added": added})
    h.count(rel, "draft_picks", filled + added)


# ------------------------------------------------------------------- top 100

def _load_top100(h: _History, rel: str) -> None:
    ctx, b = h.ctx, h.b
    rows = b.rows["top100_snapshots"]
    current = [r[0] for r in rows if r[11] == "data/top100_prospects.json"]
    snap = git_commit_date(ctx.repo_root, rel)
    if snap is None or (current and snap == current[0]):
        b.report.flag("history_top100_date_fallback", {
            "file": rel, "git_date": str(snap), "used": str(FALLBACK_TOP100_DATE),
            "why": "no distinct git commit date for this file",
        })
        snap = FALLBACK_TOP100_DATE
    existing = {(r[0], r[1]) for r in rows}
    added = 0
    for r in _load(ctx, rel):
        rank = to_int(r.get("rank"))
        if rank is None or (snap, rank) in existing:
            b.report.skip("top100_snapshots", "missing or duplicate rank", source=rel, date=str(snap), rank=rank)
            continue
        existing.add((snap, rank))
        mlb_id = to_int(r.get("mlb_id"))
        upid = None if blank(r.get("upid")) else str(r["upid"])
        if upid not in ctx.player_upids:
            upid = ctx.upid_for_mlb_id(mlb_id)
        b.add("top100_snapshots", {
            "snapshot_date": snap, "rank": rank, "upid": upid, "mlb_id": mlb_id, "name": r.get("name"),
            "org": r.get("org"), "position": r.get("position"), "level": r.get("level"), "eta": r.get("eta"),
            "raw": J(r), "official": False, "source": rel,
        })
        added += 1
    h.count(rel, "top100_snapshots", added)


# ---------------------------------------------------------- generic raw files

def _csv_rows(path):
    """Yield dicts of header -> value. Blank/duplicate headers get col_N / name_N so no cell is lost."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return
        names, seen = [], set()
        for i, name in enumerate(header):
            name = name.strip() or f"col_{i}"
            while name in seen:
                name = f"{name}_{i}"
            seen.add(name)
            names.append(name)
        for cells in reader:
            row = dict(zip(names, cells))
            for i in range(len(names), len(cells)):  # cells beyond the header
                row[f"col_{i}"] = cells[i]
            yield row


def _load_csv(h: _History, rel: str) -> None:
    n = 0
    for row in _csv_rows(h.ctx.repo_root / rel):
        h.raw(rel, row)
        n += 1
    h.count(rel, "history_raw", n)


def _load_json_raw(h: _History, rel: str, *, team_key: bool = False) -> None:
    """One history_raw row per top-level entry (list element or dict key)."""
    data = _load(h.ctx, rel)
    n = 0
    if isinstance(data, list):
        for item in data:
            h.raw(rel, item)
            n += 1
    elif rel.endswith("yahoo_players_2026.json") and team_key:
        for key, players in data.items():
            for p in players:
                h.raw(rel, {"team_key": key, **p})
                n += 1
    elif rel.endswith("yahoo_2026_complete.json"):
        info = {"league_info": data.get("league_info"), "stat_categories": data.get("stat_categories"),
                "fetched_at": data.get("fetched_at")}
        h.raw(rel, info)
        n += 1
        for key, team in (data.get("teams") or {}).items():
            h.raw(rel, {"team_key": key, "team": team})
            n += 1
    else:
        for key, value in data.items():
            h.raw(rel, {"key": key, "value": value})
            n += 1
    h.count(rel, "history_raw", n)


# ---------------------------------------------------------------------- main

def build_history(ctx, b) -> None:
    h = _History(ctx, b)
    root = ctx.repo_root
    base = root / HIST
    if not base.exists():
        b.report.notes.append(f"{HIST} not found: history skipped")
        return

    def rel_of(p):
        return p.relative_to(root).as_posix()

    standings_date: dict[int, object] = {}
    for year in (2015, 2016, 2024, 2025):
        rel = f"{HIST}/{year}/standings_{year}.json"
        if (root / rel).exists():
            standings_date[year] = parse_date(_load(ctx, rel).get("date"))
            _load_standings(h, rel, year)
            h.handled.add(rel)
    for year in (2015, 2016, 2024, 2025):
        rel = f"{HIST}/{year}/yahoo_players_{year}.json"
        if (root / rel).exists():
            end = standings_date.get(year) or date(year, 12, 31)
            _load_rosters(h, rel, year, parse_ts(f"{end.isoformat()}T00:00:00"))
            h.handled.add(rel)

    rel = f"{HIST}/2025/draft_order_2025.json"
    if (root / rel).exists():
        _load_draft_order(h, rel)
        h.handled.add(rel)
    for name in ("draft_state_prospect_2025.json", "draft_state_keeper_2025.json"):
        rel = f"{HIST}/2025/{name}"
        if (root / rel).exists():
            _load_draft_state(h, rel)
            h.handled.add(rel)

    rel = f"{HIST}/2025/top100_prospects.json"
    if (root / rel).exists():
        _load_top100(h, rel)
        h.handled.add(rel)

    h.handled.add(TOP100_CSV)  # loaded by import_json
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = rel_of(path)
        if rel in h.handled:
            continue
        if path.suffix.lower() not in (".json", ".csv"):
            b.report.skip("history_raw", "not a json/csv data file", file=rel)
            continue
        if path.stat().st_size == 0:
            b.report.skip("history_raw", "0-byte file", file=rel)
            continue
        if path.suffix.lower() == ".csv":
            _load_csv(h, rel)
        else:
            _load_json_raw(h, rel, team_key=True)
        h.handled.add(rel)
