"""Tests for scripts/db/import_history.py using small fixtures in a temp repo."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.db.import_history import _csv_rows, build_history  # noqa: E402
from scripts.db.import_json import TABLE_COLUMNS, Bundle, Context, J  # noqa: E402


def write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def make_ctx(root):
    ctx = Context(repo_root=root, import_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = Bundle()
    for abbr, name in [("WIZ", "Whiz Kids"), ("JEP", "Jepordizers"), ("RIZZ", "Rizzo's Hizzos"),
                       ("THE", "The Ice Cream Man"), ("LIG", "Lights Out")]:
        ctx.teams[abbr] = {"abbr": abbr}
        ctx.add_alias(abbr, abbr, b)
        ctx.add_alias(name, abbr, b)
    # existing team_seasons rows, as import_json creates them
    b.add("team_seasons", {"team_abbr": "WIZ", "season": 2025, "final_rank": 1, "raw": J({})})
    ctx.player_upids.update({"100", "200", "300"})
    ctx.players_by_yahoo_id["9001"] = "100"
    ctx.players_by_norm_name["bob smith"] = ["200"]
    ctx.players_by_norm_name["twin"] = ["300", "301"]
    return ctx, b


def cols(b, table, i=0):
    return dict(zip(TABLE_COLUMNS[table], b.rows[table][i]))


def test_standings_update_in_place(tmp_path):
    ctx, b = make_ctx(tmp_path)
    write(tmp_path, "data/historical/2025/standings_2025.json", {
        "season": 2025, "date": "2025-09-30",
        "standings": [{"rank": 3, "team": "WIZ", "team_name": "Whiz Kids"},
                      {"rank": 4, "team": "??", "team_name": "Nobody FC"}]})
    build_history(ctx, b)
    assert len(b.rows["team_seasons"]) == 1
    row = cols(b, "team_seasons")
    assert row["standing"] == 3 and row["final_rank"] == 1  # existing final_rank kept
    assert [f["team_name"] for f in b.report.flags["history_standings_unresolved_team"]] == ["Nobody FC"]
    assert [r[2].obj["team_name"] for r in b.rows["history_raw"]] == ["Nobody FC"]


def test_rosters_mangled_key_ambiguous_and_matching(tmp_path):
    ctx, b = make_ctx(tmp_path)
    write(tmp_path, "data/historical/2015/standings_2015.json", {"season": 2015, "date": "2015-09-30", "standings": []})
    write(tmp_path, "data/historical/2015/team_mappings_2015.json", [
        {"team_name": "Whiz Kids", "fbp_abbr": "WIZ"},
        {"team_name": "The Ice Cream Man", "fbp_abbr": "THE "},
        {"team_name": "The Big Lebowski", "fbp_abbr": "THE "},
        {"team_name": "Jepordizers", "fbp_abbr": "JEP"},
    ])
    write(tmp_path, "data/historical/2015/yahoo_players_2015.json", {
        "WIZ": [{"name": "A", "yahoo_id": "9001"},          # yahoo_id match
                {"name": "Bob Smith", "yahoo_id": "0"},     # unique-name match
                {"name": "Twin", "yahoo_id": "1"},          # name not unique -> raw
                {"name": "Nobody", "yahoo_id": "2"}],       # unknown -> raw
        "THE ": [{"name": "Bob Smith", "yahoo_id": "9001"}],  # ambiguous team -> raw
        "I'LL": [{"name": "Bob Smith", "yahoo_id": "x"}],     # mangled: only unclaimed team is JEP
    })
    build_history(ctx, b)
    ra = [dict(zip(TABLE_COLUMNS["roster_assignments"], r)) for r in b.rows["roster_assignments"]]
    assert [(r["upid"], r["team_abbr"]) for r in ra] == [("100", "WIZ"), ("200", "WIZ"), ("200", "JEP")]
    assert all(r["valid_to"] is not None and r["source"] == "historical/yahoo_players_2015.json" for r in ra)
    assert ra[0]["valid_from"].astimezone(timezone.utc).isoformat().startswith("2015-01-01T05:00")
    raw = [r for r in b.rows["history_raw"] if r[0].endswith("yahoo_players_2015.json")]
    assert [r[2].obj["name"] for r in raw] == ["Twin", "Nobody", "Bob Smith"]  # THE roster is last
    assert raw[-1][2].obj["team_abbr"] is None
    keys = {f["key"]: f for f in b.report.flags["history_roster_key"]}
    assert keys["THE "]["team"] is None and "ambiguous" in keys["THE "]["how"]
    assert keys["I'LL"]["team"] == "JEP"


def test_csv_rows_shape(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,,a\n1,2,3\n4,5,6,7\n", encoding="utf-8")
    assert list(_csv_rows(p)) == [
        {"a": "1", "col_1": "2", "a_2": "3"},
        {"a": "4", "col_1": "5", "a_2": "6", "col_3": "7"},
    ]


def test_csv_and_empty_files_go_to_history_raw(tmp_path):
    ctx, b = make_ctx(tmp_path)
    write(tmp_path, "data/historical/2026/farm.csv", "name,team\nX,WIZ\nY,JEP\n")
    write(tmp_path, "data/historical/2026/empty.csv", "")
    write(tmp_path, "data/historical/2025/Nov 1 2025 MLB Top 100 Prospects.csv", "1,A\n")
    build_history(ctx, b)
    assert [(r[0], r[1]) for r in b.rows["history_raw"]] == [
        ("data/historical/2026/farm.csv", 0), ("data/historical/2026/farm.csv", 1)]
    assert b.report.skipped["history_raw"][0]["reason"] == "0-byte file"
    assert {"file": "data/historical/2026/farm.csv", "rows": 2, "table": "history_raw"} in b.report.flags["history_file_rows"]


def test_draft_state_fills_result(tmp_path):
    ctx, b = make_ctx(tmp_path)
    write(tmp_path, "data/historical/2025/draft_order_2025.json", {
        "draft_type": "prospect", "season": 2025, "notes": "mirrors test order",
        "picks": [{"round": 1, "pick": 1, "team": "WIZ", "round_type": "fypd"}]})
    write(tmp_path, "data/historical/2025/draft_state_prospect_2025.json", {
        "draft_type": "prospect", "season": 2025,
        "picks_made": [{"round": 1, "pick": 1, "team": "WIZ", "player": "Bob Smith"},
                       {"round": 1, "pick": 2, "team": "JEP", "player": "Z"}]})
    build_history(ctx, b)
    assert len(b.rows["draft_picks"]) == 2
    first = cols(b, "draft_picks", 0)
    assert first["round_type"] == "fypd" and first["result"] is not None
    assert any("mirrors test order" in n for n in b.report.notes)
