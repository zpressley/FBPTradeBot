# commands/lookup.py - Updated to work with the new data structure

import json
import re
from difflib import get_close_matches

# Load combined players data
def load_all_players():
    try:
        with open("data/combined_players.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ combined_players.json not found")
        return []

all_players = load_all_players()

def extract_name(line):
    """
    Extracts the player's actual name from formats like:
    'SS Trea Turner [PHI] - VC 1' → 'Trea Turner'
    """
    try:
        # Handle both old format and new format
        if " [" in line and "] [" in line:
            # New format: "SS Trea Turner [PHI] [VC 1]"
            match = re.match(r"^\w+\s+(.+?)\s+\[.+?\]\s+\[.+?\]", line)
        elif " [" in line:
            # Old format: "SS Trea Turner [PHI] - VC 1"
            match = re.match(r"^\w+\s+(.+?)\s+\[", line)
        else:
            # Just a name
            return line.strip()
        
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
    
    # Convert back to actual player objects
    matched_players = []
    for match in matches:
        for player in all_players:
            if player["name"].lower() == match:
                # Format the player for display
                formatted_player = {
                    "name": player["name"],
                    "formatted": format_player_display(player),
                    "manager": player.get("manager", "Unknown"),
                    "player_type": player.get("player_type", "Unknown")
                }
                matched_players.append(formatted_player)
                break
    
    return matched_players

def format_player_display(player):
    """Format player for consistent display"""
    pos = player.get("position", "?")
    name = player.get("name", "Unknown")
    team = player.get("team", "FA")
    contract = player.get("years_simple", "?")
    return f"{pos} {name} [{team}] [{contract}]"

def find_player_exact(name, team=None):
    """Find a player by exact name match, optionally filtered by team"""
    for player in all_players:
        if player["name"].lower() == name.lower():
            if team is None or player.get("manager") == team:
                return {
                    "name": player["name"],
                    "formatted": format_player_display(player),
                    "manager": player.get("manager", "Unknown"),
                    "player_type": player.get("player_type", "Unknown")
                }
    return None

def get_team_roster(team_abbr):
    """Get all players for a specific team"""
    team_players = [p for p in all_players if p.get("manager") == team_abbr]
    return [
        {
            "name": p["name"],
            "formatted": format_player_display(p),
            "manager": p.get("manager", "Unknown"),
            "player_type": p.get("player_type", "Unknown")
        }
        for p in team_players
    ]

# For backward compatibility
def fuzzy_match_name(input_name, all_names):
    """Legacy function for backward compatibility"""
    matches = get_close_matches(input_name, all_names, n=1, cutoff=0.8)
    return matches[0] if matches else None