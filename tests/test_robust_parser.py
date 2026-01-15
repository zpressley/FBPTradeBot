#!/usr/bin/env python3
"""
Robust CSV parser for Player Database.csv
Handles the documentation row and extracts ranks correctly
"""

import csv
import json

def load_ranks_robust(csv_path="data/Player Database.csv"):
    """
    Robust rank loader that handles the CSV format correctly
    """
    
    print(f"📊 Loading ranks from {csv_path}...")
    
    rank_map = {}
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        # Read all lines
        lines = f.readlines()
        
        # Find header row (should be row 1, has "UPID,Player Name,...")
        header_idx = None
        for i, line in enumerate(lines):
            if line.startswith('UPID,'):
                header_idx = i
                break
        
        if header_idx is None:
            print("❌ Could not find header row")
            return {}
        
        print(f"✅ Found headers at line {header_idx}")
        
        # Parse headers
        headers = lines[header_idx].strip().split(',')
        
        # Find column indices
        upid_idx = headers.index('UPID') if 'UPID' in headers else None
        rank_idx = headers.index('Rank/ADP') if 'Rank/ADP' in headers else None
        name_idx = headers.index('Player Name') if 'Player Name' in headers else None
        
        if upid_idx is None or rank_idx is None:
            print(f"❌ Could not find required columns")
            print(f"   UPID column: {upid_idx}")
            print(f"   Rank/ADP column: {rank_idx}")
            return {}
        
        print(f"✅ Column indices: UPID={upid_idx}, Rank/ADP={rank_idx}, Name={name_idx}")
        
        # Parse data rows
        for i in range(header_idx + 1, len(lines)):
            line = lines[i].strip()
            if not line:
                continue
            
            # Split by comma (handle quotes)
            parts = line.split(',')
            
            if len(parts) <= max(upid_idx, rank_idx):
                continue
            
            upid = parts[upid_idx].strip().strip('"')
            rank_raw = parts[rank_idx].strip().strip('"')
            name = parts[name_idx].strip().strip('"') if name_idx and len(parts) > name_idx else ''
            
            if upid and rank_raw:
                try:
                    rank = int(rank_raw)
                    rank_map[upid] = rank
                    
                    if len(rank_map) <= 5:  # Show first 5
                        print(f"   Sample: UPID '{upid}' → Rank {rank} → {name}")
                        
                except ValueError:
                    continue
        
        print(f"✅ Loaded {len(rank_map)} total ranks")
        return rank_map

def main():
    """Test the robust parser"""
    
    print("🧪 Testing Robust CSV Parser")
    print("=" * 70)
    print()
    
    rank_map = load_ranks_robust()
    
    print()
    print("=" * 70)
    
    if len(rank_map) > 0:
        print(f"✅ SUCCESS: Loaded {len(rank_map)} ranks")
        
        # Show some examples
        print()
        print("Sample mappings:")
        for i, (upid, rank) in enumerate(list(rank_map.items())[:10], 1):
            print(f"  {i}. UPID '{upid}' → Rank {rank}")
        
        # Test specific prospects
        print()
        print("Known prospects:")
        test_upids = {
            '3445': 'Jordan Lawlar',
            '6566': 'Max Clark', 
            '7630': 'Charlie Condon',
            '3930': 'Brandon Sproat'
        }
        
        for upid, name in test_upids.items():
            if upid in rank_map:
                print(f"  ✅ {name}: UPID '{upid}' → Rank {rank_map[upid]}")
            else:
                print(f"  ❌ {name}: UPID '{upid}' NOT FOUND")
    else:
        print("❌ FAILED: No ranks loaded")

if __name__ == "__main__":
    main()