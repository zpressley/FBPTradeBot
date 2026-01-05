import requests
import xmltodict
import json
from token_manager import get_access_token  # Use centralized token manager

# Get the latest access token
ACCESS_TOKEN = get_access_token()

# Check if token retrieval failed
if not ACCESS_TOKEN:
    print("❌ No valid access token available. Run get_token.py to re-authenticate.")
    exit()

# Replace with your League ID
LEAGUE_ID = "mlb.l.15505"

# Base Yahoo Fantasy API Endpoint for Players
BASE_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_ID}/players"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Pagination: Yahoo only returns 25 players per request, so we need to loop through pages
players_list = []
start = 0  # Start index for pagination
players_per_page = 25  # Max players Yahoo returns per request

while True:
    # Request URL with pagination
    url = f"{BASE_URL};start={start}"

    print(f"Fetching players from {start} to {start + players_per_page - 1}...")

    # Make API Request
    response = requests.get(url, headers=HEADERS)

    # Convert XML to JSON
    try:
        parsed_data = xmltodict.parse(response.text)

        # Extract players
        players_data = parsed_data["fantasy_content"]["league"]["players"].get("player", [])

        if not players_data:
            print("No more players found. Exiting loop.")
            break  # Exit loop if no more players

        # If only one player is returned, Yahoo returns a dict instead of a list
        if isinstance(players_data, dict):
            players_data = [players_data]

        # Extract Player Info
        for player in players_data:
            player_info = {
                "player_id": player.get("player_id", "N/A"),
                "name": player.get("name", {}).get("full", "Unknown"),
                "team": player.get("editorial_team_full_name", "Free Agent"),  # Defaults to Free Agent if no team
                "position": player.get("eligible_positions", {}).get("position", "N/A")
            }
            players_list.append(player_info)

        # Move to the next page
        start += players_per_page

    except Exception as e:
        print(f"Error fetching players: {str(e)}")
        break  # Stop if there's an error

# Print all players
print("\nAll Players:")
print(json.dumps(players_list, indent=4))

# Save to a JSON file (Optional)
with open("players.json", "w") as f:
    json.dump(players_list, f, indent=4)

print("\n✅ Saved all players to 'players.json'")


       
