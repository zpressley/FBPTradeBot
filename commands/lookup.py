import json
import re
from difflib import get_close_matches

# Load flat list of players
with open("data/combined_players.json", "r") as f:
    all_players = json.load(f)

def extract_name(line):
    """
    Extracts the player's actual name from formats like:
    'SS Trea Turner [PHI] - VC 1' → 'Trea Turner'
    """
    try:
        match = re.match(r"^\w+\s+(.+?)\s+\[", line)
        return match.group(1).strip() if match else line.strip()
    except:
        return line.strip()

def fuzzy_lookup_all(name, threshold=0.7):
    """
    Returns a list of fuzzy-matched players from all_players
    """
    submitted = name.lower()
    player_names = [p["name"].lower() for p in all_players]

    matches = get_close_matches(submitted, player_names, n=5, cutoff=threshold)
    return [p for p in all_players if p["name"].lower() in matches]
