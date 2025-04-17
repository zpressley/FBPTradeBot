import json
import os

YAHOO_FILE = "data/yahoo_players.json"
SHEET_FILE = "data/sheet_players.json"
OUTPUT_FILE = "data/combined_players.json"

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)

def merge_players(yahoo_data, sheet_data):
    combined = []

    # Index sheets by lower name for lookup
    sheet_lookup = {p["Player Name"].lower(): p for p in sheet_data}

    # Add Yahoo players
    for manager, roster in yahoo_data.items():
        for player in roster:
            name = player.get("name", "")
            pos = player.get("position", "")
            team = player.get("team", "")
            yahoo_id = player.get("yahoo_id", "")

            # Pull from sheet if matched
            sheet = sheet_lookup.get(name.lower(), {})
            combined.append({
                "name": name,
                "team": team,
                "position": pos,
                "manager": manager,
                "player_type": sheet.get("Player Type", "MLB"),
                "contract_type": sheet.get("Contract Type", ""),
                "status": sheet.get("Status", ""),
                "years_simple": sheet.get("Years (Simple)", ""),
                "yahoo_id": yahoo_id,
                "upid": sheet.get("UPID", "")
            })

    # Add Farm-only players from Sheet
    for player in sheet_data:
        if player.get("Player Type") == "Farm":
            name = player.get("Player Name", "")
            if not any(p["name"] == name for p in combined):
                combined.append({
                    "name": name,
                    "team": player.get("Team", ""),
                    "position": player.get("Pos", ""),
                    "manager": player.get("Manager", ""),
                    "player_type": "Farm",
                    "contract_type": player.get("Contract Type", ""),
                    "status": player.get("Status", ""),
                    "years_simple": player.get("Years (Simple)", ""),
                    "yahoo_id": "",
                    "upid": player.get("UPID", "")
                })

    return combined

def save_json(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Combined data saved to {path} with {len(data)} players.")

if __name__ == "__main__":
    yahoo = load_json(YAHOO_FILE)
    sheet = load_json(SHEET_FILE)
    merged = merge_players(yahoo, sheet)
    save_json(merged, OUTPUT_FILE)
