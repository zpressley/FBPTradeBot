"""Tests for the Top 100 scraper parsing/matching/freeze rules and the BC retention report.
No network and no browser: Playwright is never imported."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "data_pipeline"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import scrape_pipeline_top100 as sc  # noqa: E402
import bc_retention_report as bc  # noqa: E402


# ---------------------------------------------------------------- fixtures

def json_html(n=100, key="__NEXT_DATA__"):
    prospects = [{"rank": i, "player": {"id": 600000 + i, "fullName": f"Player {i}"},
                  "team": {"abbreviation": "LAD"}, "position": {"abbreviation": "SS"},
                  "level": "AA", "eta": "2027", "age": 20} for i in range(1, n + 1)]
    blob = json.dumps({"props": {"pageProps": {"prospects": prospects}}})
    if key == "__NEXT_DATA__":
        return f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>'
    return f"<script>window.{key} = {blob};</script>"


def anchor_html(n=100, with_rank_text=True):
    parts = []
    for i in range(1, n + 1):
        rank = f'<span class="rank">{i}</span>' if with_rank_text else ""
        parts.append(f'<div>{rank}<a href="/player/player-{i}-{700000 + i}"><img alt="x"></a>'
                     f'<a href="/player/player-{i}-{700000 + i}">Player {i}</a></div>')
    return "".join(parts)


def headshot_html(n=100):
    return "".join(
        f'<li><b>{i}</b><img alt="Player {i}" src="https://img.mlbstatic.com/people/{800000 + i}/headshot/67/current"></li>'
        for i in range(1, n + 1))


# ------------------------------------------------------------ parse strategies

@pytest.mark.parametrize("key", ["__NEXT_DATA__", "__PRELOADED_STATE__", "__INITIAL_STATE__"])
def test_json_strategy(key):
    rows, strategy = sc.parse_rankings_html(json_html(key=key))
    assert strategy == "json" and len(rows) == 100
    assert rows[0]["rank"] == 1 and rows[0]["mlb_id"] == 600001 and rows[0]["name"] == "Player 1"
    assert rows[0]["org"] == "LAD" and rows[0]["position"] == "SS" and rows[0]["eta"] == "2027"


def test_anchor_strategy_with_rank_text():
    rows, strategy = sc.parse_rankings_html(anchor_html())
    assert strategy == "anchors" and len(rows) == 100
    assert (rows[41]["rank"], rows[41]["mlb_id"], rows[41]["name"]) == (42, 700042, "Player 42")


def test_anchor_strategy_falls_back_to_document_order():
    rows, strategy = sc.parse_rankings_html(anchor_html(with_rank_text=False))
    assert strategy == "anchors"
    assert [r["rank"] for r in rows] == list(range(1, 101))


def test_anchor_bare_numeric_href():
    html = "".join(f'<a href="/player/{500000 + i}">P{i}</a>' for i in range(1, 101))
    rows, strategy = sc.parse_rankings_html(html)
    assert strategy == "anchors" and rows[99]["mlb_id"] == 500100


def test_headshot_strategy():
    rows, strategy = sc.parse_rankings_html(headshot_html())
    assert strategy == "headshots" and len(rows) == 100
    assert (rows[6]["rank"], rows[6]["mlb_id"], rows[6]["name"]) == (7, 800007, "Player 7")


def test_stats_page_parse_and_merge():
    html = ('<table><thead><tr><th>Rk</th><th>Player</th><th>Age</th><th>L</th><th>AVG</th></tr></thead><tbody>'
            '<tr><td>1</td><td><img src="/people/600001/headshot/x"> Player 1</td><td>19</td><td>AA</td><td>.300</td></tr>'
            '<tr><td></td><td></td></tr></tbody></table>')
    stats = sc.parse_stats_html(html)
    assert len(stats) == 1 and stats[0]["mlb_id"] == 600001 and stats[0]["stats"]["AVG"] == ".300"
    rows, _ = sc.parse_rankings_html(json_html())
    assert sc.merge_stats(rows, stats) == 1
    assert rows[0]["stats"]["AVG"] == ".300" and rows[1]["stats"] == {}


# ----------------------------------------------------------------- thresholds

def test_thresholds():
    rows, _ = sc.parse_rankings_html(json_html())
    assert sc.check_thresholds(rows) == []
    assert any("only 99 rows" in e for e in sc.check_thresholds(rows[:99]))
    for r in rows[:6]:
        r["mlb_id"] = None
    assert any("MLB ID" in e for e in sc.check_thresholds(rows))
    for r in rows[:5]:  # 5 missing IDs is still fine
        pass
    rows2, _ = sc.parse_rankings_html(json_html())
    for r in rows2[:5]:
        r["mlb_id"] = None
    assert sc.check_thresholds(rows2) == []


# ------------------------------------------------------------------- matching

PLAYERS = [
    {"upid": "1", "name": "Jesús Made", "mlb_id": 700001, "team": "MIL"},
    {"upid": "2", "name": "Str Id", "mlb_id": "700002", "team": "SEA"},
    {"upid": "3", "name": "Name Only", "mlb_id": None, "team": "ATH"},
    {"upid": "4", "name": "Dupe Name", "mlb_id": 1, "team": "NYY"},
    {"upid": "5", "name": "Dupe Name", "mlb_id": 2, "team": "BOS"},
    {"upid": "", "name": "Blank Upid", "mlb_id": 700009, "team": "SD"},
    {"upid": "6", "name": "Stale Org", "mlb_id": None, "team": "BOS"},   # MLB now lists him with BAL
    {"upid": "7", "name": "No Org", "mlb_id": None, "team": ""},
]


def row(rank, name, mlb_id, org=None):
    return {"rank": rank, "name": name, "mlb_id": mlb_id, "org": org, "position": None,
            "level": None, "eta": None, "age": None}


def test_matching():
    rows = [row(1, "Wrong Name", 700001), row(2, "Str Id", 700002),
            row(3, "Name Only", None, "Athletics"), row(4, "Dupe Name", None, "Boston Red Sox"),
            row(5, "Dupe Name", None), row(6, "Nobody", 123), row(7, "Blank Upid", 700009),
            row(8, "Stale Org", 700010, "Baltimore Orioles"), row(9, "No Org", 700011, "Chicago Cubs")]
    unmatched = sc.match_rows(rows, PLAYERS)
    got = [(r["upid"], r["match_method"]) for r in rows]
    assert got == [("1", "mlb_id"), ("2", "mlb_id"), ("3", "name_org"), ("5", "name_org"),
                   (None, None), (None, None), (None, None), ("6", "name_only"), ("7", "name_only")]
    assert [u["rank"] for u in unmatched] == [5, 6, 7]
    assert rows[0]["org"] == "MIL"  # filled from combined_players on an ID match


# ---------------------------------------------------------------- freeze rule

def snap(official):
    return sc.build_snapshot([{**row(1, "A", 1), "upid": "9", "match_method": "mlb_id"}], [],
                             "2026-11-01", official, None, "2026-11-01T02:00:00-04:00")


def test_snapshot_shape():
    s = snap(True)
    assert s["official"] is True and s["source"] == "mlb.com/prospects" and s["count"] == 1
    assert {"rank", "name", "mlb_id", "level", "stats", "org", "upid", "match_method"} <= set(s["players"][0])
    assert "override" not in s
    o = sc.build_snapshot([], [], "d", True, "fix #5", "t")
    assert o["override"]["reason"] == "fix #5"


def test_freeze_rule(tmp_path):
    path = str(tmp_path / "2026-11-01.json")
    sc.check_freeze(path, None)  # no file: fine
    sc.write_snapshot(snap(False), path)
    sc.check_freeze(path, None)  # unofficial: may be overwritten
    sc.write_snapshot(snap(True), path)
    with pytest.raises(SystemExit):
        sc.check_freeze(path, None)
    sc.check_freeze(path, "typo in rank 48")  # override allowed


def test_db_skipped_without_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sc.write_db(snap(True), None)
    assert "skipping" in capsys.readouterr().out


# ------------------------------------------------------------ retention report

def test_retention_classification():
    players = [
        {"upid": "1", "name": "In By Upid", "mlb_id": 1, "team": "LAD", "FBP_Team": "WIZ", "contract_type": "Blue Chip Contract", "years_simple": "P"},
        {"upid": "2", "name": "In By Mlb", "mlb_id": "22", "team": "SEA", "FBP_Team": "WIZ", "contract_type": "Blue Chip Contract", "years_simple": "P"},
        {"upid": "3", "name": "Out", "mlb_id": 3, "team": "SEA", "FBP_Team": "HAM", "contract_type": "Blue Chip Contract", "years_simple": "P"},
        {"upid": "4", "name": "Grad", "mlb_id": 4, "team": "SEA", "FBP_Team": "HAM", "contract_type": "Keeper Contract", "years_simple": "TC BC-1"},
        {"upid": "5", "name": "Unowned BC", "mlb_id": 5, "team": "SEA", "FBP_Team": "None", "contract_type": "Blue Chip Contract", "years_simple": "P"},
        {"upid": "6", "name": "Blank BC", "mlb_id": 6, "team": "SEA", "FBP_Team": "", "contract_type": "Blue Chip Contract", "years_simple": "P"},
        {"upid": "7", "name": "Keeper", "mlb_id": 7, "team": "SEA", "FBP_Team": "HAM", "contract_type": "Keeper Contract", "years_simple": "P"},
    ]
    snapshot = {"date": "2026-11-01", "official": True,
                "players": [{"rank": 3, "upid": "1", "mlb_id": 1}, {"rank": 9, "upid": None, "mlb_id": 22}]}
    rows = bc.classify(players, snapshot)
    assert {r["name"]: r["result"] for r in rows} == {
        "In By Upid": bc.RETAINED, "In By Mlb": bc.RETAINED, "Out": bc.REPURCHASE, "Grad": bc.GRADUATED}
    text = bc.discord_text(rows, "2026-11-01")
    assert "**HAM**" in text and "Retained (free): 2 | Must repurchase at PAD: 1 | Already graduated: 1" in text


def test_retention_requires_official(tmp_path):
    snap_path = tmp_path / "s.json"
    snap_path.write_text(json.dumps({"date": "2026-11-01", "official": False, "players": []}))
    out = str(tmp_path / "out.csv")
    assert bc.main(["--snapshot", str(snap_path), "--out", out]) == 1
    assert bc.main(["--snapshot", str(snap_path), "--out", out, "--allow-unofficial"]) == 0
    assert os.path.exists(out)


# ------------------------------------------------------------ table strategy

def table_html(n=100, with_ids=True):
    """Mirrors the live rankings table seen 2026-11-03 (one <tr> per prospect)."""
    rows = []
    for i in range(1, n + 1):
        pid = 810000 + i
        link = f'<a href="https://www.mlb.com/stories/player-{i}-{pid}">' if with_ids else "<a>"
        img = (f'<img class="headshot-img" src="https://img.mlbstatic.com/x/v1/people/{pid}/headshot/silo/current" '
               f'alt="Photo headshot of Player {i}">') if with_ids else "<img>"
        rows.append(
            '<tr data-testid="table-row"><td data-testid="table-cell" class="rankings__table__cell--rank">'
            f'{i}</td><td data-testid="table-cell" class="rankings__table__cell--player"><div class="prospect-headshot">'
            f'{link}<div class="headshot-container">{img}</div></a><div class="prospect-headshot__name">Pláyer {i}</div>'
            '</div></td><td class="rankings__table__cell--position">SS</td>'
            '<td class="rankings__table__cell--team"><div class="prospect-team"><img alt="Logo">'
            '<div class="prospect-team__name">Milwaukee Brewers</div></div></td>'
            '<td class="rankings__table__cell--level"><div class="prospect-level">'
            '<div class="prospect-level__label">AA</div><div class="prospect-level__scale"><span></span></div></div></td>'
            '<td class="rankings__table__cell--eta">2027</td><td class="rankings__table__cell--currentAge">19</td>'
            '<td class="rankings__table__cell--heightWeight">6\' 1" / 221 lbs</td>'
            '<td class="rankings__table__cell--bats">S</td><td class="rankings__table__cell--throws">R</td></tr>')
    return ('<html><body><table class="rankings__table rankings__table--top100"><thead><tr><th class="rankings__table__cell--rank">Rank</th></tr></thead>'
            '<tbody>' + "".join(rows) + '</tbody></table><div class="load-more__container">'
            '<button data-testid="load-more-button">Show Full List</button></div></body></html>')


def test_table_strategy():
    rows, strategy = sc.parse_rankings_html(table_html())
    assert strategy == "table" and len(rows) == 100
    first = rows[0]
    assert first == {"rank": 1, "name": "Pláyer 1", "mlb_id": 810001, "org": "Milwaukee Brewers",
                     "position": "SS", "level": "AA", "eta": "2027", "age": "19"}
    assert rows[-1]["rank"] == 100 and rows[-1]["mlb_id"] == 810100
    assert sc.check_thresholds(rows) == []


def test_table_strategy_without_ids_fails_threshold():
    rows, strategy = sc.parse_rankings_html(table_html(with_ids=False))
    assert len(rows) == 100 and all(r["mlb_id"] is None for r in rows)
    assert sc.check_thresholds(rows)
