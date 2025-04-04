# commands/lookup.py

import json
from difflib import get_close_matches

COMBINED_FILE = "data/combined_players.json"

def extract_name(formatted_str):
    try:
        name = formatted_str.split(" ", 1)[1].split(" [")[0].strip()
        return name
    except:
        return formatted_str.strip()

# Load combined player data once
with open(COMBINED_FILE, "r") as f:
    combined_data = json.load(f)

all_players = []
for team, players in combined_data.items():
    for p in players:
        all_players.append({
            "name": extract_name(p),
            "formatted": p,
            "manager": team
        })

def lookup_player(input_name: str):
    matches = get_close_matches(input_name, [p["name"] for p in all_players], n=1, cutoff=0.8)
    if not matches:
        return {
            "formatted": f"{input_name} [Not in System]",
            "manager": "Unknown",
            "match_warning": None
    }


    match_name = matches[0]
    player = next(p for p in all_players if p["name"] == match_name)

    return {
        "formatted": player["formatted"],
        "manager": player["manager"],
        "match_warning": input_name if match_name.lower() != input_name.lower() else None
    }

def fuzzy_lookup_all(name, threshold=0.7):
    from difflib import get_close_matches
    submitted = name.lower()
    all_names = [p["name"].lower() for p in all_players]
    matches = get_close_matches(submitted, all_names, n=5, cutoff=threshold)
    return [p for p in all_players if p["name"].lower() in matches]
