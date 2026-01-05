import requests
import xmltodict
import json
from token_manager import get_access_token

# Get the latest access token
ACCESS_TOKEN = get_access_token()
print(f"Access Token: {ACCESS_TOKEN}")  # Debug statement

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# Yahoo MLB Game Key for 2024
GAME_KEY = "398"

# Yahoo Fantasy API Test URL (Fetching Global Player Stats)
TEST_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/game/{GAME_KEY}/players;start=0/stats"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Log Request
print(f"🔍 Testing Yahoo API URL: {TEST_URL}")

# Make API Request
response = requests.get(TEST_URL, headers=HEADERS)
print(f"HTTP Status Code: {response.status_code}")  # Debug statement
print(f"Response Text: {response.text}")  # Debug statement

# ✅ Log Response Status
print(f"\n🔍 HTTP Status Code: {response.status_code}")

# ✅ Print Raw API Response
print("\n🔍 Raw API Response:")
print(response.text)

# Convert XML to JSON
try:
    parsed_data = xmltodict.parse(response.text)
    print("\n🔍 Parsed JSON Response:")
    print(json.dumps(parsed_data, indent=4))  # Print nicely formatted JSON

except Exception as e:
    print(f"\n❌ Error parsing Yahoo API response: {str(e)}")
    