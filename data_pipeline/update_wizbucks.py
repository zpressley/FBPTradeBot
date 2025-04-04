import gspread
import json
import os
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheet Info
SHEET_KEY = "172eaArOcLoViepVh14sW3JLjyDGB3yfFVxVjIG9kEak"
TAB_NAME = "FBP HUB"
RANGE = "A1:D13"

def get_wizbuck_balances():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_KEY).worksheet(TAB_NAME)

    raw_data = sheet.get(RANGE)
    headers = raw_data[0]
    rows = raw_data[1:]

    balances = {}

    for row in rows:
        print("ROW:", row)  # Optional debug
        try:
            manager_name = row[1].strip()  # Column B
            balance = int(row[3].replace("$", "").strip())  # Column D, cleaned
            balances[manager_name] = balance
        except (IndexError, ValueError) as e:
            print(f"⚠️ Skipping row: {row} → {e}")
            continue


    return balances

def save_to_json(data, filename="data/wizbucks.json"):
    os.makedirs("data", exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Wiz Buck balances saved to {filename}")

if __name__ == "__main__":
    balances = get_wizbuck_balances()
    save_to_json(balances)
