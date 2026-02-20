#!/usr/bin/env python3
"""
Add FantasyPros ADP rankings to combined_players.json
Reads rankings from CSV and updates player rank field based on UPID match
"""

import json
import csv
from pathlib import Path

# File paths
REPO_ROOT = Path(__file__).parent.parent
CSV_PATH = Path.home() / "Downloads" / "FantasyPros_2026_Overall_MLB_ADP_Rankings.csv - Sheet1.csv"
PLAYERS_FILE = REPO_ROOT / "data" / "combined_players.json"

def load_rankings_from_csv(csv_path):
    """Load rankings from FantasyPros CSV"""
    rankings = {}
    
    print(f"📖 Reading rankings from {csv_path}")
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            upid = row['UPID'].strip()
            rank = int(row['Rank'])
            rankings[upid] = rank
    
    print(f"✅ Loaded {len(rankings)} ranked players")
    return rankings

def update_player_ranks(players_file, rankings):
    """Update combined_players.json with ranks"""
    
    print(f"📖 Reading {players_file}")
    with open(players_file, 'r', encoding='utf-8') as f:
        players = json.load(f)
    
    print(f"✅ Loaded {len(players)} players")
    
    # Update ranks
    ranked_count = 0
    unranked_count = 0
    
    for player in players:
        upid_str = str(player.get('upid', ''))
        
        if upid_str in rankings:
            player['rank'] = rankings[upid_str]
            ranked_count += 1
        else:
            # Remove rank field if it exists but player is not ranked
            if 'rank' in player:
                del player['rank']
            unranked_count += 1
    
    print(f"✅ Updated {ranked_count} players with ranks")
    print(f"ℹ️  {unranked_count} players without ranks")
    
    # Save updated file
    print(f"💾 Saving updated players to {players_file}")
    with open(players_file, 'w', encoding='utf-8') as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
    
    print("✅ Done!")
    
    # Show some examples
    print("\n📊 Sample ranked players:")
    ranked_players = [p for p in players if 'rank' in p]
    ranked_players.sort(key=lambda x: x.get('rank', 9999))
    for p in ranked_players[:5]:
        print(f"  Rank {p['rank']}: {p['name']} (UPID: {p['upid']})")

def main():
    # Check if CSV exists
    if not CSV_PATH.exists():
        print(f"❌ CSV file not found: {CSV_PATH}")
        print("Please ensure the FantasyPros CSV is downloaded to ~/Downloads/")
        return 1
    
    # Load rankings
    rankings = load_rankings_from_csv(CSV_PATH)
    
    # Update players
    update_player_ranks(PLAYERS_FILE, rankings)
    
    return 0

if __name__ == '__main__':
    exit(main())
