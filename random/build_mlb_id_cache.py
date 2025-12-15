import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Constants
SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
MAP_TAB = "Player ID Map"
CACHE_FILE = "data/mlb_id_cache.json"

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def get_prospect_upids(gclient):
    sheet = gclient.open_by_key(SHEET_KEY).worksheet(PLAYER_TAB)
    data = sheet.get_all_values()
    headers = data[0]

    upid_idx = headers.index("UPID")
    years_idx = headers.index("Years (Simple)")

    upids = set()
    for row in data[1:]:
        if len(row) <= max(upid_idx, years_idx):
            continue
        if row[years_idx].strip() == "P":
            upid = str(row[upid_idx]).strip()
            if upid:
                upids.add(upid)

    print(f"🔍 Found {len(upids)} prospect UPIDs from Player Data tab")
    return upids

def build_cache():
    gclient = authorize_gsheets()
    prospect_upids = get_prospect_upids(gclient)

    map_sheet = gclient.open_by_key(SHEET_KEY).worksheet(MAP_TAB)
    id_rows = map_sheet.get_all_records()

    cache = {}
    skipped = 0

    for row in id_rows:
        upid = str(row.get("UPID", "")).strip()
        name = row.get("Player Name", "").strip()
        mlb_id = row.get("MLB ID")

        if upid in prospect_upids and name and mlb_id:
            cache[upid] = {
                "name": name,
                "mlb_id": int(mlb_id)
            }
            print(f"✅ Cached {name} ({upid}) → {mlb_id}")
        else:
            skipped += 1

    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"\n✅ MLB ID cache saved to {CACHE_FILE} with {len(cache)} entries.")
    print(f"🔸 Skipped {skipped} rows (missing UPID, name, or not a prospect)")

if __name__ == "__main__":
    build_cache()
