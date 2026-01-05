import requests
import xmltodict
import json

# Replace with your access token
ACCESS_TOKEN = "SQDugu6buwoWGtR1Afg0UwBbJnPqDex8QmM_L5U080UtYiEngS.IlDTALimoh6vV3oSKq5vSgN7GcAt8oNT2dyu8oh5xYSXzPAMcWMLRXWhyUdHz2YdPAo44AmEV8trLeMiY2F84kRhQzyILRn3zHf.U_wzGbcvZpouCpEyHZi9vJwZ4Y5MGFSuKDXbpK1oCN0jGv3lYzKy3BspzZu7u9RyaL_KA_1tWwsCsD.4H5EQ.MbrqTjvaw9Lc3YCIXlKlQX.S6uY.K0XrGFDgCvD2YEyO_f96yb3P4M.e8Rq6Iteb3rZgynMqYZyP9QHgua5FCg2s5DNORo3wcTmpbtV1a6ZmyRBfljpvHjdIeLmWl0YCVqPwO3eLDqGXJxC8o3DjO5TSve0NO_TBf.SM1T.aRDkzjBt1vm0Pd21UT7Zn1LvHpXGmI24MUVL5rWmPwmqv8H_w4AQ3olBrw6mtGhytPGot3Hhg939osLgl8lr6uWpCy9GaH6OY5A1ScfK2qEl0Zv.St5_fGOXcNAToyH29wVCv2p4pJsNuZJ60aepDGhfeWWncdmckoGyNfVuncvSycr3qcQkMo3ZMnPGomLSmmon8NVfK0gV1vLB_4Xj3SZkJ7p5WV2BL6G5jEOegcPq07xo3tp4ULe_aZwvqkgqvE7kN_AfxXb0NtqrlWKs9lz1gmeYowg6.9QAIbRKcmo_kG6v.Bij9JYY6hnKJv2j1ljGV9xuUDxka84dMqcFnijBz6wtoTdBpP_.0AaydAwijmIuhMg5Dlmcrq5ZWf1jCd12TvpJpHd2l_3hbj9B8MvDOl4OQlCjKW9P5S7omkZPptGmZqpOrEtnItzE_sI2OceniKDRGILhvoRi7wIqK47M3v5C7SfIF7gBD04Ig0r1wofRbpM1WItWMAd1MyQBHTXcqSaZZR9POlotNHnI-"

# Replace with your actual League ID
LEAGUE_ID = "mlb.l.15505"  # Update with your actual league ID

# Yahoo Fantasy API Endpoint for League Teams
TEAMS_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_ID}/teams"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Fetch all teams to get their team keys
teams_response = requests.get(TEAMS_URL, headers=HEADERS)
teams_data = xmltodict.parse(teams_response.text)

# Extract team keys
team_keys = []
teams = teams_data["fantasy_content"]["league"]["teams"]["team"]
if isinstance(teams, list):  # If multiple teams
    for team in teams:
        team_keys.append(team["team_key"])
else:  # If only one team
    team_keys.append(teams["team_key"])

print("\nExtracted Team Keys:", team_keys)

# Fetch roster for each team
all_rosters = {}
for team_key in team_keys:
    print(f"\nFetching roster for team: {team_key}")
    ROSTER_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/team/{team_key}/roster"
    response = requests.get(ROSTER_URL, headers=HEADERS)

    # Print the raw response for debugging
    print(f"Raw response for {team_key}:")
    print(response.text)  # Print the full XML response

    # Convert XML to JSON
    try:
        roster_data = xmltodict.parse(response.text)

        # Debugging: Print parsed JSON structure
        print(f"\nConverted JSON for {team_key}: {json.dumps(roster_data, indent=4)}")

        # Ensure "fantasy_content" and "team" exist
        if "fantasy_content" in roster_data and "team" in roster_data["fantasy_content"]:
            team_data = roster_data["fantasy_content"]["team"]
            
            # Check if roster exists
            if "roster" in team_data and "players" in team_data["roster"]:
                players = team_data["roster"]["players"].get("player", [])

                # Handle single player case
                if isinstance(players, dict):  # If Yahoo returns a single player as a dict
                    players = [players]

                all_rosters[team_key] = []
                for player in players:
                    player_info = {
                        "player_id": player.get("player_id", "N/A"),
                        "name": player.get("name", {}).get("full", "Unknown"),
                        "position": player.get("eligible_positions", {}).get("position", "N/A"),
                        "team": player.get("editorial_team_full_name", "Unknown")
                    }
                    all_rosters[team_key].append(player_info)
            else:
                print(f"⚠️ Warning: No roster data found for {team_key}")

        else:
            print(f"⚠️ Warning: Missing 'fantasy_content' or 'team' in API response for {team_key}")

    except Exception as e:
        print(f"❌ Error parsing roster for {team_key}: {str(e)}")

# Print final roster data
print("\nAll Rosters (Team-wise):")
print(json.dumps(all_rosters, indent=4))
