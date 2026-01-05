import requests
import xmltodict
import json
from token_manager import get_access_token

# Get the latest access token
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# Yahoo MLB Game Key for 2024
GAME_KEY = "398"  # Yahoo's game key for MLB 2024

# Yahoo Fantasy API Test URL (Fetching All Players)
TEST_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/game/{GAME_KEY}/players;start=0/stats"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Log Request
print(f"\n🔍 Testing Yahoo API URL: {TEST_URL}")

# Make API Request
response = requests.get(TEST_URL, headers=HEADERS)

# ✅ Log HTTP Response Status
print(f"\n🔍 HTTP Status Code: {response.status_code}")

# ✅ Print Raw API Response (Log only first 1000 chars to avoid huge output)
raw_response = response.text
print("\n🔍 Raw API Response (First 1000 chars):")
print(raw_response[:1000])  # Print the first 1000 characters

# Convert XML to JSON
try:
    parsed_data = xmltodict.parse(response.text)

    # ✅ Print Formatted JSON Response (Only the first level)
    print("\n🔍 Parsed JSON Response (Top-Level Keys):")
    print(json.dumps(parsed_data, indent=4)[:1000])  # Print the first 1000 characters

    # ✅ Check if 'fantasy_content' exists
    if "fantasy_content" not in parsed_data:
        print("\n❌ Error: 'fantasy_content' is missing from response. Yahoo may be blocking access.")

except Exception as e:
    print(f"\n❌ Error parsing Yahoo API response: {str(e)}")
