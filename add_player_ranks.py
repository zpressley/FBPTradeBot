#!/usr/bin/env python3
"""
Add ADP rankings to MLB players in combined_players.json
"""
import json
import csv
from pathlib import Path

# File paths
CSV_PATH = Path.home() / "Downloads" / "FantasyPros_2026_Overall_MLB_ADP_Rankings.csv - Sheet1.csv"
PLAYERS_PATH = Path("/Users/zpressley/fbp-trade-bot/data/combined_players.json")

def load_rankings(csv_path):
    """Load rankings from CSV and create UPID -> Rank mapping"""
    rankings = {}
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            upid = row['UPID'].strip()
            rank = int(row['Rank'])
            rankings[upid] = rank
    
    print(f"✅ Loaded {len(rankings)} rankings from CSV")
    return rankings

def update_players(players_path, rankings):
    """Update combined_players.json with rank field for MLB players"""
    # Load players
    with open(players_path, 'r') as f:
        players = json.load(f)
    
    print(f"📊 Total players in file: {len(players)}")
    
    # Track stats
    mlb_count = 0
    ranked_count = 0
    unranked_mlb = []
    
    # Update players
    for player in players:
        if player.get('player_type') == 'MLB':
            mlb_count += 1
            upid = str(player.get('upid', ''))
            
            if upid in rankings:
                player['rank'] = rankings[upid]
                ranked_count += 1
            else:
                # Set rank to null for unranked MLB players
                player['rank'] = None
                unranked_mlb.append(f"{player.get('name', 'Unknown')} (UPID: {upid})")
        else:
            # Remove rank field from non-MLB players if it exists
            if 'rank' in player:
                del player['rank']
    
    # Write back to file
    with open(players_path, 'w') as f:
        json.dump(players, f, indent=2)
    
    # Print summary
    print(f"\n📈 Summary:")
    print(f"   MLB players: {mlb_count}")
    print(f"   Ranked: {ranked_count}")
    print(f"   Unranked: {mlb_count - ranked_count}")
    
    if unranked_mlb and len(unranked_mlb) <= 20:
        print(f"\n⚠️  Unranked MLB players:")
        for name in unranked_mlb[:20]:
            print(f"   - {name}")
    
    print(f"\n✅ Updated {PLAYERS_PATH}")

def main():
    print("🏐 Adding ADP Rankings to MLB Players\n")
    
    # Load rankings
    rankings = load_rankings(CSV_PATH)
    
    # Update players
    update_players(PLAYERS_PATH, rankings)
    
    print("\n✅ Done!")

if __name__ == "__main__":
    main()
