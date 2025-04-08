import requests
import xml.etree.ElementTree as ET
import json
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from token_manager import get_access_token



LEAGUE_ID = "15505"
GAME_KEY = "mlb"
YAHOO_TEAM_MAP = {
    "1": "WIZ", "2": "B2J", "3": "CFL", "4": "HAM",
    "5": "JEP", "6": "LFB", "7": "LAW", "8": "SAD",
    "9": "DRO", "10": "RV", "11": "TBB", "12": "WAR"
}
OUTPUT_FILE = "data/standings.json"

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
        if standings_node is None:
            continue

        wins = int(standings_node.attrib.get("wins", 0))
        losses = int(standings_node.attrib.get("losses", 0))
        ties = int(standings_node.attrib.get("ties", 0))
        total = wins + losses + ties
        win_pct = wins + 0.5 * ties
        pct_display = round(win_pct / total, 3) if total > 0 else 0.000
        rank = int(team.find("y:team_standings/y:rank", ns).text)

        standings.append({
            "rank": rank,
            "team": abbr,
            "record": f"{wins}-{losses}-{ties}",
            "win_pct": pct_display
        })

    standings.sort(key=lambda x: x["rank"])

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
