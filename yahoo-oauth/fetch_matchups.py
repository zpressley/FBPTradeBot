import requests
import xmltodict
import json
import csv
from token_manager import get_access_token

# ✅ Get the latest access token
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# ✅ Yahoo Fantasy API League Key for 2024
LEAGUE_KEY = "458.l.2527"  # ✅ Correct 2024 League Key

# ✅ API URL for Matchups
MATCHUP_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/scoreboard"

# ✅ Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# ✅ Function to Fetch Weekly Matchups
def fetch_matchups():
    response = requests.get(MATCHUP_URL, headers=HEADERS)

    # ✅ Print Raw API Response (First 1000 characters)
    print("\n🔍 Raw API Response (First 1000 chars):")
    print(response.text[:1000])

    try:
        parsed_data = xmltodict.parse(response.text)

        # ✅ Check if 'scoreboard' exists in response
        if "scoreboard" not in parsed_data["fantasy_content"]["league"]:
            print("\n❌ Error: 'scoreboard' key missing in response.")
            return []

        matchups_data = parsed_data["fantasy_content"]["league"]["scoreboard"]["matchups"]["matchup"]

        # ✅ Convert single matchup case to list
        if isinstance(matchups_data, dict):
            matchups_data = [matchups_data]

        matchup_list = []
        for matchup in matchups_data:
            week = matchup["week"]
            teams = matchup["teams"]["team"]

            # ✅ Convert single team case to list
            if isinstance(teams, dict):
                teams = [teams]

            # ✅ Extract both teams in the matchup
            team1 = teams[0]
            team2 = teams[1]

            matchup_info = {
                "week": week,
                "team1_id": team1["team_id"],
                "team1_name": team1["name"],
                "team1_manager": team1["managers"]["manager"]["nickname"],
                "team1_score": team1["team_points"]["total"],

                "team2_id": team2["team_id"],
                "team2_name": team2["name"],
                "team2_manager": team2["managers"]["manager"]["nickname"],
                "team2_score": team2["team_points"]["total"],
            }

            matchup_list.append(matchup_info)

        return matchup_list

    except Exception as e:
        print(f"\n❌ Error fetching matchups: {str(e)}")
        return []

# ✅ Fetch Matchups
matchups = fetch_matchups()

# ✅ Save to CSV
csv_filename = "yahoo_2024_matchups.csv"
with open(csv_filename, "w", newline="") as csv_file:
    fieldnames = ["week", "team1_id", "team1_name", "team1_manager", "team1_score",
                  "team2_id", "team2_name", "team2_manager", "team2_score"]
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()

    for matchup in matchups:
        writer.writerow(matchup)

print(f"\n✅ Successfully saved matchup data to {csv_filename}!")
