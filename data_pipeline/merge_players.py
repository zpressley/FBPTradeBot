import json
import os

TEAM_NAME_TO_ABBR = {
    "Hammers": "HAM",
    "Rick Vaughn": "RV",
    "Btwn2Jackies": "B2J",
    "Country Fried Lamb": "CFL",
    "Law-Abiding Citizens": "LAW",
    "La Flama Blanca": "LFB",
    "Jepordizers!": "JEP",
    "The Bluke Blokes": "TBB",
    "Whiz Kids": "WIZ",
    "Andromedans": "DRO",
    "not much of a donkey": "SAD",
    "Weekend Warriors": "WAR"
}


YAHOO_FILE = "data/yahoo_players.json"
SHEET_FILE = "data/sheet_players.json"
OUTPUT_FILE = "data/combined_players.json"

def load_json(filename):
    with open(filename, "r") as f:
        return json.load(f)

def merge_players(yahoo_data, sheet_data):
    combined = {}

    for team, yahoo_roster in yahoo_data.items():
        combined[team] = []

        # Normalize Yahoo names for matching
        yahoo_names = {player["name"].lower(): player for player in yahoo_roster}

        for player in sheet_data:
            player_name = player["Player Name"]
            sheet_team_full = player.get("Manager", "").strip()
            sheet_team = TEAM_NAME_TO_ABBR.get(sheet_team_full, "")
            player_type = player.get("Player Type", "").strip().upper()
            team_abbr = team.upper()
            combined[team].sort(key=sort_by_mlb_then_prospect)

            # Skip if not owned by this team
            if sheet_team != team_abbr:
                continue

            # MLB players must be on Yahoo roster
            if player_type == "MLB":
                if player_name.lower() not in yahoo_names:
                    continue  # not on their current Yahoo team

            # Build final display string
            pos = player.get("Pos", "??")
            mlb_team = player.get("Team", "FA")
            # Adjust status label for Prospects (Status = P) with Contract Type
            if player.get("Years (Simple)", "") == "P":
                contract_type_raw = player.get("Contract Type", "").strip().lower()
                type_map = {
                    "farm contract": "FC",
                    "purchased contract": "PC",
                    "development cont.": "DC"
                }
                contract_abbr = type_map.get(contract_type_raw)
                status = f"[P] [{contract_abbr}]" if contract_abbr else "[P]"
            else:
                status = player.get("Years (Simple)", player.get("Status", "FA"))
            display = f"{pos} {player_name} [{mlb_team}] - {status}"

            combined[team].append(display)

    return combined

STATUS_ORDER = {
    "FC 2": 1,
    "FC 1": 2,
    "VC 2": 3,
    "VC 1": 4,
    "TC 2": 5,
    "TC 1": 6,
    "TC R": 7,
    "P [PC]": 8,
    "P [FC]": 9,
    "P [DC]": 10,
    "P": 11
}

POS_ORDER = {
    "C": 1,
    "1B": 2,
    "2B": 3,
    "SS": 4,
    "3B": 5,
    "CF": 6,
    "OF": 7,
    "Util": 8,
    "SP": 9,
    "RP": 10,
    "P": 11,
    "RHP": 12,
    "LHP": 13
}


def sort_by_mlb_then_prospect(player_str):
    try:
        parts = player_str.split(" ")
        pos = parts[0]
        status = player_str.split(" - ")[-1].strip()

        pos_rank = POS_ORDER.get(pos, 99)
        status_rank = STATUS_ORDER.get(status, 99)

        # Split MLB vs Prospect
        if status.startswith("[P]"):
            return (1, pos_rank)  # Prospect group
        else:
            return (0, pos_rank)  # MLB group

    except Exception as e:
        print(f"Sort error on: {player_str} → {e}")
        return (99, 99, 99)


def save_json(data, filename):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Combined player data saved to {filename}")

if __name__ == "__main__":
    yahoo_data = load_json(YAHOO_FILE)
    sheet_data = load_json(SHEET_FILE)
    combined_data = merge_players(yahoo_data, sheet_data)
    save_json(combined_data, OUTPUT_FILE)
