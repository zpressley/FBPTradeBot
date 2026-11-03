"""Full reload of data/*.json and config/*.json into Postgres (Spec 01, section 4).

Shadow mode: the JSON stays the source of truth. This script reads it,
never modifies it, and rebuilds the database mirror in ONE transaction:
truncate every imported table, reload everything, commit. If anything
fails the transaction rolls back and the database is left as it was.

Two phases:
  1. build: parse every source file into rows in memory (no database).
     This is where skips, unmapped values and generated ids are recorded.
  2. write: truncate + insert inside one transaction, then store the
     report in import_runs.

Usage:
    python scripts/db/import_json.py [--dry-run] [--database-url URL] [--repo-root DIR]

--dry-run runs the build phase only and prints the report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.db.common import (  # noqa: E402
    CONTRACT_TIER_MAP,
    CONTRACT_TYPE_MAP,
    blank,
    connect,
    normalize_name,
    parse_date,
    parse_ts,
    to_bool,
    to_int,
    to_num,
)

# Column lists are the contract between the build phase (this file and
# import_history.py) and the write phase. Keep them in sync with
# db/migrations/0001_init.sql.
TABLE_COLUMNS: dict[str, list[str]] = {
    "teams": ["abbr", "name", "discord_id", "yahoo_team_id", "division", "role", "active"],
    "team_name_aliases": ["alias", "team_abbr"],
    "seasons": ["year", "dates"],
    "team_seasons": ["team_abbr", "season", "final_rank", "standing", "division", "kap_rollover", "raw"],
    "players": [
        "upid", "name", "mlb_id", "yahoo_id", "bbref_id", "fangraphs_id", "birth_date", "bats",
        "throws", "height", "weight", "mlb_team", "positions", "mlb_primary_position",
        "player_type", "fypd", "rank", "raw",
    ],
    "player_aliases": ["alias_normalized", "upid"],
    "roster_assignments": [
        "upid", "team_abbr", "contract_type", "contract_tier", "status_raw", "valid_from",
        "valid_to", "source",
    ],
    "wb_ledger": [
        "txn_id", "ts", "team_abbr", "amount", "balance_before", "balance_after", "txn_type",
        "description", "related_upid", "season", "metadata", "source_txn_id", "raw",
    ],
    "trades": [
        "trade_id", "status", "initiator_team", "teams", "created_at", "expires_at",
        "manager_approved_at", "processed_at", "data_applied_at", "admin_decision_by",
        "rejection_reason", "rejected_by", "raw",
    ],
    "trade_transfers": ["trade_id", "type", "upid", "from_team", "to_team", "amount", "raw"],
    "player_events": [
        "id", "season", "ts", "update_type", "event", "source", "admin", "upid", "player_name",
        "owner_raw", "owner_abbr", "contract", "status_raw", "source_file", "id_generated", "raw",
    ],
    "draft_picks": ["draft", "season", "round", "pick", "team_abbr", "round_type", "result", "raw"],
    "top100_snapshots": [
        "snapshot_date", "rank", "upid", "mlb_id", "name", "org", "position", "level", "eta",
        "raw", "official", "source",
    ],
    "player_season_stats": [
        "upid", "season", "level", "stat_type", "source", "mlb_id", "mlb_team", "games",
        "plate_appearances", "at_bats", "runs", "hits", "doubles", "triples", "home_runs", "rbi",
        "stolen_bases", "caught_stealing", "base_on_balls", "strike_outs", "avg", "obp", "slg",
        "ops", "innings_pitched", "era", "whip", "wins", "losses", "saves", "holds",
        "games_started", "earned_runs", "raw",
    ],
    "app_state": ["key", "value", "updated_at"],
    "history_raw": ["source_file", "row_num", "row"],
}

# Load order (spec 01, section 4) = insert order; truncation happens once up front.
LOAD_ORDER = [
    "teams", "team_name_aliases", "seasons", "team_seasons", "players", "player_aliases",
    "roster_assignments", "wb_ledger", "trades", "trade_transfers", "player_events",
    "draft_picks", "top100_snapshots", "player_season_stats", "app_state", "history_raw",
]

# Small state files preserved whole in app_state until their area moves over.
APP_STATE_FILES = [
    "auction_current", "auction_resolved_state", "trade_id_state", "draft_state_keeper_2026",
    "draft_state_prospect_2026", "kap_submissions", "pad_submissions_2026", "manager_boards_2026",
    "standings", "standings_refresh_backoff_state", "roster_sync_state", "team_colors",
    "site_theme", "team_planner_plans", "manager_notes", "player_add_requests",
    "forklift_mode_keeper_2026", "forklift_mode_prospect_2026",
]

# Historical team names seen in the logs that no mapping file covers (spec 01 example).
EXTRA_TEAM_ALIASES = {
    "Jeppie Torrs": "JEP",
}


class Json:
    """jsonb parameter wrapper: psycopg adapts dict/list through psycopg.types.json."""

    def __init__(self, value):
        self.value = value


def J(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, list] = field(default_factory=dict)   # table -> [{reason, ...}]
    flags: dict[str, list] = field(default_factory=dict)     # topic -> [...]
    notes: list[str] = field(default_factory=list)

    def skip(self, table: str, reason: str, **detail):
        self.skipped.setdefault(table, []).append({"reason": reason, **detail})

    def flag(self, topic: str, item):
        self.flags.setdefault(topic, []).append(item)

    def as_dict(self) -> dict:
        return {
            "counts": self.counts,
            "skipped": {k: len(v) for k, v in self.skipped.items()},
            "skipped_detail": self.skipped,
            "flags": {k: len(v) for k, v in self.flags.items()},
            "flag_detail": self.flags,
            "notes": self.notes,
        }

    def print(self, verbose: bool = False) -> None:
        print("\n== rows per table ==")
        for table in LOAD_ORDER:
            print(f"  {table:22s} {self.counts.get(table, 0):>7d}")
        if self.skipped:
            print("\n== skipped rows ==")
            for table, items in self.skipped.items():
                reasons: dict[str, int] = {}
                for it in items:
                    reasons[it["reason"]] = reasons.get(it["reason"], 0) + 1
                for reason, n in reasons.items():
                    print(f"  {table}: {n} ({reason})")
                if verbose:
                    for it in items[:50]:
                        print("     ", {k: v for k, v in it.items() if k != "reason"})
        if self.flags:
            print("\n== flags (reported, not fixed) ==")
            for topic, items in self.flags.items():
                print(f"  {topic}: {len(items)}")
                for it in items[: (len(items) if verbose else 10)]:
                    print("     ", it)
                if not verbose and len(items) > 10:
                    print(f"      ... {len(items) - 10} more (use --verbose)")
        for n in self.notes:
            print("note:", n)


@dataclass
class Bundle:
    """Rows to insert, keyed by table, in TABLE_COLUMNS order."""

    rows: dict[str, list[tuple]] = field(default_factory=lambda: {t: [] for t in TABLE_COLUMNS})
    report: Report = field(default_factory=Report)

    def add(self, table: str, row: dict) -> None:
        cols = TABLE_COLUMNS[table]
        unknown = set(row) - set(cols)
        if unknown:
            raise KeyError(f"{table}: unknown columns {sorted(unknown)}")
        self.rows[table].append(tuple(row.get(c) for c in cols))


@dataclass
class Context:
    repo_root: Path
    import_time: datetime
    teams: dict[str, dict] = field(default_factory=dict)          # abbr -> team row
    alias_to_abbr: dict[str, str] = field(default_factory=dict)   # casefolded alias -> abbr
    ambiguous_aliases: set[str] = field(default_factory=set)
    player_upids: set[str] = field(default_factory=set)
    players_by_mlb_id: dict[int, str] = field(default_factory=dict)
    players_by_yahoo_id: dict[str, str] = field(default_factory=dict)
    players_by_norm_name: dict[str, list[str]] = field(default_factory=dict)
    unresolved_teams: dict[str, int] = field(default_factory=dict)

    @property
    def data(self) -> Path:
        return self.repo_root / "data"

    @property
    def config(self) -> Path:
        return self.repo_root / "config"

    def load_json(self, rel: str):
        with open(self.repo_root / rel, encoding="utf-8") as f:
            return json.load(f)

    def add_alias(self, alias: str | None, abbr: str, bundle: Bundle) -> None:
        if blank(alias):
            return
        key = str(alias).strip().casefold()
        if not key:
            return
        existing = self.alias_to_abbr.get(key)
        if existing is None:
            if key in self.ambiguous_aliases:
                return
            self.alias_to_abbr[key] = abbr
            bundle.add("team_name_aliases", {"alias": str(alias).strip(), "team_abbr": abbr})
        elif existing != abbr:
            # the same text names two different teams: unusable as an alias
            self.ambiguous_aliases.add(key)
            del self.alias_to_abbr[key]
            bundle.rows["team_name_aliases"] = [
                r for r in bundle.rows["team_name_aliases"] if r[0].casefold() != key
            ]
            bundle.report.flag("ambiguous_team_alias", {"alias": alias, "teams": [existing, abbr]})

    def resolve_team(self, value, *, where: str) -> str | None:
        """Resolve a team name/abbreviation through the alias table. None if unknown."""
        if blank(value):
            return None
        key = str(value).strip().casefold()
        abbr = self.alias_to_abbr.get(key)
        if abbr is None:
            self.unresolved_teams[f"{where}: {str(value).strip()}"] = (
                self.unresolved_teams.get(f"{where}: {str(value).strip()}", 0) + 1
            )
        return abbr

    def upid_for_mlb_id(self, mlb_id) -> str | None:
        mid = to_int(mlb_id)
        return self.players_by_mlb_id.get(mid) if mid else None


# ------------------------------------------------------------------ helpers

def sha1(*parts) -> str:
    return hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def git_commit_date(repo_root: Path, rel: str) -> date | None:
    """Date of the last commit touching rel, or None (read-only git use)."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", rel],
            cwd=repo_root, capture_output=True, text=True, timeout=20, check=False,
        )
        s = out.stdout.strip()
        return date.fromisoformat(s) if s else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def synth_abbr(name: str, taken: set[str]) -> str:
    initials = "".join(w[0] for w in name.replace("'", "").split() if w and w[0].isalnum()).upper()
    base = (initials or name.strip().upper().replace(" ", ""))[:4] or "TEAM"
    cand, n = base, 2
    while cand in taken:
        cand = f"{base[:3]}{n}"
        n += 1
    return cand


# -------------------------------------------------------------------- teams

def build_teams(ctx: Context, b: Bundle) -> None:
    managers = ctx.load_json("config/managers.json")
    for abbr, t in managers["teams"].items():
        row = {
            "abbr": abbr,
            "name": t["name"],
            "discord_id": t.get("discord_id"),
            "yahoo_team_id": t.get("yahoo_team_id"),
            "division": t.get("division"),
            "role": t.get("role") or None,
            "active": True,
        }
        ctx.teams[abbr] = row
        b.add("teams", row)
        ctx.add_alias(abbr, abbr, b)
        ctx.add_alias(t.get("name"), abbr, b)
        ctx.add_alias(t.get("full_name"), abbr, b)
        ctx.add_alias(t.get("manager"), abbr, b)
        if t.get("final_rank_2025") is not None:
            b.add("team_seasons", {
                "team_abbr": abbr, "season": 2025, "final_rank": to_int(t.get("final_rank_2025")),
                "raw": J({"final_rank_2025": t.get("final_rank_2025")}),
            })
        b.add("team_seasons", {
            "team_abbr": abbr, "season": 2026,
            "standing": to_int(t.get("standing")),
            "division": t.get("division"),
            "kap_rollover": to_int(t.get("kap_rollover_2026")),
            "raw": J({"standing": t.get("standing"), "kap_rollover_2026": t.get("kap_rollover_2026")}),
        })

    # Historical teams from data/historical/<year>/team_mappings_<year>.json.
    # A mapping abbreviation that already names a current team with a
    # different spelling of the name ('JEP' / 'Jepordizers') is the same
    # franchise: the name becomes an alias. Anything else becomes an
    # inactive team row so historical rosters and events can reference it.
    names_taken = {t["name"].casefold(): a for a, t in ctx.teams.items()}
    seasons_seen: set[tuple[str, int]] = {(r[0], r[1]) for r in b.rows["team_seasons"]}
    for mapping_file in sorted((ctx.data / "historical").glob("*/team_mappings_*.json")):
        year = to_int(mapping_file.parent.name)
        rel = mapping_file.relative_to(ctx.repo_root).as_posix()
        for m in json.load(open(mapping_file, encoding="utf-8")):
            raw_abbr = (m.get("fbp_abbr") or "").strip().upper()
            name = (m.get("team_name") or "").strip()
            if not name:
                b.report.skip("teams", "mapping row without team_name", file=rel, row=m)
                continue
            abbr = names_taken.get(name.casefold())
            if abbr is None and raw_abbr in ctx.teams:
                abbr = raw_abbr  # same franchise, renamed
            if abbr is None:
                abbr = raw_abbr if raw_abbr and raw_abbr not in ctx.teams else synth_abbr(name, set(ctx.teams))
                if abbr != raw_abbr:
                    b.report.flag("synthesized_team_abbr", {"name": name, "mapping_abbr": raw_abbr, "abbr": abbr, "file": rel})
                row = {
                    "abbr": abbr, "name": name, "discord_id": None,
                    "yahoo_team_id": m.get("yahoo_team_id"), "division": None, "role": None,
                    "active": False,
                }
                ctx.teams[abbr] = row
                names_taken[name.casefold()] = abbr
                b.add("teams", row)
                ctx.add_alias(abbr, abbr, b)
            ctx.add_alias(name, abbr, b)
            if raw_abbr:
                ctx.add_alias(raw_abbr, abbr, b)
            if year and (abbr, year) not in seasons_seen:
                seasons_seen.add((abbr, year))
                b.add("team_seasons", {"team_abbr": abbr, "season": year, "raw": J(m)})

    for alias, abbr in EXTRA_TEAM_ALIASES.items():
        ctx.add_alias(alias, abbr, b)
    b.report.counts["teams"] = len(b.rows["teams"])


def build_seasons(ctx: Context, b: Bundle) -> None:
    dates = ctx.load_json("config/season_dates.json")
    year = to_int(dates.get("season_year")) or ctx.import_time.year
    b.add("seasons", {"year": year, "dates": J(dates)})


# ------------------------------------------------------------------ players

def build_players(ctx: Context, b: Bundle) -> None:
    rows = ctx.load_json("data/combined_players.json")
    seen: set[str] = set()
    for r in rows:
        upid = (r.get("upid") or "").strip() if isinstance(r.get("upid"), str) else r.get("upid")
        upid = str(upid).strip() if upid not in (None, "") else ""
        if not upid:
            b.report.skip("players", "blank upid", name=r.get("name"), mlb_id=r.get("mlb_id"))
            continue
        if upid in seen:
            b.report.skip("players", "duplicate upid", upid=upid, name=r.get("name"))
            continue
        seen.add(upid)
        mlb_id = to_int(r.get("mlb_id"))
        if mlb_id and mlb_id in ctx.players_by_mlb_id:
            b.report.flag("duplicate_mlb_id", {"mlb_id": mlb_id, "upids": [ctx.players_by_mlb_id[mlb_id], upid], "name": r.get("name")})
        elif mlb_id:
            ctx.players_by_mlb_id[mlb_id] = upid
        yid = None if blank(r.get("yahoo_id")) else str(r.get("yahoo_id")).strip()
        if yid and yid not in ctx.players_by_yahoo_id:
            ctx.players_by_yahoo_id[yid] = upid
        ctx.player_upids.add(upid)
        ctx.players_by_norm_name.setdefault(normalize_name(r.get("name")), []).append(upid)
        fypd = to_bool(r.get("fypd"))
        b.add("players", {
            "upid": upid,
            "name": r.get("name") or r.get("player_name") or f"UPID {upid}",
            "mlb_id": mlb_id,
            "yahoo_id": yid,
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
            "fypd": fypd,
            "rank": to_int(r.get("rank")),
            "raw": J(r),
        })
    b.report.counts["players"] = len(b.rows["players"])
    build_roster_assignments(ctx, b, rows)


def build_player_aliases(ctx: Context, b: Bundle) -> None:
    db = ctx.load_json("data/upid_database.json")
    seen: set[tuple[str, str]] = set()
    for alias, upids in db.get("name_index", {}).items():
        norm = normalize_name(alias)
        if not norm:
            continue
        for upid in (upids if isinstance(upids, list) else [upids]):
            upid = str(upid).strip()
            if upid not in ctx.player_upids:
                b.report.skip("player_aliases", "upid not in players", alias=alias, upid=upid)
                continue
            if (norm, upid) in seen:
                continue
            seen.add((norm, upid))
            b.add("player_aliases", {"alias_normalized": norm, "upid": upid})
    # every player's own name is an alias too
    for row in b.rows["players"]:
        upid, name = row[0], row[1]
        norm = normalize_name(name)
        if norm and (norm, upid) not in seen:
            seen.add((norm, upid))
            b.add("player_aliases", {"alias_normalized": norm, "upid": upid})
    b.report.counts["player_aliases"] = len(b.rows["player_aliases"])


def build_roster_assignments(ctx: Context, b: Bundle, players: list[dict]) -> None:
    """Current roster = one open row per owned player (spec 01, roster_assignments)."""
    for r in players:
        upid = str(r.get("upid") or "").strip()
        if not upid or upid not in ctx.player_upids:
            continue
        fbp_team_raw = r.get("FBP_Team")
        manager_raw = r.get("manager")
        if fbp_team_raw is None or (isinstance(fbp_team_raw, str) and fbp_team_raw.strip() == "None"):
            # spec 01 known issue: rows whose FBP_Team is "None"/null instead of ""
            b.report.flag("fbp_team_none", {"upid": upid, "name": r.get("name"), "FBP_Team": fbp_team_raw, "manager": manager_raw})
        fbp_blank = blank(fbp_team_raw)
        mgr_blank = blank(manager_raw)
        if fbp_blank and mgr_blank:
            continue  # unowned
        fbp_abbr = None if fbp_blank else ctx.resolve_team(fbp_team_raw, where="combined_players.FBP_Team")
        mgr_abbr = None if mgr_blank else ctx.resolve_team(manager_raw, where="combined_players.manager")
        if fbp_abbr and mgr_abbr and fbp_abbr == mgr_abbr:
            team = fbp_abbr
        else:
            # manager and FBP_Team disagree (including one blank, or an
            # unresolvable name): import with team_abbr null and let an admin
            # decide with the evidence reconcile prints (spec 01, decided 10/27)
            team = None
            b.report.flag("team_disagreement", {
                "upid": upid, "name": r.get("name"),
                "FBP_Team": fbp_team_raw, "manager": manager_raw,
                "FBP_Team_resolved": fbp_abbr, "manager_resolved": mgr_abbr,
            })
        ctype_raw = r.get("contract_type")
        ctype = None if blank(ctype_raw) else CONTRACT_TYPE_MAP.get(ctype_raw)
        if ctype is None and not blank(ctype_raw):
            ctype = ctype_raw
            b.report.flag("unmapped_contract_type", {"upid": upid, "value": ctype_raw})
        tier_raw = r.get("years_simple")
        tier = None if blank(tier_raw) else CONTRACT_TIER_MAP.get(str(tier_raw).strip())
        if tier is None and not blank(tier_raw):
            tier = str(tier_raw).strip()
            b.report.flag("unmapped_contract_tier", {"upid": upid, "value": tier_raw})
        b.add("roster_assignments", {
            "upid": upid, "team_abbr": team, "contract_type": ctype, "contract_tier": tier,
            "status_raw": None if blank(r.get("status")) else r.get("status"),
            "valid_from": ctx.import_time, "valid_to": None, "source": "json_import",
        })
    b.report.counts["roster_assignments"] = len(b.rows["roster_assignments"])


# -------------------------------------------------------------------- money

def build_wb_ledger(ctx: Context, b: Bundle) -> None:
    rows = ctx.load_json("data/wizbucks_transactions.json")
    seen: dict[str, int] = {}
    generated = 0
    deduped = 0
    for i, r in enumerate(rows):
        team = ctx.resolve_team(r.get("team") or r.get("team_name"), where="wizbucks_transactions.team")
        if team is None:
            b.report.skip("wb_ledger", "unresolved team", row=i, team=r.get("team"), txn_id=r.get("txn_id"))
            continue
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            b.report.skip("wb_ledger", "unparseable timestamp", row=i, timestamp=r.get("timestamp"))
            continue
        source_id = r.get("txn_id") or r.get("id")
        txn_id = source_id
        if blank(txn_id):
            txn_id = "gen_" + sha1(r.get("timestamp"), team, r.get("amount"), r.get("transaction_type"), r.get("description"))[:20]
            generated += 1
            source_id = None
        if txn_id in seen:
            seen[txn_id] += 1
            txn_id = f"{txn_id}#{seen[txn_id]}"
            deduped += 1
            b.report.flag("duplicate_wb_txn_id", {"txn_id": source_id, "row": i})
        else:
            seen[txn_id] = 1
        related = r.get("related_player") or {}
        meta = r.get("metadata") or {}
        b.add("wb_ledger", {
            "txn_id": txn_id, "ts": ts, "team_abbr": team,
            "amount": to_int(r.get("amount")) or 0,
            "balance_before": to_int(r.get("balance_before")),
            "balance_after": to_int(r.get("balance_after")),
            "txn_type": r.get("transaction_type") or "unknown",
            "description": r.get("description"),
            "related_upid": None if blank(related.get("upid")) else str(related.get("upid")),
            "season": to_int(meta.get("season")),
            "metadata": J(meta),
            "source_txn_id": None if txn_id == source_id else source_id,
            "raw": J(r),
        })
    b.report.counts["wb_ledger"] = len(b.rows["wb_ledger"])
    if generated:
        b.report.notes.append(f"wb_ledger: generated {generated} txn_ids for rows that had none")
    if deduped:
        b.report.notes.append(f"wb_ledger: {deduped} duplicate txn_ids suffixed with #n (original kept in source_txn_id)")


# ------------------------------------------------------------------- trades

def build_trades(ctx: Context, b: Bundle) -> None:
    trades = ctx.load_json("data/trades.json")
    items = list(trades.values()) if isinstance(trades, dict) else list(trades)
    pending_path = ctx.data / "pending_trades.json"
    if pending_path.exists():
        pending = json.load(open(pending_path, encoding="utf-8"))
        items += list(pending.values()) if isinstance(pending, dict) else list(pending)
    seen: set[str] = set()
    for t in items:
        tid = t.get("trade_id")
        if blank(tid):
            b.report.skip("trades", "missing trade_id", teams=t.get("teams"))
            continue
        if tid in seen:
            b.report.skip("trades", "duplicate trade_id", trade_id=tid)
            continue
        seen.add(tid)
        b.add("trades", {
            "trade_id": tid, "status": t.get("status"), "initiator_team": t.get("initiator_team"),
            "teams": list(t.get("teams") or []),
            "created_at": parse_ts(t.get("created_at")), "expires_at": parse_ts(t.get("expires_at")),
            "manager_approved_at": parse_ts(t.get("manager_approved_at")),
            "processed_at": parse_ts(t.get("processed_at")),
            "data_applied_at": parse_ts(t.get("data_applied_at")),
            "admin_decision_by": t.get("admin_decision_by"),
            "rejection_reason": t.get("rejection_reason"), "rejected_by": t.get("rejected_by"),
            "raw": J(t),
        })
        for x in t.get("transfers") or []:
            upid = None if blank(x.get("upid")) else str(x.get("upid"))
            if upid and upid not in ctx.player_upids:
                b.report.flag("trade_transfer_unknown_upid", {"trade_id": tid, "upid": upid})
            b.add("trade_transfers", {
                "trade_id": tid, "type": x.get("type") or "unknown", "upid": upid,
                "from_team": x.get("from_team"), "to_team": x.get("to_team"),
                "amount": to_int(x.get("amount")), "raw": J(x),
            })
    b.report.counts["trades"] = len(b.rows["trades"])
    b.report.counts["trade_transfers"] = len(b.rows["trade_transfers"])


# ------------------------------------------------------------ event history

def build_player_events(ctx: Context, b: Bundle) -> None:
    seen: set[str] = set()
    generated = 0
    for rel, source_file in (("data/transactions_history.json", "transactions_history"),
                             ("data/player_log.json", "player_log")):
        for i, r in enumerate(ctx.load_json(rel)):
            rid = r.get("id")
            gen = False
            if blank(rid):
                rid = "gen_" + sha1(r.get("season"), r.get("timestamp"), r.get("upid"), r.get("player_name"), r.get("update_type"), r.get("event"))
                gen = True
                generated += 1
            if rid in seen:
                b.report.skip("player_events", "duplicate id", id=rid, source_file=source_file, row=i)
                continue
            seen.add(rid)
            ts = parse_ts(r.get("timestamp"))
            if ts is None and not blank(r.get("timestamp")):
                b.report.flag("unparseable_event_timestamp", {"id": rid, "timestamp": r.get("timestamp")})
            owner_raw = None if blank(r.get("owner")) else str(r.get("owner")).strip()
            owner_abbr = ctx.resolve_team(owner_raw, where=f"{source_file}.owner") if owner_raw else None
            upid = None if blank(r.get("upid")) else str(r.get("upid")).strip()
            b.add("player_events", {
                "id": rid, "season": to_int(r.get("season")), "ts": ts,
                "update_type": r.get("update_type"), "event": r.get("event"),
                "source": r.get("source"), "admin": r.get("admin"), "upid": upid,
                "player_name": r.get("player_name"), "owner_raw": owner_raw, "owner_abbr": owner_abbr,
                "contract": r.get("contract"), "status_raw": r.get("status"),
                "source_file": source_file, "id_generated": gen, "raw": J(r),
            })
    b.report.counts["player_events"] = len(b.rows["player_events"])
    b.report.notes.append(f"player_events: generated {generated} ids")


# ------------------------------------------------------ drafts and prospects

def build_draft_picks(ctx: Context, b: Bundle) -> None:
    seen: set[tuple] = set()
    rows = ctx.load_json("data/draft_order_2026.json")
    for i, r in enumerate(rows):
        if "draft" not in r:
            if set(r) == {"_comment"}:
                continue
            b.report.skip("draft_picks", "row without draft key", row=i, keys=sorted(r))
            continue
        key = (r["draft"], 2026, to_int(r.get("round")), to_int(r.get("pick")))
        if key in seen:
            b.report.skip("draft_picks", "duplicate pick", key=key)
            continue
        seen.add(key)
        team = ctx.resolve_team(r.get("current_owner") or r.get("team"), where="draft_order_2026.team")
        b.add("draft_picks", {
            "draft": key[0], "season": 2026, "round": key[2], "pick": key[3],
            "team_abbr": team, "round_type": r.get("round_type"),
            "result": J(r.get("result")) if r.get("result") is not None else None,
            "raw": J(r),
        })
    b.report.counts["draft_picks"] = len(b.rows["draft_picks"])


def build_top100(ctx: Context, b: Bundle) -> None:
    seen: set[tuple] = set()

    def add(snapshot_date, rank, row, *, source, official=False, upid=None, mlb_id=None,
            name=None, org=None, position=None, level=None, eta=None):
        key = (snapshot_date, rank)
        if rank is None or key in seen:
            b.report.skip("top100_snapshots", "missing or duplicate rank", source=source, date=str(snapshot_date), rank=rank)
            return
        seen.add(key)
        b.add("top100_snapshots", {
            "snapshot_date": snapshot_date, "rank": rank, "upid": upid, "mlb_id": mlb_id,
            "name": name, "org": org, "position": position, "level": level, "eta": eta,
            "raw": J(row), "official": official, "source": source,
        })

    # data/top100_prospects.json: the current hub list; date = last commit date
    rel = "data/top100_prospects.json"
    snap_date = git_commit_date(ctx.repo_root, rel) or ctx.import_time.date()
    for r in ctx.load_json(rel):
        upid = None if blank(r.get("upid")) else str(r["upid"])
        if upid and upid not in ctx.player_upids:
            b.report.flag("top100_unknown_upid", {"source": rel, "upid": upid, "name": r.get("name")})
            upid = None
        add(snap_date, to_int(r.get("rank")), r, source=rel, upid=upid, mlb_id=to_int(r.get("mlb_id")),
            name=r.get("name"), org=r.get("org"), position=r.get("position"), level=r.get("level"), eta=r.get("eta"))

    # Nov 1 2025 hand-built CSV (no header): rank,name,pos,org,level,eta,age,ht/wt,bats,throws
    rel = "data/historical/2025/Nov 1 2025 MLB Top 100 Prospects.csv"
    with open(ctx.repo_root / rel, encoding="utf-8", newline="") as f:
        for i, cells in enumerate(csv.reader(f)):
            if not cells or not cells[0].strip().isdigit():
                continue
            name = cells[1].strip() if len(cells) > 1 else ""
            upid = _upid_by_name(ctx, name)
            row = {"rank": cells[0], "name": name, "position": _cell(cells, 2), "org": _cell(cells, 3),
                   "level": _cell(cells, 4), "eta": _cell(cells, 5), "age": _cell(cells, 6),
                   "height_weight": _cell(cells, 7), "bats": _cell(cells, 8), "throws": _cell(cells, 9),
                   "row_num": i}
            if upid is None:
                b.report.flag("top100_unmatched_name", {"source": rel, "name": name, "rank": cells[0]})
            add(date(2025, 11, 1), to_int(cells[0]), row, source=rel, official=True, upid=upid,
                name=name, org=row["org"], position=row["position"], level=row["level"], eta=row["eta"])

    # data/pipeline_snapshots/*.json: monthly scrapes, including the Nov 1 2026 list
    for path in sorted((ctx.data / "pipeline_snapshots").glob("*.json")):
        rel = path.relative_to(ctx.repo_root).as_posix()
        snap = json.load(open(path, encoding="utf-8"))
        snap_date = parse_date(snap.get("date")) or parse_date(path.stem)
        if snap_date is None:
            b.report.skip("top100_snapshots", "snapshot without a date", source=rel)
            continue
        official = bool(snap.get("official"))
        for r in snap.get("players") or []:
            mlb_id = to_int(r.get("mlb_id"))
            upid = None if blank(r.get("upid")) else str(r["upid"])
            if upid is None and mlb_id:
                upid = ctx.upid_for_mlb_id(mlb_id)
            if upid is None:
                upid = _upid_by_name(ctx, r.get("name"))
            if upid is not None and upid not in ctx.player_upids:
                upid = None
            add(snap_date, to_int(r.get("rank")), r, source=rel, official=official, upid=upid, mlb_id=mlb_id,
                name=r.get("name"), org=r.get("org") or (r.get("stats") or {}).get("Tm") or None,
                position=r.get("position"), level=r.get("level"), eta=r.get("eta"))
    b.report.counts["top100_snapshots"] = len(b.rows["top100_snapshots"])


def _cell(cells: list[str], i: int) -> str | None:
    return cells[i].strip() if i < len(cells) and cells[i].strip() else None


def _upid_by_name(ctx: Context, name) -> str | None:
    cands = ctx.players_by_norm_name.get(normalize_name(name), [])
    return cands[0] if len(cands) == 1 else None


STAT_COLUMNS = {
    "games": ("games", to_int), "plate_appearances": ("plateAppearances", to_int),
    "at_bats": ("atBats", to_int), "runs": ("runs", to_int), "hits": ("hits", to_int),
    "doubles": ("doubles", to_int), "triples": ("triples", to_int), "home_runs": ("homeRuns", to_int),
    "rbi": ("rbi", to_int), "stolen_bases": ("stolenBases", to_int),
    "caught_stealing": ("caughtStealing", to_int), "base_on_balls": ("baseOnBalls", to_int),
    "strike_outs": ("strikeOuts", to_int), "avg": ("avg", to_num), "obp": ("obp", to_num),
    "slg": ("slg", to_num), "ops": ("ops", to_num), "innings_pitched": ("inningsPitched", to_num),
    "era": ("era", to_num), "whip": ("whip", to_num), "wins": ("wins", to_int),
    "losses": ("losses", to_int), "saves": ("saves", to_int), "holds": ("holds", to_int),
    "games_started": ("gamesStarted", to_int), "earned_runs": ("earnedRuns", to_int),
}


def build_player_season_stats(ctx: Context, b: Bundle) -> None:
    rows = ctx.load_json("data/player_stats.json")
    seen: set[tuple] = set()
    for i, r in enumerate(rows):
        key = (str(r.get("upid") or "").strip(), to_int(r.get("season")), str(r.get("level") or ""),
               str(r.get("stat_type") or ""), str(r.get("source") or ""))
        if not key[0] or key[1] is None:
            b.report.skip("player_season_stats", "missing upid or season", row=i, name=r.get("player_name"))
            continue
        if key in seen:
            b.report.skip("player_season_stats", "duplicate key", key=key)
            continue
        seen.add(key)
        if key[0] not in ctx.player_upids:
            b.report.flag("stats_unknown_upid", {"upid": key[0], "name": r.get("player_name")})
        row = {"upid": key[0], "season": key[1], "level": key[2], "stat_type": key[3], "source": key[4],
               "mlb_id": to_int(r.get("mlb_id")), "mlb_team": r.get("mlb_team"), "raw": J(r)}
        for col, (src, conv) in STAT_COLUMNS.items():
            row[col] = conv(r.get(src))
        b.add("player_season_stats", row)
    b.report.counts["player_season_stats"] = len(b.rows["player_season_stats"])


# ----------------------------------------------------------- catch-all state

def build_app_state(ctx: Context, b: Bundle) -> None:
    for key in APP_STATE_FILES:
        path = ctx.data / f"{key}.json"
        if not path.exists():
            b.report.skip("app_state", "file missing", key=key)
            continue
        try:
            value = json.load(open(path, encoding="utf-8"))
        except json.JSONDecodeError as e:
            b.report.skip("app_state", f"invalid json: {e}", key=key)
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        b.add("app_state", {"key": key, "value": J(value), "updated_at": mtime})
    b.report.counts["app_state"] = len(b.rows["app_state"])


# -------------------------------------------------------------------- build

def build(ctx: Context) -> Bundle:
    b = Bundle()
    t0 = time.time()
    build_teams(ctx, b)
    build_seasons(ctx, b)
    build_players(ctx, b)          # also roster_assignments
    build_player_aliases(ctx, b)
    build_wb_ledger(ctx, b)
    build_trades(ctx, b)
    build_player_events(ctx, b)
    build_draft_picks(ctx, b)
    build_top100(ctx, b)
    build_player_season_stats(ctx, b)
    build_app_state(ctx, b)
    try:
        from scripts.db.import_history import build_history
    except ImportError:
        build_history = None
    if build_history is not None:
        build_history(ctx, b)
    for table in TABLE_COLUMNS:
        b.report.counts[table] = len(b.rows[table])
    if ctx.unresolved_teams:
        for k, n in sorted(ctx.unresolved_teams.items(), key=lambda kv: -kv[1]):
            b.report.flag("unresolved_team_reference", {"where_value": k, "rows": n})
    b.report.notes.append(f"build phase took {time.time() - t0:.1f}s")
    return b


# -------------------------------------------------------------------- write

def write(conn, b: Bundle) -> int:
    """Truncate + insert everything in one transaction. Returns the import_runs id."""
    t0 = time.time()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_xact_lock(hashtext('import_json'))")
            cur.execute("truncate table " + ", ".join(LOAD_ORDER) + " restart identity cascade")
            for table in LOAD_ORDER:
                rows = b.rows[table]
                if not rows:
                    continue
                cols = TABLE_COLUMNS[table]
                sql = f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})"
                cur.executemany(sql, rows)
            b.report.notes.append(f"write phase took {time.time() - t0:.1f}s")
            cur.execute(
                "insert into import_runs (finished_at, report) values (now(), %s) returning id",
                (J(b.report.as_dict()),),
            )
            return cur.fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reload data/*.json into Postgres (shadow mirror).")
    ap.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    ap.add_argument("--verbose", action="store_true", help="print every skipped row and flag")
    args = ap.parse_args(argv)

    ctx = Context(repo_root=Path(args.repo_root), import_time=datetime.now(timezone.utc))
    bundle = build(ctx)
    if args.dry_run:
        bundle.report.print(verbose=args.verbose)
        print("\n--dry-run: nothing written.")
        return 0
    with connect(args.database_url) as conn:
        run_id = write(conn, bundle)
    bundle.report.print(verbose=args.verbose)
    print(f"\nimport committed (import_runs.id = {run_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
