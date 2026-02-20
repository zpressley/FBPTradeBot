#!/usr/bin/env python3
"""
Build keeper pool JSON with Yahoo 2025 stats merged in.
This creates a comprehensive player pool for the keeper draft with all relevant stats.
"""

import json
import csv
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"
STATS_DIR = DATA_DIR / "stats"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "fbp-hub" / "data"

COMBINED_PLAYERS_FILE = DATA_DIR / "combined_players.json"
YAHOO_STATS_FILE = STATS_DIR / "yahoo_players_2025_stats.csv"
OUTPUT_FILE = OUTPUT_DIR / "keeper_pool_2026.json"


def load_combined_players():
    """Load combined_players.json and filter to MLB players only."""
    with open(COMBINED_PLAYERS_FILE, 'r') as f:
        all_players = json.load(f)
    
    # Filter to MLB players only (no prospects)
    mlb_players = [p for p in all_players if p.get('player_type') == 'MLB']
    print(f"Loaded {len(mlb_players)} MLB players from combined_players.json")
    
    return mlb_players


def load_yahoo_stats():
    """Load Yahoo 2025 stats and index by yahoo_id."""
    stats_by_yahoo_id = {}
    
    with open(YAHOO_STATS_FILE, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)  # Skip header
        
        # CSV has duplicate column names, so we need to access by index:
        # 0: player_id, 1: name, 2: team, 3: position
        # BATTER STATS: 4: H/AB, 5: R, 6: H, 7: HR, 8: RBI, 9: SB, 10: BB, 11: K, 12: TB, 13: AVG, 14: OPS
        # PITCHER STATS: 15: APP, 16: IP, 17: ER, 18: HR, 19: K, 20: TB, 21: ERA, 22: K/9, 23: H/9, 24: BB/9, 25: QS
        
        for row in reader:
            yahoo_id = row[0]  # player_id
            
            # Parse stat value, return None if empty or '-'
            def parse_stat(val):
                if not val or val.strip() == '' or val.strip() == '-':
                    return None
                return val.strip()
            
            # Build stats object
            stats = {
                # Batter stats (columns 4-14)
                'H/AB': parse_stat(row[4]),
                'R': parse_stat(row[5]),
                'H': parse_stat(row[6]),
                'HR': parse_stat(row[7]),
                'RBI': parse_stat(row[8]),
                'SB': parse_stat(row[9]),
                'BB': parse_stat(row[10]),
                'K': parse_stat(row[11]),
                'TB': parse_stat(row[12]),
                'AVG': parse_stat(row[13]),
                'OPS': parse_stat(row[14]),
                
                # Pitcher stats (columns 15-25)
                'APP': parse_stat(row[15]),
                'IP': parse_stat(row[16]),
                'ER': parse_stat(row[17]),
                'HR_P': parse_stat(row[18]),  # HR allowed (pitchers)
                'K_P': parse_stat(row[19]),   # Strikeouts (pitchers)
                'TB_P': parse_stat(row[20]),  # Total bases allowed (pitchers)
                'ERA': parse_stat(row[21]),
                'K/9': parse_stat(row[22]),
                'H/9': parse_stat(row[23]),
                'BB/9': parse_stat(row[24]),
                'QS': parse_stat(row[25])
            }
            
            stats_by_yahoo_id[yahoo_id] = stats
    
    print(f"Loaded stats for {len(stats_by_yahoo_id)} players from Yahoo CSV")
    return stats_by_yahoo_id


def merge_stats(players, stats_by_yahoo_id):
    """Merge Yahoo stats into player records."""
    keeper_pool = []
    matched = 0
    unmatched = 0
    
    for player in players:
        yahoo_id = str(player.get('yahoo_id', '')).strip()
        
        # Create base keeper pool entry
        pool_entry = {
            'upid': player.get('upid'),
            'name': player.get('name'),
            'team': player.get('team'),
            'position': player.get('position'),
            'age': player.get('age'),
            'rank': player.get('rank'),  # FantasyPros rank
            'manager': player.get('manager'),  # Current owner
            'status': player.get('status'),
            'contract_type': player.get('contract_type'),
            'years_simple': player.get('years_simple'),
        }
        
        # Add stats if available
        if yahoo_id and yahoo_id in stats_by_yahoo_id:
            pool_entry['stats_2025'] = stats_by_yahoo_id[yahoo_id]
            matched += 1
        else:
            pool_entry['stats_2025'] = None
            unmatched += 1
        
        keeper_pool.append(pool_entry)
    
    print(f"Matched stats for {matched} players")
    print(f"No stats found for {unmatched} players")
    
    return keeper_pool


def main():
    """Main execution."""
    print("Building keeper pool with Yahoo 2025 stats...")
    print()
    
    # Load data
    players = load_combined_players()
    stats = load_yahoo_stats()
    
    # Merge
    keeper_pool = merge_stats(players, stats)
    
    # Sort by rank (nulls last)
    keeper_pool.sort(key=lambda p: (p['rank'] is None, p['rank'] if p['rank'] else 9999, p['name']))
    
    # Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(keeper_pool, f, indent=2)
    
    print()
    print(f"✅ Keeper pool written to {OUTPUT_FILE}")
    print(f"Total players in pool: {len(keeper_pool)}")


if __name__ == '__main__':
    main()
