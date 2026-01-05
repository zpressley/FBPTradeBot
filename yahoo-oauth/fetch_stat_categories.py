import requests
import xmltodict
import json
from token_manager import get_access_token

# Get the latest access token
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# ✅ Yahoo API URL to Get 2024 MLB Stat Categories
GAME_KEY = "431"  # 2024 MLB game key
STAT_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/game/{GAME_KEY}/stat_categories"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Make API Request
response = requests.get(STAT_URL, headers=HEADERS)

# ✅ Print Raw API Response (First 1000 chars)
print("\n🔍 Raw API Response (First 1000 chars):")
print(response.text[:1000])

# Convert XML to JSON
try:
    parsed_data = xmltodict.parse(response.text)

    # ✅ Ensure "stat_categories" exist in the response
    if "game" not in parsed_data["fantasy_content"] or "stat_categories" not in parsed_data["fantasy_content"]["game"]:
        print("\n❌ Error: 'stat_categories' key is missing in API response.")
        exit()

    stats_data = parsed_data["fantasy_content"]["game"]["stat_categories"]["stats"]

    # ✅ Handle single stat vs. multiple stats
    if isinstance(stats_data["stat"], dict):  # If only one stat exists, Yahoo returns a dictionary
        stats_data["stat"] = [stats_data["stat"]]  # Convert to list

    stat_categories = stats_data["stat"]

    # ✅ Print stat mapping
    stat_map = {}
    print("\n📊 Yahoo Fantasy Stat Categories:")
    for stat in stat_categories:
        stat_id = stat["stat_id"]
        stat_name = stat["name"]
        stat_map[stat_id] = stat_name
        print(f"Stat ID {stat_id}: {stat_name}")

    # ✅ Save to JSON for future use
    with open("stat_map.json", "w") as f:
        json.dump(stat_map, f, indent=4)

    print("\n✅ Stat map saved to stat_map.json!")

except Exception as e:
    print(f"\n❌ Error parsing Yahoo API response: {str(e)}")
