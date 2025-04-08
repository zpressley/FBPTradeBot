import json
import os
import requests
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Constants
SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
CACHE_FILE = "data/mlb_id_cache.json"
OUTPUT_DIR = "data/roster_snapshots"

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def load_prospects():
    gclient = authorize_gsheets()
    sheet = gclient.open_by_key(SHEET_KEY).worksheet(PLAYER_TAB)
    data = sheet.get_all_values()
    headers = data[0]

    upid_idx = headers.index("UPID")
    name_idx = headers.index("Player Name")
    years_idx = headers.index("Years (Simple)")

    prospects = []
    for row in data[1:]:
        if len(row) <= max(upid_idx, name_idx, years_idx):
            continue
        if row[years_idx].strip() == "P":
            upid = str(row[upid_idx]).strip()
            name = row[name_idx].strip()
            if upid and name:
                prospects.append({"upid": upid, "name": name})
    return prospects

def load_mlb_id_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def get_all_active_ids():
    active_ids = set()
    for team_id in range(108, 159):
        url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster/active"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code != 200:
                continue
            for player in res.json().get("roster", []):
                pid = player.get("person", {}).get("id")
                if pid:
                    active_ids.add(pid)
        except requests.exceptions.RequestException:
            continue
    return active_ids

def fetch_bulk_profiles(mlb_ids):
    if not mlb_ids:
        return {}
    url = f"https://statsapi.mlb.com/api/v1/people?personIds={','.join(str(i) for i in mlb_ids)}&hydrate=currentTeam"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return {}
        return {p["id"]: p for p in res.json().get("people", [])}
    except:
        return {}

def track_roster_status():
    prospects = load_prospects()
    id_cache = load_mlb_id_cache()
    active_ids = get_all_active_ids()

    valid = []
    for p in prospects:
        entry = id_cache.get(p["upid"])
        if entry and entry.get("mlb_id"):
            valid.append({
                "name": entry.get("name"),
                "mlb_id": entry.get("mlb_id")
            })

    mlb_ids = [v["mlb_id"] for v in valid]
    profiles = fetch_bulk_profiles(mlb_ids)

    snapshot = {}
    for p in valid:
        pid = p["mlb_id"]
        profile = profiles.get(pid, {})
        snapshot[p["name"]] = {
            "mlb_id": pid,
            "on_roster": pid in active_ids,
            "currentTeam": profile.get("currentTeam", {}).get("name", "Unknown"),
            "rosterStatus": profile.get("rosterStatus", "Unknown")
        }

    today = datetime.today().strftime("%Y-%m-%d")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, f"{today}.json"), "w") as f:
        json.dump({"date": today, "players": snapshot}, f, indent=2)

    print(f"✅ Roster snapshot saved to data/roster_snapshots/{today}.json")

if __name__ == "__main__":
    track_roster_status()
