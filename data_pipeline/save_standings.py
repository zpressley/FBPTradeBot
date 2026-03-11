import requests
import xml.etree.ElementTree as ET
import json
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from token_manager import get_access_token



LEAGUE_ID = "8560"
GAME_KEY = "469"  # 2026 MLB season
OUTPUT_FILE = "data/standings.json"


def _load_managers_config():
    """Load managers.json and return the teams dict."""
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "managers.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f).get("teams", {})


def _load_yahoo_team_map(teams_cfg):
    """Build yahoo_team_id -> FBP abbreviation map from managers.json."""
    return {str(info["yahoo_team_id"]): abbr for abbr, info in teams_cfg.items() if info.get("yahoo_team_id")}


def _preseason_rank(teams_cfg, abbr):
    """Return final_rank_2025 as pre-season rank fallback."""
    return teams_cfg.get(abbr, {}).get("final_rank_2025", 99)


MANAGERS_CFG = _load_managers_config()
YAHOO_TEAM_MAP = _load_yahoo_team_map(MANAGERS_CFG)

def fetch_and_save_standings():
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    standings_url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{GAME_KEY}.l.{LEAGUE_ID}/standings"
    scoreboard_url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{GAME_KEY}.l.{LEAGUE_ID}/scoreboard"

    standings_res = requests.get(standings_url, headers=headers)
    scoreboard_res = requests.get(scoreboard_url, headers=headers)

    if standings_res.status_code != 200 or scoreboard_res.status_code != 200:
        print("❌ Failed to fetch data from Yahoo.")
        return

    ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
    standings_root = ET.fromstring(standings_res.text)
    scoreboard_root = ET.fromstring(scoreboard_res.text)
    print("\n📥 DEBUG: Dumping <matchup> tags...")
    for elem in scoreboard_root.iter():
        if "matchup" in elem.tag:
            print(f"FOUND TAG: {elem.tag}")


    # Standings
    team_nodes = standings_root.findall(".//y:team", ns)
    standings = []

    for team in team_nodes:
        team_id = team.find("y:team_id", ns).text
        abbr = YAHOO_TEAM_MAP.get(team_id, f"Team {team_id}")
        standings_node = team.find("y:team_standings", ns)

        # Pre-season: team_standings may be missing or have no rank/record
        wins, losses, ties = 0, 0, 0
        rank = 0

        if standings_node is not None:
            wins = int(standings_node.attrib.get("wins", 0))
            losses = int(standings_node.attrib.get("losses", 0))
            ties = int(standings_node.attrib.get("ties", 0))
            rank_el = team.find("y:team_standings/y:rank", ns)
            if rank_el is not None and rank_el.text:
                rank = int(rank_el.text)

        total = wins + losses + ties
        win_pct = wins + 0.5 * ties
        pct_display = round(win_pct / total, 3) if total > 0 else 0.000

        standings.append({
            "rank": rank or _preseason_rank(MANAGERS_CFG, abbr),
            "manager": abbr,
            "team": abbr,
            "record": f"{wins}-{losses}-{ties}",
            "win_pct": pct_display
        })

    standings.sort(key=lambda x: x["rank"])
    print("📦 Raw standings XML:")
    print(standings_res.text[:2000])  # print first 2,000 chars

    # Matchups — Fully namespace-safe
    matchups = []
    NAMESPACE = "{http://fantasysports.yahooapis.com/fantasy/v2/base.rng}"

    for matchup in scoreboard_root.findall(f".//{NAMESPACE}matchup"):
        teams = matchup.findall(f".//{NAMESPACE}team")
        scores = matchup.findall(f".//{NAMESPACE}team_points")

        if len(teams) == 2 and len(scores) == 2:
            team1_id = teams[0].find(f"{NAMESPACE}team_id").text
            team2_id = teams[1].find(f"{NAMESPACE}team_id").text
            team1_abbr = YAHOO_TEAM_MAP.get(team1_id, team1_id)
            team2_abbr = YAHOO_TEAM_MAP.get(team2_id, team2_id)

            wc1 = scores[0].find(f"{NAMESPACE}winning_categories")
            wc2 = scores[1].find(f"{NAMESPACE}winning_categories")

            if wc1 is not None and wc2 is not None:
                team1_wins = int(wc1.text)
                team2_wins = int(wc2.text)
                matchups.append(f"{team1_abbr} {team1_wins} vs {team2_abbr} {team2_wins}")



    snapshot = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "standings": standings,
        "matchups": matchups
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"✅ Standings + matchups saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    fetch_and_save_standings()
