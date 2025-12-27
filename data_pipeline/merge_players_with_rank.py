"""
Phase 1: Add Rank/ADP to combined_players.json
Merges custom rankings from Player_Database.csv into combined players data
"""

import json
import csv
import os
from typing import Dict, List


def load_player_database_csv(csv_path: str = "data/Player Database.csv") -> Dict[str, int]:
    """
    Load rankings from Player_Database.csv
    
    Returns:
        Dict mapping UPID → rank
    """
    
    print(f"📊 Loading rankings from {csv_path}...")
    
    rank_map = {}
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            # Skip first row (documentation header)
            next(f)
            
            # Now read as CSV with actual headers in row 1
            reader = csv.DictReader(f)
            
            for row in reader:
                upid = row.get('UPID', '').strip()
                rank_raw = row.get('Rank/ADP', '').strip()
                
                # Only process if both exist
                if upid and rank_raw:
                    try:
                        rank = int(rank_raw)
                        rank_map[upid] = rank
                    except ValueError:
                        # Skip non-numeric ranks
                        continue
        
        print(f"✅ Loaded {len(rank_map)} rankings from CSV")
        return rank_map
        
    except FileNotFoundError:
        print(f"❌ CSV file not found: {csv_path}")
        return {}
    except Exception as e:
        print(f"❌ Error loading CSV: {e}")
        return {}


def load_combined_players(json_path: str = "data/combined_players.json") -> List[Dict]:
    """Load existing combined players data"""
    
    print(f"📊 Loading combined players from {json_path}...")
    
    try:
        with open(json_path, 'r') as f:
            players = json.load(f)
        
        print(f"✅ Loaded {len(players)} players")
        return players
        
    except FileNotFoundError:
        print(f"❌ File not found: {json_path}")
        return []
    except Exception as e:
        print(f"❌ Error loading JSON: {e}")
        return []


def merge_ranks(players: List[Dict], rank_map: Dict[str, int]) -> List[Dict]:
    """
    Add rank field to each player based on UPID mapping.
    
    Args:
        players: List of player dicts
        rank_map: Dict mapping UPID → rank
        
    Returns:
        Updated player list with rank field
    """
    
    print(f"🔄 Merging ranks into player data...")
    
    matched = 0
    unmatched = 0
    
    for player in players:
        upid = str(player.get('upid', '')).strip()
        
        if upid and upid in rank_map:
            player['rank'] = rank_map[upid]
            matched += 1
        else:
            # No rank found - assign high number (bottom of list)
            player['rank'] = 9999
            unmatched += 1
    
    print(f"✅ Matched ranks: {matched}")
    print(f"⚠️ Unmatched (assigned 9999): {unmatched}")
    
    return players


def save_combined_players(players: List[Dict], json_path: str = "data/combined_players.json"):
    """Save updated combined players data"""
    
    print(f"💾 Saving updated data to {json_path}...")
    
    # Create backup first
    backup_path = json_path.replace('.json', '_backup.json')
    if os.path.exists(json_path):
        import shutil
        shutil.copy(json_path, backup_path)
        print(f"📦 Backup saved to {backup_path}")
    
    # Save updated data
    with open(json_path, 'w') as f:
        json.dump(players, f, indent=2)
    
    print(f"✅ Saved {len(players)} players with ranks")


def verify_ranks(players: List[Dict]):
    """Verify rank integration worked correctly"""
    
    print(f"\n📊 Rank Distribution:")
    print(f"=" * 60)
    
    # Count by rank ranges
    top_50 = sum(1 for p in players if p.get('rank', 9999) <= 50)
    top_100 = sum(1 for p in players if p.get('rank', 9999) <= 100)
    top_200 = sum(1 for p in players if p.get('rank', 9999) <= 200)
    unranked = sum(1 for p in players if p.get('rank', 9999) == 9999)
    
    print(f"Top 50:    {top_50} players")
    print(f"Top 100:   {top_100} players")
    print(f"Top 200:   {top_200} players")
    print(f"Unranked:  {unranked} players")
    
    # Show some examples
    print(f"\n🔝 Top 10 Ranked Prospects:")
    print(f"=" * 60)
    
    # Get Farm players only (prospects)
    prospects = [p for p in players if p.get('player_type') == 'Farm']
    
    # Sort by rank
    prospects_sorted = sorted(prospects, key=lambda p: p.get('rank', 9999))
    
    for i, p in enumerate(prospects_sorted[:10], 1):
        rank = p.get('rank', '???')
        name = p.get('name', 'Unknown')
        pos = p.get('position', '?')
        team = p.get('team', '?')
        manager = p.get('manager', 'Available')
        
        print(f"{i}. Rank {rank:3d}: {name} ({pos}) [{team}] - {manager}")


def main():
    """Main execution"""
    
    print("🚀 Phase 1: Rank Integration")
    print("=" * 60)
    print()
    
    # 1. Load rank data from CSV
    rank_map = load_player_database_csv("data/Player_Database.csv")
    
    if not rank_map:
        print("❌ Failed to load rankings - check CSV path")
        return
    
    print()
    
    # 2. Load combined players
    players = load_combined_players("data/combined_players.json")
    
    if not players:
        print("❌ Failed to load combined players")
        return
    
    print()
    
    # 3. Merge ranks
    players_with_ranks = merge_ranks(players, rank_map)
    
    print()
    
    # 4. Save updated data
    save_combined_players(players_with_ranks, "data/combined_players.json")
    
    print()
    
    # 5. Verify
    verify_ranks(players_with_ranks)
    
    print()
    print("=" * 60)
    print("✅ Phase 1 Complete!")
    print()
    print("Next Steps:")
    print("  1. Review top ranked prospects above")
    print("  2. Check data/combined_players_backup.json if needed")
    print("  3. Proceed to Phase 2: Database Tracker")


if __name__ == "__main__":
    main()