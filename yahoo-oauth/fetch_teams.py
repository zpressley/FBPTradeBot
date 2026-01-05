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

# Make API Request
response = requests.get(BASE_URL, headers=HEADERS)

# Convert XML to JSON
try:
    parsed_data = xmltodict.parse(response.text)  # Convert XML to OrderedDict
    json_data = json.dumps(parsed_data, indent=4)  # Convert OrderedDict to JSON

    print("\nConverted JSON Response:")
    print(json_data)
    
    # Extract Team Keys
    teams_info = parsed_data["fantasy_content"]["league"]["teams"]["team"]
    
    team_keys = []
    if isinstance(teams_info, list):  # If multiple teams
        for team in teams_info:
            team_keys.append(team["team_key"])
    else:  # If only one team
        team_keys.append(teams_info["team_key"])

    print("\nExtracted Team Keys:", team_keys)

except Exception as e:
    print(f"Error converting XML to JSON: {str(e)}")
