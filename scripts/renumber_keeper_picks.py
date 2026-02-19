#!/usr/bin/env python3
"""
Renumber keeper draft picks to reset within each round (1-12 per round)
instead of sequential numbering across all picks.
"""

import json
from collections import defaultdict

def renumber_keeper_picks():
    # Load draft order
    with open('data/draft_order_2026.json', 'r') as f:
        data = json.load(f)
    
    # Track picks per round for keeper draft
    round_counts = defaultdict(int)
    
    # Update pick numbers
    updated_count = 0
    for pick in data:
        # Skip non-keeper picks and comments
        if '_comment' in pick or pick.get('draft') != 'keeper':
            continue
        
        round_num = pick['round']
        round_counts[round_num] += 1
        
        # Set pick number to count within round
        old_pick = pick['pick']
        new_pick = round_counts[round_num]
        
        if old_pick != new_pick:
            pick['pick'] = new_pick
            updated_count += 1
    
    # Save updated data
    with open('data/draft_order_2026.json', 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"✅ Renumbered {updated_count} keeper draft picks")
    print(f"📊 Picks per round:")
    for round_num in sorted(round_counts.keys()):
        print(f"   Round {round_num}: {round_counts[round_num]} picks")

if __name__ == '__main__':
    renumber_keeper_picks()
