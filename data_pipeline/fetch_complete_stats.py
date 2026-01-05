#!/usr/bin/env python3
"""
FBP Complete Stats Importer
Place in: data_pipeline/fetch_all_stats.py
Run from: project root OR data_pipeline folder

Fetches:
1. MLB Advanced Stats (Fangraphs via pybaseball) - wOBA, FIP, xFIP, etc.
2. ALL MiLB Stats (MLB Stats API) - AAA, AA, A+, A, Rookie stats

Output: CSV files in data/ folder with UPID, MLB_ID, Yahoo_ID mappings
"""

import pandas as pd
import json
import os
import sys
import requests
import time
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Try pybaseball import
try:
    from pybaseball import batting_stats, pitching_stats
    PYBASEBALL_AVAILABLE = True
except ImportError:
    print("⚠️ pybaseball not installed")
    print("Install: pip3 install pybaseball --break-system-packages")
    PYBASEBALL_AVAILABLE = False

# ========================================
# CONFIGURATION
# ========================================

START_YEAR = 2022
END_YEAR = 2025

# Determine if running from project root or data_pipeline/
if os.path.basename(os.getcwd()) == 'data_pipeline':
    # Running from data_pipeline/
    DATA_DIR = "../data"
else:
    # Running from project root
    DATA_DIR = "data"

# File paths
COMBINED_PLAYERS = os.path.join(DATA_DIR, "combined_players.json")
MLB_ID_CACHE = os.path.join(DATA_DIR, "mlb_id_cache.json")
OUTPUT_DIR = DATA_DIR

# MiLB Levels
MILB_LEVELS = {
    11: 'Triple-A',
    12: 'Double-A', 
    13: 'High-A',
    14: 'Single-A',
    15: 'Low-A',
    16: 'Rookie',
    17: 'Winter'
}

# ========================================
# FUNCTIONS
# ========================================

def load_player_ids():
    """Load player ID mappings from combined_players.json and mlb_id_cache.json"""
    print("\n📋 Loading player IDs...")
    
    # Load combined players
    try:
        with open(COMBINED_PLAYERS, 'r') as f:
            players = json.load(f)
        print(f"✅ Loaded {len(players)} players from combined_players.json")
    except FileNotFoundError:
        print(f"❌ File not found: {COMBINED_PLAYERS}")
        return {}
    
    # Load MLB ID cache
    try:
        with open(MLB_ID_CACHE, 'r') as f:
            mlb_cache = json.load(f)
        print(f"✅ Loaded {len(mlb_cache)} MLB IDs from cache")
    except FileNotFoundError:
        print(f"⚠️ File not found: {MLB_ID_CACHE}")
        mlb_cache = {}
    
    # Build mapping
    id_map = {}
    
    for player in players:
        name = player.get('name', '').strip()
        if not name:
            continue
        
        upid = player.get('upid', '')
        mlb_id = mlb_cache.get(upid, {}).get('mlb_id', '') if upid else ''
        
        id_map[name.lower()] = {
            'name': name,
            'upid': upid,
            'mlb_id': mlb_id,
            'yahoo_id': player.get('yahoo_id', ''),
            'position': player.get('position', ''),
            'team': player.get('team', ''),
            'manager': player.get('manager', ''),
            'player_type': player.get('player_type', '')
        }
    
    return id_map


def fetch_mlb_advanced_stats():
    """Fetch MLB advanced stats from Fangraphs (not in Yahoo)"""
    if not PYBASEBALL_AVAILABLE:
        print("\n⚠️ Skipping MLB advanced stats (pybaseball not installed)")
        return None
    
    print(f"\n{'='*70}")
    print(f"PART 1: MLB ADVANCED STATS (Fangraphs)")
    print(f"{'='*70}")
    print(f"Fetching {START_YEAR}-{END_YEAR}...")
    print("⏱️  This takes 2-3 minutes (scraping Fangraphs)...")
    
    all_records = []
    
    # Batting
    try:
        print(f"\n⚾ Batting...")
        batting = batting_stats(START_YEAR, END_YEAR, qual=50)
        print(f"✅ {len(batting)} batter-seasons")
        
        # Advanced stats only (not in Yahoo)
        advanced_cols = [
            'WAR', 'wOBA', 'wRC+', 'ISO', 'BABIP', 'Spd', 'Off', 'BsR',
            'BB%', 'K%', 'O-Swing%', 'Z-Swing%', 'SwStr%',
            'GB%', 'FB%', 'LD%', 'Pull%', 'Hard%', 'Barrel%'
        ]
        
        for _, row in batting.iterrows():
            record = {
                'name': row['Name'],
                'season': int(row['Season']),
                'stat_source': 'fangraphs',
                'stat_type': 'batting'
            }
            
            for col in advanced_cols:
                if col in row.index and pd.notna(row[col]):
                    clean = f"fg_{col.lower().replace('%','_pct').replace('/','_').replace('-','_').replace('+','_plus')}"
                    record[clean] = row[col]
            
            all_records.append(record)
        
        print(f"   Added {len([c for c in advanced_cols if c in batting.columns])} advanced stats")
        
    except Exception as e:
        print(f"❌ Batting error: {e}")
    
    # Pitching
    try:
        print(f"\n🥎 Pitching...")
        pitching = pitching_stats(START_YEAR, END_YEAR, qual=20)
        print(f"✅ {len(pitching)} pitcher-seasons")
        
        advanced_cols_pitch = [
            'WAR', 'FIP', 'xFIP', 'SIERA', 'K%', 'BB%', 'K-BB%',
            'BABIP', 'LOB%', 'GB%', 'FB%', 'SwStr%', 'HR/FB',
            'FA%', 'SL%', 'CH%', 'CU%', 'vFA', 'Hard%'
        ]
        
        for _, row in pitching.iterrows():
            record = {
                'name': row['Name'],
                'season': int(row['Season']),
                'stat_source': 'fangraphs',
                'stat_type': 'pitching'
            }
            
            for col in advanced_cols_pitch:
                if col in row.index and pd.notna(row[col]):
                    clean = f"fg_{col.lower().replace('%','_pct').replace('/','_').replace('-','_')}"
                    record[clean] = row[col]
            
            all_records.append(record)
        
        print(f"   Added {len([c for c in advanced_cols_pitch if c in pitching.columns])} advanced stats")
        
    except Exception as e:
        print(f"❌ Pitching error: {e}")
    
    if all_records:
        return pd.DataFrame(all_records)
    return None


def fetch_milb_stats(id_map):
    """Fetch ALL MiLB stats for prospects from MLB Stats API"""
    print(f"\n{'='*70}")
    print(f"PART 2: MINOR LEAGUE STATS (All Levels)")
    print(f"{'='*70}")
    print("Fetching stats for prospects in minors...")
    
    # Filter to prospects with MLB IDs
    prospects = [p for p in id_map.values() 
                 if p['player_type'] == 'Farm' and p['mlb_id']]
    
    print(f"📋 {len(prospects)} prospects to check")
    
    if not prospects:
        print("⚠️ No prospects with MLB IDs found")
        return None
    
    milb_records = []
    
    for i, prospect in enumerate(prospects, 1):
        mlb_id = prospect['mlb_id']
        
        url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats"
        params = {'stats': 'season', 'season': 2025, 'group': 'hitting,pitching'}
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                continue
            
            data = response.json()
            
            for stat_group in data.get('stats', []):
                for split in stat_group.get('splits', []):
                    sport_id = split.get('sport', {}).get('id', 1)
                    
                    # Only MiLB (skip MLB = sportId 1)
                    if sport_id == 1:
                        continue
                    
                    stat_dict = split.get('stat', {})
                    if not stat_dict:
                        continue
                    
                    # Base record with IDs
                    record = {
                        'upid': prospect['upid'],
                        'mlb_id': mlb_id,
                        'yahoo_id': prospect['yahoo_id'],
                        'name': prospect['name'],
                        'manager': prospect['manager'],
                        'season': 2025,
                        'stat_source': 'mlb_api_milb',
                        'level': MILB_LEVELS.get(sport_id, 'Unknown'),
                        'level_id': sport_id,
                        'team': split.get('team', {}).get('name', ''),
                        'stat_type': 'batting' if 'hitting' in stat_group.get('group', {}).get('displayName', '').lower() else 'pitching'
                    }
                    
                    # Add ALL stats with milb_ prefix
                    for key, value in stat_dict.items():
                        record[f"milb_{key}"] = value
                    
                    milb_records.append(record)
            
            if i % 10 == 0:
                print(f"  📊 Processed {i}/{len(prospects)}...")
            
            time.sleep(0.2)  # Rate limiting
            
        except Exception as e:
            continue
    
    if milb_records:
        print(f"\n✅ Fetched {len(milb_records)} MiLB stat records")
        print(f"   • {pd.DataFrame(milb_records)['name'].nunique()} prospects")
        return pd.DataFrame(milb_records)
    
    return None


def merge_and_save(mlb_df, milb_df, id_map):
    """Merge MLB advanced + MiLB stats, add IDs, save CSVs"""
    print(f"\n🔗 Merging and mapping to IDs...")
    
    all_records = []
    
    # MLB Advanced Stats - add IDs
    if mlb_df is not None:
        for _, row in mlb_df.iterrows():
            name_key = row['name'].lower()
            if name_key in id_map:
                ids = id_map[name_key]
                record = {
                    'upid': ids['upid'],
                    'mlb_id': ids['mlb_id'],
                    'yahoo_id': ids['yahoo_id'],
                    'manager': ids['manager'],
                    **row.to_dict()
                }
                all_records.append(record)
    
    # MiLB Stats - already have IDs
    if milb_df is not None:
        all_records.extend(milb_df.to_dict('records'))
    
    if not all_records:
        print("❌ No data to save")
        return
    
    # Create DataFrame
    final_df = pd.DataFrame(all_records)
    
    # Save complete file
    complete_file = os.path.join(OUTPUT_DIR, "fbp_complete_stats.csv")
    final_df.to_csv(complete_file, index=False)
    print(f"✅ Saved: {complete_file}")
    print(f"   • {len(final_df)} total records")
    print(f"   • {final_df['name'].nunique()} players")
    
    # Save MLB-only
    if mlb_df is not None:
        mlb_only = final_df[final_df['stat_source'] == 'fangraphs']
        mlb_file = os.path.join(OUTPUT_DIR, "fbp_mlb_advanced.csv")
        mlb_only.to_csv(mlb_file, index=False)
        print(f"   • {mlb_file} ({len(mlb_only)} MLB)")
    
    # Save MiLB-only
    if milb_df is not None:
        milb_only = final_df[final_df['stat_source'] == 'mlb_api_milb']
        milb_file = os.path.join(OUTPUT_DIR, "fbp_milb_stats.csv")
        milb_only.to_csv(milb_file, index=False)
        print(f"   • {milb_file} ({len(milb_only)} MiLB)")
    
    return final_df


def main():
    print("=" * 70)
    print("🚀 FBP COMPLETE STATS IMPORTER")
    print("=" * 70)
    print(f"Working directory: {os.getcwd()}")
    print(f"Output directory: {os.path.abspath(OUTPUT_DIR)}")
    print("=" * 70)
    
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load player IDs
    id_map = load_player_ids()
    if not id_map:
        print("❌ Cannot proceed without player IDs")
        return
    
    # Fetch MLB advanced stats (Fangraphs)
    mlb_df = fetch_mlb_advanced_stats()
    
    # Fetch MiLB stats (MLB API)
    milb_df = fetch_milb_stats(id_map)
    
    # Merge and save
    if mlb_df is not None or milb_df is not None:
        final_df = merge_and_save(mlb_df, milb_df, id_map)
    else:
        print("❌ No data fetched")
        return
    
    # Summary
    print(f"\n{'='*70}")
    print(f"✅ IMPORT COMPLETE!")
    print(f"{'='*70}")
    
    print(f"\n📁 Files in {OUTPUT_DIR}/:")
    print(f"   1. fbp_complete_stats.csv - Everything combined")
    print(f"   2. fbp_mlb_advanced.csv - MLB advanced only")
    print(f"   3. fbp_milb_stats.csv - MiLB only")
    
    print(f"\n📊 What you got:")
    
    if mlb_df is not None:
        mlb_count = len(mlb_df[mlb_df['stat_type'] == 'batting'])
        pitch_count = len(mlb_df[mlb_df['stat_type'] == 'pitching'])
        print(f"   ⚾ MLB Advanced: {mlb_count} batters, {pitch_count} pitchers")
        print(f"      Stats: wOBA, wRC+, FIP, xFIP, ISO, BABIP, etc.")
    
    if milb_df is not None:
        levels = milb_df['level'].value_counts().to_dict()
        print(f"   🏟️  MiLB Stats: {len(milb_df)} records across levels:")
        for level, count in levels.items():
            print(f"      • {level}: {count} records")
    
    print(f"\n💡 Quick lookup:")
    print(f"""
import pandas as pd

stats = pd.read_csv('{OUTPUT_DIR}/fbp_complete_stats.csv')

# MLB player - get advanced stats
mlb_player = stats[(stats['upid'] == 'YOUR_UPID') & 
                   (stats['stat_source'] == 'fangraphs')]
woba = mlb_player['fg_woba'].values[0]

# Prospect - get MiLB stats
prospect = stats[(stats['upid'] == 'YOUR_UPID') & 
                 (stats['stat_source'] == 'mlb_api_milb')]
level = prospect['level'].values[0]
avg = prospect['milb_avg'].values[0]
    """)
    
    print(f"\n🚀 Next: Add to Discord bot, FBP Hub website")
    print("=" * 70)


if __name__ == "__main__":
    main()
