import requests
import xmltodict
import json
import csv
from token_manager import get_access_token

# ✅ Get the latest access token from our OAuth setup
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# ✅ Set Yahoo Fantasy API parameters
LEAGUE_KEY = "458.l.15505"  # 2025 MLB League Key
GAME_KEY = "458"  # 2025 MLB Game Key
BATTERS_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/players;position=B;sort=OR;status=A;stat1=S_PSR"
PITCHERS_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/players;position=P;sort=OR;status=A;stat1=S_PSR"

# ✅ Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# ✅ Function to Fetch and Process Projections
def fetch_projections(url, position_type):
    response = requests.get(url, headers=HEADERS)

    # ✅ Print Raw API Response (First 500 characters)
    print("\n🔍 Full API Response for Batters (First 1000 chars):")
    print(response.text[:1000])  # Print more characters to see projections
    print(response.text[:500])

    try:
        parsed_data = xmltodict.parse(response.text)
        players_data = parsed_data["fantasy_content"]["game"]["players"].get("player", [])

        if isinstance(players_data, dict):  # Handle single player case
            players_data = [players_data]

        projections_list = []
        for player in players_data:
            player_info = {
                "player_id": player.get("player_id", "N/A"),
                "name": player.get("name", {}).get("full", "Unknown"),
                "team": player.get("editorial_team_full_name", "Free Agent"),
                "position": player.get("eligible_positions", {}).get("position", "N/A"),
                "projected_pts": player.get("player_points", {}).get("total", "N/A")  # Projected PTS
            }

            projections_list.append(player_info)

        return projections_list

    except Exception as e:
        print(f"\n❌ Error fetching {position_type} projections: {str(e)}")
        return []

# ✅ Fetch both batters and pitchers projections
batters_projections = fetch_projections(BATTERS_URL, "Batters")
pitchers_projections = fetch_projections(PITCHERS_URL, "Pitchers")

# ✅ Save to CSV
csv_filename = "yahoo_2025_projections.csv"
with open(csv_filename, "w", newline="") as csv_file:
    fieldnames = ["player_id", "name", "team", "position", "projected_pts"]
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()

    for player in batters_projections + pitchers_projections:
        writer.writerow(player)

print(f"\n✅ Successfully saved projections to {csv_filename}!")
