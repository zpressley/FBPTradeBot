"""Fetch live standings + scoreboard from Yahoo Fantasy API.

Writes data/standings.json with:
  - standings: rank, record, win_pct, opponent, score, per-category stats
  - matchups: full matchup pairs with category breakdowns
  - stat_categories: category ID → display name mapping

Uses JSON format API.
"""

import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import requests

from data_pipeline.token_manager import get_access_token

LEAGUE_ID = "8560"
GAME_KEY = "469"
LEAGUE_KEY = f"{GAME_KEY}.l.{LEAGUE_ID}"
OUTPUT_FILE = "data/standings.json"
BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def _load_managers_config():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "managers.json",
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f).get("teams", {})


def _yahoo_team_id_map(teams_cfg):
    """yahoo_team_id → FBP abbreviation."""
    return {
        str(info["yahoo_team_id"]): abbr
        for abbr, info in teams_cfg.items()
        if info.get("yahoo_team_id")
    }


def _api(path, token):
    url = f"{BASE}/{path}?format=json"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def fetch_and_save_standings():
    token = get_access_token()
    teams_cfg = _load_managers_config()
    tid_map = _yahoo_team_id_map(teams_cfg)

    # ── 1. Stat category names ──
    settings_data = _api(f"league/{LEAGUE_KEY}/settings", token)
    stat_cats = settings_data["fantasy_content"]["league"][1]["settings"][0]["stat_categories"]["stats"]
    stat_names = {}
    stat_list = []
    seen_display = set()
    for s in stat_cats:
        stat = s["stat"]
        sid = str(stat["stat_id"])
        display = stat["display_name"]
        # Disambiguate duplicate names (batting HR vs pitching HR, etc.)
        # Pitching stats have stat_id >= 24
        if display in seen_display:
            display = f"P_{display}"  # e.g. P_HR, P_K, P_TB
        seen_display.add(stat["display_name"])
        stat_names[sid] = display
        stat_list.append({
            "stat_id": sid,
            "display_name": display,
            "name": stat["name"],
            "display_only": stat.get("is_only_display_stat", "0") == "1",
        })

    # ── 2. Standings ──
    standings_data = _api(f"league/{LEAGUE_KEY}/standings", token)
    raw_teams = standings_data["fantasy_content"]["league"][1]["standings"][0]["teams"]
    standings = []

    for i in range(raw_teams["count"]):
        team = raw_teams[str(i)]["team"]
        team_name = team[0][2]["name"]
        team_id = str(team[0][1]["team_id"])
        abbr = tid_map.get(team_id, team_name)
        record = team[2]["team_standings"]
        rank = int(record["rank"])
        wins = int(record["outcome_totals"]["wins"])
        losses = int(record["outcome_totals"]["losses"])
        ties = int(record["outcome_totals"]["ties"])
        pct = record["outcome_totals"].get("percentage", "0")

        standings.append({
            "rank": rank,
            "manager": abbr,
            "team": abbr,
            "record": f"{wins}-{losses}-{ties}",
            "win_pct": float(pct),
        })

    standings.sort(key=lambda x: x["rank"])

    # ── 3. Scoreboard (live matchup scores + per-category stats) ──
    scoreboard_data = _api(f"league/{LEAGUE_KEY}/scoreboard", token)
    sb = scoreboard_data["fantasy_content"]["league"][1]["scoreboard"]
    current_week = scoreboard_data["fantasy_content"]["league"][0].get("current_week", 1)
    raw_matchups = sb["0"]["matchups"]

    matchups = []
    for i in range(raw_matchups["count"]):
        m = raw_matchups[str(i)]["matchup"]
        teams_data = m["0"]["teams"]

        sides = []
        for t in range(2):
            team_info = teams_data[str(t)]["team"]
            team_name = team_info[0][2]["name"]
            team_id = str(team_info[0][1]["team_id"])
            abbr = tid_map.get(team_id, team_name)
            points = team_info[1]["team_points"]["total"]

            # Per-category stats
            categories = {}
            for s in team_info[1].get("team_stats", {}).get("stats", []):
                sid = str(s["stat"]["stat_id"])
                val = s["stat"]["value"]
                display = stat_names.get(sid, sid)
                categories[display] = val

            sides.append({
                "team": abbr,
                "name": team_name,
                "score": points,
                "categories": categories,
            })

        matchups.append({
            "team1": sides[0],
            "team2": sides[1],
        })

    # ── 4. Per-team matchup summary with ties ──
    scoring_cats = sum(1 for c in stat_list if not c["display_only"])

    def _parse_record(rec: str) -> tuple[int, int, int]:
        try:
            w, l, t = [int(x) for x in str(rec or "0-0-0").split("-")]
            return w, l, t
        except Exception:
            return 0, 0, 0

    team_matchups = {}
    for mu in matchups:
        t1, t2 = mu["team1"], mu["team2"]
        for me, opp in [(t1, t2), (t2, t1)]:
            wins = int(me["score"])
            losses = int(opp["score"])
            ties = scoring_cats - wins - losses
            total = wins + losses + ties
            live_pct = round((wins + 0.5 * ties) / total, 3) if total > 0 else 0.0
            team_matchups[me["team"]] = {
                "opponent": opp["team"],
                "score": me["score"],
                "opponent_score": opp["score"],
                "ties": str(ties),
                "live_record": f"{wins}-{losses}-{ties}",
                "live_win_pct": live_pct,
                "categories": me["categories"],
            }

    for s in standings:
        mu = team_matchups.get(s["team"], {})
        s["opponent"] = mu.get("opponent", "")
        s["score"] = mu.get("score", "0")
        s["opponent_score"] = mu.get("opponent_score", "0")
        s["ties"] = mu.get("ties", "0")
        s["live_record"] = mu.get("live_record", "0-0-0")
        s["live_win_pct"] = mu.get("live_win_pct", 0.0)
        s["categories"] = mu.get("categories", {})

        # Overall live = completed record + current live matchup record.
        # Example: 8-11-1 + 1-0-19 => 9-11-20
        rw, rl, rt = _parse_record(s.get("record"))
        lw, ll, lt = _parse_record(s.get("live_record"))
        ow, ol, ot = rw + lw, rl + ll, rt + lt
        total = ow + ol + ot
        overall_pct = round((ow + 0.5 * ot) / total, 3) if total > 0 else 0.0
        s["overall_live_record"] = f"{ow}-{ol}-{ot}"
        s["overall_live_win_pct"] = overall_pct

    # Compute live_rank: sort by overall live win% descending, break ties by
    # current matchup score.
    sorted_live = sorted(
        standings,
        key=lambda x: (x.get("overall_live_win_pct", x.get("live_win_pct", 0.0)), int(x.get("score", "0"))),
        reverse=True,
    )
    for i, s in enumerate(sorted_live, 1):
        s["live_rank"] = i

    # ── 5. Save ──
    snapshot = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "week": current_week,
        "stat_categories": stat_list,
        "standings": standings,
        "matchups": matchups,
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    print(f"✅ Standings + live matchups saved ({len(standings)} teams, {len(matchups)} matchups, week {current_week})")


if __name__ == "__main__":
    fetch_and_save_standings()
