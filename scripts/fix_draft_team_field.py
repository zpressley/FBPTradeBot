#!/usr/bin/env python3
"""
Fix draft_order_2026.json: Ensure 'team' field matches 'current_owner'

The 'team' field is legacy and should always equal 'current_owner'.
"""

import json

def fix_draft_team_fields():
    # Load draft order
    with open('data/draft_order_2026.json', 'r') as f:
        data = json.load(f)
    
    fixed_count = 0
    
    for pick in data:
        # Skip comment objects
        if '_comment' in pick:
            continue
        
        # Only process keeper draft picks
        if pick.get('draft') != 'keeper':
            continue
        
        current_owner = pick.get('current_owner')
        team = pick.get('team')
        
        if current_owner and team != current_owner:
            print(f"Fixing: Round {pick['round']}, Pick {pick['pick']}")
            print(f"  Before: team={team}, current_owner={current_owner}")
            pick['team'] = current_owner
            print(f"  After:  team={pick['team']}, current_owner={current_owner}")
            fixed_count += 1
    
    # Save updated data
    with open('data/draft_order_2026.json', 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"\n✅ Fixed {fixed_count} keeper draft picks")
    print("📝 'team' field now matches 'current_owner' for all picks")

if __name__ == '__main__':
    fix_draft_team_fields()
