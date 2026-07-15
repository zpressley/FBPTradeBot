import json
import requests
from token_manager import get_access_token as get_token  # You already have this
import os

# Your league + team mapping
LEAGUE_ID = "15505"
GAME_KEY = "mlb"  # This may need to be dynamically fetched via API, but 'mlb' usually works

YAHOO_TEAM_MAP = {
    "1": "WIZ",
    "2": "B2J",
    "3": "CFL",
    "4": "HAM",
    "5": "JEP",
    "6": "LFB",
    "7": "LAW",
    "8": "SAD",
    "9": "DRO",
    "10": "RV",
    "11": "TBB",
    "12": "WAR"
}

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

    # Yahoo returns XML inside JSON, so parse as raw text
    from xml.etree import ElementTree as ET
    root = ET.fromstring(response.text)

    teams_data = {}

    for team in root.findall(".//team"):
        team_id = team.find("team_id").text
        fbp_team = YAHOO_TEAM_MAP.get(team_id)

        if not fbp_team:
            continue

        players = []
        roster = team.find(".//roster")
        if roster is not None:
            for player in roster.findall(".//player"):
                name = player.find(".//name/full").text
                pos = player.find("display_position").text
                mlb_team = player.find("editorial_team_abbr").text
                player_id = player.find("player_id").text

                players.append({
                    "name": name,
                    "position": pos,
                    "team": mlb_team,
                    "yahoo_id": player_id
                })

        teams_data[fbp_team] = players

    return teams_data

def save_to_json(data, filename="data/yahoo_players.json"):
    os.makedirs("data", exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Yahoo players saved to {filename}")

if __name__ == "__main__":
    rosters = fetch_yahoo_rosters()
    save_to_json(rosters)
