import json
import requests
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from token_manager import get_access_token as get_token
 # You already have this
import os

# Yahoo league config
LEAGUE_ID = "8560"
GAME_KEY = "469"  # 2026 MLB season


def _load_yahoo_team_map():
    """Build yahoo_team_id -> FBP abbreviation map from managers.json."""
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "managers.json")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    mapping = {}
    for abbr, info in cfg.get("teams", {}).items():
        yid = info.get("yahoo_team_id")
        if yid:
            mapping[str(yid)] = abbr
    return mapping


YAHOO_TEAM_MAP = _load_yahoo_team_map()

def fetch_yahoo_rosters():
    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{GAME_KEY}.l.{LEAGUE_ID}/teams;out=roster"
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch league data: {response.status_code} - {response.text}")

    from xml.etree import ElementTree as ET
    root = ET.fromstring(response.text)
    ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

    teams_data = {}

    print("📥 Parsing Yahoo teams and rosters...")

    for team in root.findall(".//y:team", ns):
        team_id = team.find("y:team_id", ns).text
        fbp_team = YAHOO_TEAM_MAP.get(team_id)

        print(f"\n➡️ Yahoo team ID: {team_id} → FBP team: {fbp_team or 'UNKNOWN'}")

        if not fbp_team:
            print(f"⚠️ Skipping unknown team_id: {team_id}")
            continue

        players = []
        roster = team.find(".//y:roster", ns)
        if roster is None:
            print(f"⚠️ No roster found for Yahoo team ID {team_id}")
            continue

        for player in roster.findall(".//y:player", ns):
            name_elem = player.find(".//y:full", ns)
            pos_elem = player.find("y:display_position", ns)
            team_elem = player.find("y:editorial_team_abbr", ns)
            player_id_elem = player.find("y:player_id", ns)

            name = name_elem.text if name_elem is not None else "Unknown"
            pos = pos_elem.text if pos_elem is not None else "N/A"
            mlb_team = team_elem.text if team_elem is not None else "N/A"
            player_id = player_id_elem.text if player_id_elem is not None else "?"

            players.append({
                "name": name,
                "position": pos,
                "team": mlb_team,
                "yahoo_id": player_id
            })

        print(f"✅ {len(players)} players found for {fbp_team}")
        teams_data[fbp_team] = players

    print("\n✅ Finished parsing all Yahoo teams.\n")
    return teams_data



def save_to_json(data, filename="data/yahoo_players.json"):
    os.makedirs("data", exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Yahoo players saved to {filename}")

if __name__ == "__main__":
    rosters = fetch_yahoo_rosters()
    save_to_json(rosters)

