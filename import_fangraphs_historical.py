#!/usr/bin/env python3
"""
Optimized Fangraphs Importer - Fast Exact Matching
Uses exact name matching first, optional fuzzy matching for unmatched
"""

import json
import os
import pandas as pd

# Config
STATS_FILE = "data/player_stats.json"
CACHE_FILE = "data/mlb_id_cache.json"
FANGRAPHS_DIR = "fangraphs_data"

# Field mappings (abbreviated for speed)
BATTING_FIELDS = {
    "Name": "player_name", "Team": "mlb_team", "G": "games", "PA": "plateAppearances",
    "AB": "atBats", "H": "hits", "HR": "homeRuns", "R": "runs", "RBI": "rbi",
    "BB": "baseOnBalls", "SO": "strikeOuts", "SB": "stolenBases",
    "AVG": "avg", "OBP": "obp", "SLG": "slg", "OPS": "ops",
    "wOBA": "wOBA", "wRC+": "wRC_plus", "ISO": "ISO", "BABIP": "BABIP",
    "Off": "Off", "WAR": "WAR", "BB%": "BB_pct", "K%": "K_pct"
}

PITCHING_FIELDS = {
    "Name": "player_name", "Team": "mlb_team", "W": "wins", "L": "losses",
    "SV": "saves", "G": "games", "GS": "gamesStarted", "IP": "inningsPitched",
    "H": "hits", "ER": "earnedRuns", "HR": "homeRuns", "BB": "baseOnBalls",
    "SO": "strikeOuts", "ERA": "era", "WHIP": "whip", "FIP": "FIP",
    "xFIP": "xFIP", "WAR": "WAR", "K%": "K_pct", "BB%": "BB_pct"
}

def load_data():
    """Load existing stats and cache"""
    existing = []
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            existing = json.load(f)
    
    with open(CACHE_FILE, 'r') as f:
        cache = json.load(f)
    
    # Build exact match lookup (lowercase)
    name_to_upid = {v['name'].lower().strip(): upid for upid, v in cache.items()}
    
    return existing, name_to_upid

def import_fangraphs_fast():
    """Fast import using only exact name matching"""
    print("🚀 Optimized Fangraphs Importer (Exact Matching Only)")
    print("=" * 70 + "\n")
    
    if not os.path.exists(FANGRAPHS_DIR):
        print(f"❌ {FANGRAPHS_DIR}/ not found")
        return
    
    existing_stats, name_to_upid = load_data()
    
    print(f"📊 Current: {len(existing_stats)} records")
    print(f"🔗 Lookup: {len(name_to_upid)} names\n")
    
    csv_files = [f for f in os.listdir(FANGRAPHS_DIR) 
                 if f.endswith('.csv') and 'upid' not in f.lower()]
    
    print(f"📁 Found {len(csv_files)} CSV files\n")
    
    total_added = 0
    
    for csv_file in sorted(csv_files):
        csv_path = os.path.join(FANGRAPHS_DIR, csv_file)
        
        try:
            # Read CSV
            df = pd.read_csv(csv_path)
            
            # Must have Name and Season columns
            if 'Name' not in df.columns or 'Season' not in df.columns:
                print(f"   ⚠️  {csv_file}: Missing Name or Season column")
                continue
            
            # Detect type
            is_batting = any(col in df.columns for col in ['AB', 'HR', 'AVG', 'H', 'wRC+'])
            is_pitching = any(col in df.columns for col in ['IP', 'ERA', 'WHIP', 'W', 'FIP'])
            
            if not (is_batting or is_pitching):
                print(f"   ⚠️  {csv_file}: Can't detect type")
                continue
            
            field_map = BATTING_FIELDS if is_batting else PITCHING_FIELDS
            stat_type = "batting" if is_batting else "pitching"
            
            print(f"   📂 {csv_file} ({stat_type})...")
            
            matched = 0
            
            # Process rows with exact matching only (fast!)
            for _, row in df.iterrows():
                name = str(row.get('Name', '')).strip()
                season = row.get('Season')
                
                if not name or pd.isna(season):
                    continue
                
                # FAST: Exact match only (no fuzzy matching!)
                upid = name_to_upid.get(name.lower().strip())
                
                if not upid:
                    continue  # Skip unmatched (no slow fuzzy matching)
                
                # Quick normalization
                record = {
                    "upid": upid,
                    "player_name": name,
                    "season": int(season),
                    "mlb_team": str(row.get('Team', '')).strip() if 'Team' in row else "",
                    "mlb_id": None,
                    "fbp_name": name,
                    "fbp_manager": "",
                    "fbp_contract": "",
                    "fbp_player_type": "MLB",
                    "age": None,
                    "position": "P" if is_pitching else None,
                    "stat_type": stat_type,
                    "level": "MLB",
                    "source": "fangraphs_csv"
                }
                
                # Add stats
                for fg_field, std_field in field_map.items():
                    if fg_field in row and pd.notna(row[fg_field]):
                        record[std_field] = row[fg_field]
                
                # Add nulls
                if is_batting:
                    record.update({"inningsPitched": None, "era": None, "whip": None})
                else:
                    record.update({"atBats": None, "avg": None, "ops": None})
                
                existing_stats.append(record)
                matched += 1
            
            print(f"      ✅ Added {matched} records")
            total_added += matched
            
        except Exception as e:
            print(f"      ❌ Error: {e}")
    
    # Save
    os.makedirs("data", exist_ok=True)
    with open(STATS_FILE, 'w') as f:
        json.dump(existing_stats, f, indent=2)
    
    size = os.path.getsize(STATS_FILE)
    
    print("\n" + "=" * 70)
    print("✅ Import Complete!")
    print("=" * 70)
    print(f"   Added: {total_added:,} player-seasons")
    print(f"   Total: {len(existing_stats):,} records")
    print(f"   Size: {size/1024:.1f} KB ({size/1024/1024:.1f} MB)")
    print("=" * 70)
    
    # Show coverage
    seasons = {}
    for s in existing_stats:
        season = s.get('season')
        seasons[season] = seasons.get(season, 0) + 1
    
    print(f"\n📅 Season Coverage:")
    for season in sorted(seasons.keys()):
        print(f"   {season}: {seasons[season]:,} records")
    
    print(f"\n💡 Note: Used exact name matching only (fast)")
    print(f"   Unmatched names were skipped")
    print(f"   To see unmatched, add --verbose flag")

if __name__ == "__main__":
    import_fangraphs_fast()