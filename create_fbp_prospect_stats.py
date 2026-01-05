#!/usr/bin/env python3
"""
Create fbp_prospect_stats_2025.csv by merging MLB prospect CSVs with FBP data
Matches stats to your prospects using UPID → name → MLB ID matching
"""

import json
import os
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher

# Config
BATTER_CSV = "mlb_prospect_batstats_2025.csv"
PITCHER_CSV = "mlb_prospect_pitchstats_2025.csv"
FBP_SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
CACHE_FILE = "data/mlb_id_cache.json"
OUTPUT_FILE = "data/fbp_prospect_stats_2025.csv"

def load_mlb_id_cache():
    """Load MLB ID cache"""
    with open(CACHE_FILE, 'r') as f:
        return json.load(f)

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def get_fbp_farm_players(gclient):
    """Get all FBP Farm players"""
    sheet = gclient.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
    all_data = sheet.get_all_values()
    headers = all_data[0]
    
    upid_idx = 0
    name_idx = 1
    manager_idx = headers.index("Manager")
    contract_idx = headers.index("Contract Type")
    player_type_idx = headers.index("Player Type")
    
    farm = []
    for row in all_data[1:]:
        if len(row) > player_type_idx and row[player_type_idx].strip() == "Farm":
            upid = str(row[upid_idx]).strip()
            name = row[name_idx].strip()
            manager = row[manager_idx].strip() if manager_idx < len(row) else ""
            contract = row[contract_idx].strip() if contract_idx < len(row) else ""
            
            if upid and name:
                farm.append({
                    'upid': upid,
                    'name': name,
                    'manager': manager,
                    'contract': contract
                })
    
    return farm

def main():
    print("🚀 Creating FBP Prospect Stats Database")
    print("="*70 + "\n")
    
    # Load MLB prospect CSVs
    print("📊 Loading MLB prospect stats...")
    df_bat = pd.read_csv(BATTER_CSV)
    df_pitch = pd.read_csv(PITCHER_CSV)
    print(f"   ✅ {len(df_bat)} batters")
    print(f"   ✅ {len(df_pitch)} pitchers\n")
    
    # Load FBP data
    print("🌾 Loading FBP Farm players...")
    gclient = authorize_gsheets()
    fbp_farm = get_fbp_farm_players(gclient)
    print(f"   ✅ {len(fbp_farm)} Farm players\n")
    
    # Load MLB ID cache
    print("🔗 Loading MLB ID cache...")
    cache = load_mlb_id_cache()
    print(f"   ✅ {len(cache)} MLB IDs\n")
    
    # Create MLB ID → stats lookup
    print("🔗 Creating MLB ID → stats lookup...")
    mlb_stats = {}
    
    for _, row in df_bat.iterrows():
        mlb_id = int(row['playerId'])
        mlb_stats[mlb_id] = {
            'player_type': 'batter',
            'stats': row.to_dict()
        }
    
    for _, row in df_pitch.iterrows():
        mlb_id = int(row['playerId'])
        if mlb_id not in mlb_stats:  # Don't overwrite batters
            mlb_stats[mlb_id] = {
                'player_type': 'pitcher',
                'stats': row.to_dict()
            }
    
    print(f"   ✅ {len(mlb_stats)} unique MLB IDs with stats\n")
    
    # Match FBP prospects to stats
    print("🎯 Matching FBP prospects to MLB stats...")
    
    matched_records = []
    matched_count = 0
    no_mlb_id = 0
    no_stats = 0
    
    for prospect in fbp_farm:
        upid = prospect['upid']
        
        # Get MLB ID from cache
        cache_entry = cache.get(upid)
        
        if not cache_entry:
            no_mlb_id += 1
            continue
        
        mlb_id = cache_entry['mlb_id']
        
        # Get stats for this MLB ID
        stats_entry = mlb_stats.get(mlb_id)
        
        if not stats_entry:
            no_stats += 1
            continue
        
        # Merge FBP data + MLB stats
        record = {
            'upid': upid,
            'name': prospect['name'],
            'manager': prospect['manager'],
            'contract': prospect['contract'],
            'mlb_id': mlb_id,
            'player_type': stats_entry['player_type'],
            **stats_entry['stats']
        }
        
        matched_records.append(record)
        matched_count += 1
        
        if matched_count <= 10:
            ptype = stats_entry['player_type']
            print(f"   ✅ {prospect['name']:<30} {ptype:<8} MLB ID: {mlb_id}")
    
    # Create DataFrame and save
    print(f"\n💾 Saving prospect stats...")
    df_final = pd.DataFrame(matched_records)
    
    os.makedirs("data", exist_ok=True)
    df_final.to_csv(OUTPUT_FILE, index=False)
    
    print("\n" + "="*70)
    print(f"✅ FBP Prospect Stats saved to {OUTPUT_FILE}")
    print(f"   • Total prospects with stats: {len(df_final)}")
    print(f"   • Batters: {len(df_final[df_final['player_type'] == 'batter'])}")
    print(f"   • Pitchers: {len(df_final[df_final['player_type'] == 'pitcher'])}")
    print(f"   • Columns: {len(df_final.columns)}")
    print("="*70)
    
    print(f"\n📊 Coverage Analysis:")
    print(f"   Total FBP Farm players: {len(fbp_farm)}")
    print(f"   └─ Matched to stats: {matched_count} ({matched_count/len(fbp_farm)*100:.1f}%)")
    print(f"   └─ No MLB ID in cache: {no_mlb_id}")
    print(f"   └─ Have MLB ID but no stats: {no_stats}")
    
    print(f"\n💡 The {no_stats} with MLB ID but no stats are likely:")
    print(f"   - Too low level (not in 2024 prospect stats)")
    print(f"   - Graduated to MLB (no longer in prospect rankings)")
    print(f"   - International/complex league only")
    
    print(f"\n🎯 Next steps:")
    print(f"   1. Use fbp_prospect_stats_2025.csv in Discord bot")
    print(f"   2. Display stats on FBP Hub website")
    print(f"   3. Run merge_with_upid_alternates.py weekly to refresh")

if __name__ == "__main__":
    main()