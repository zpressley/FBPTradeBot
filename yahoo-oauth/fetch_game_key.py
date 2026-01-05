import requests
import xmltodict
import json
from token_manager import get_access_token

# Get latest access token
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# ✅ Yahoo API URL to Get Only MLB Game Keys
GAME_URL = "https://fantasysports.yahooapis.com/fantasy/v2/games;game_codes=mlb"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Make API Request
response = requests.get(GAME_URL, headers=HEADERS)

# ✅ Print Raw API Response
print("\n🔍 Raw API Response (First 1000 chars):")
print(response.text[:1000])

# Convert XML to JSON
try:
    parsed_data = xmltodict.parse(response.text)

    # ✅ Check if "games" exist in the response
    if "games" not in parsed_data["fantasy_content"]:
        print("\n❌ Error: 'games' key is missing in API response.")
        exit()

    games = parsed_data["fantasy_content"]["games"]["game"]

    # ✅ Convert to list if only one game exists
    if isinstance(games, dict):
        games = [games]

    # ✅ Find the latest MLB game key
    latest_game_key = None
    latest_season = 0
    for game in games:
        if game["code"] == "mlb":
            season = int(game["season"])
            if season == 2024:  # Ensure we grab the correct season
                latest_game_key = game["game_key"]

    if latest_game_key:
        print(f"\n✅ Yahoo MLB Game Key for 2024: {latest_game_key}")
    else:
        print("\n❌ No MLB game key found for 2024.")

except Exception as e:
    print(f"\n❌ Error parsing Yahoo API response: {str(e)}")
