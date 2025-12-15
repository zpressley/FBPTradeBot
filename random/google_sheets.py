import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Set up the Google Sheets client
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import APIError

def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key("13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA")
    return sheet.worksheet("Player Data")


# Look up a player by name (partial or exact match)
from difflib import get_close_matches

def fuzzy_match_name(input_name, all_names):
    matches = get_close_matches(input_name, all_names, n=1, cutoff=0.8)
    return matches[0] if matches else None

import socket

def lookup_player(player_name):
    try:
        ws = get_sheet()
        expected_headers = [...]  # keep this as-is
        records = ws.get_all_records(expected_headers=expected_headers)

        all_player_names = [row["Player Name"] for row in records if row["Player Name"]]
        match = fuzzy_match_name(player_name, all_player_names)

        for row in records:
            if row["Player Name"].lower() == (match or "").lower():
                pos = row.get("Pos", "??")
                team = row.get("Team", "FA")
                status = row.get("Years (Simple)", row.get("Status", "FA"))
                formatted = f"{pos} {row['Player Name']} [{team}] - {status}"
                return {
                    "formatted": formatted,
                    "manager": row.get("Manager", "Unknown"),
                    "match_warning": player_name if match and match.lower() != player_name.lower() else None
                }

    except Exception as e:
        print(f"[Google Sheets Error] {e}")
        return {
            "formatted": f"?? {player_name} [FA] - FA (lookup failed)",
            "manager": "Unknown",
            "match_warning": None
        }

    return {
        "formatted": f"?? {player_name} [FA] - FA",
        "manager": "Unknown",
        "match_warning": None
    }
