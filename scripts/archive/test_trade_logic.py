#!/usr/bin/env python3
# Test script to validate trade logic without Discord

import json
import sys
from difflib import get_close_matches

# Load data
def load_data():
    with open("data/combined_players.json", "r") as f:
        players = json.load(f)
    with open("data/wizbucks.json", "r") as f:
        wizbucks = json.load(f)
    return players, wizbucks

def get_full_team_name(abbr):
    """Convert team abbreviation to full name for wizbucks lookup"""
    team_map = {
        "HAM": "Hammers", "RV": "Rick Vaughn", "B2J": "Btwn2Jackies",
        "CFL": "Country Fried Lamb", "LAW": "Law-Abiding Citizens",
        "LFB": "La Flama Blanca", "JEP": "Jepordizers!", "TBB": "The Bluke Blokes",
        "WIZ": "Whiz Kids", "DRO": "Andromedans", "SAD": "not much of a donkey",
        "WAR": "Weekend Warriors"
    }
    return team_map.get(abbr, abbr)

def find_player_on_roster(search_name, roster):
    """Find a player on the roster with fuzzy matching"""
    # First: Try exact match
    for player in roster:
        if player["name"].lower() == search_name.lower():
            return player, "exact"
    
    # Second: Use fuzzy matching
    roster_names = [p["name"] for p in roster]
    matches = get_close_matches(search_name, roster_names, n=1, cutoff=0.8)
    
    if matches:
        matched_name = matches[0]
        for player in roster:
            if player["name"] == matched_name:
                return player, "fuzzy"
    
    return None, None

def test_trade(team1_abbr, team1_assets, team2_abbr, team2_assets):
    """Test a trade scenario"""
    print(f"\n=== TESTING TRADE: {team1_abbr} ↔ {team2_abbr} ===")
    print(f"{team1_abbr} gives: {team1_assets}")
    print(f"{team2_abbr} gives: {team2_assets}")
    
    players, wizbucks = load_data()
    
    # Get team rosters
    team1_roster = [p for p in players if p.get("manager") == team1_abbr]
    team2_roster = [p for p in players if p.get("manager") == team2_abbr]
    
    print(f"\n{team1_abbr} roster has {len(team1_roster)} players")
    print(f"{team2_abbr} roster has {len(team2_roster)} players")
    
    # Test team1 assets
    print(f"\n--- Validating {team1_abbr} assets ---")
    for asset in team1_assets:
        if "$" in asset.lower() and "wb" in asset.lower():
            print(f"✅ Wiz Bucks: {asset}")
            continue
            
        player, match_type = find_player_on_roster(asset, team1_roster)
        if player:
            contract = player.get("years_simple", "?")
            print(f"✅ {match_type.upper()}: {player['name']} [{contract}]")
            if match_type == "fuzzy":
                print(f"   (matched '{asset}' → '{player['name']}')")
        else:
            print(f"❌ NOT FOUND: '{asset}'")
            # Show suggestions
            roster_names = [p["name"] for p in team1_roster]
            suggestions = get_close_matches(asset, roster_names, n=3, cutoff=0.4)
            if suggestions:
                print(f"   Similar: {', '.join(suggestions)}")
    
    # Test team2 assets
    print(f"\n--- Validating {team2_abbr} assets ---")
    for asset in team2_assets:
        if "$" in asset.lower() and "wb" in asset.lower():
            print(f"✅ Wiz Bucks: {asset}")
            continue
            
        player, match_type = find_player_on_roster(asset, team2_roster)
        if player:
            contract = player.get("years_simple", "?")
            print(f"✅ {match_type.upper()}: {player['name']} [{contract}]")
            if match_type == "fuzzy":
                print(f"   (matched '{asset}' → '{player['name']}')")
        else:
            print(f"❌ NOT FOUND: '{asset}'")
            # Show suggestions
            roster_names = [p["name"] for p in team2_roster]
            suggestions = get_close_matches(asset, roster_names, n=3, cutoff=0.4)
            if suggestions:
                print(f"   Similar: {', '.join(suggestions)}")
    
    # Check Wiz Bucks balances
    print(f"\n--- Wiz Bucks Balances ---")
    team1_full = get_full_team_name(team1_abbr)
    team2_full = get_full_team_name(team2_abbr)
    
    print(f"{team1_abbr} ({team1_full}): ${wizbucks.get(team1_full, 0)}")
    print(f"{team2_abbr} ({team2_full}): ${wizbucks.get(team2_full, 0)}")

if __name__ == "__main__":
    print("🔍 FBP Trade Logic Tester")
    
    # Test the failing trade from Discord
    print("\n" + "="*50)
    print("TEST 1: The trade that was failing in Discord")
    test_trade(
        "WAR", ["Masyn Winn"],
        "WIZ", ["Lars Nootbaar"]
    )
    
    # Test with some variations
    print("\n" + "="*50)
    print("TEST 2: With slight name variations")
    test_trade(
        "WAR", ["masyn winn"],
        "WIZ", ["lars nootbaar"]
    )
    
    # Test with Wiz Bucks
    print("\n" + "="*50)
    print("TEST 3: With Wiz Bucks included")
    test_trade(
        "WAR", ["Masyn Winn", "$10WB"],
        "WIZ", ["Lars Nootbaar"]
    )
    
    # Test interactive mode
    if len(sys.argv) > 1 and sys.argv[1] == "interactive":
        print("\n" + "="*50)
        print("INTERACTIVE MODE")
        
        while True:
            print("\nEnter trade details (or 'quit' to exit):")
            team1 = input("Team 1 abbr (WAR, WIZ, etc.): ").strip().upper()
            if team1.lower() == 'quit':
                break
                
            team1_assets = input("Team 1 assets (comma-separated): ").split(",")
            team1_assets = [a.strip() for a in team1_assets if a.strip()]
            
            team2 = input("Team 2 abbr: ").strip().upper()
            team2_assets = input("Team 2 assets (comma-separated): ").split(",")
            team2_assets = [a.strip() for a in team2_assets if a.strip()]
            
            test_trade(team1, team1_assets, team2, team2_assets)
    
    print("\n✅ Testing complete!")