import gspread
import json
import os
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheet settings
SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
TAB_NAME = "Player Data"

# Headers we care about
EXPECTED_HEADERS = [
    "Player Name", "Team", "Pos", "Player Type", "Manager",
    "Contract Type", "Status", "Years (Simple)"
]

def get_sheet_records():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_KEY).worksheet(TAB_NAME)

    raw_records = sheet.get_all_records(expected_headers=EXPECTED_HEADERS)

    cleaned = []

    for row in raw_records:
        player = {}
        for key in EXPECTED_HEADERS:
            player[key] = row.get(key, "").strip()
        cleaned.append(player)

    return cleaned

def save_to_json(data, filename="data/sheet_players.json"):
    os.makedirs("data", exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ HUB player data saved to {filename}")

if __name__ == "__main__":
    players = get_sheet_records()
    save_to_json(players)
