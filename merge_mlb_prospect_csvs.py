#!/usr/bin/env python3
"""
Enhanced MLB ID matcher using UPID database alternate names
Matches FBP prospects → MLB prospect CSVs using primary + alternate names
"""

import json
import os
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher

# Config
FBP_SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
ID_MAP_TAB = "Player ID Map"

UPID_SHEET_KEY = "19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo"
UPID_TAB = "PlayerUPID"

CACHE_FILE = "data/mlb_id_cache.json"

# CSV files - exact names from user
CSV_LOCATIONS = [
    ("mlb_prospect_batstats_2025.csv", "mlb_prospect_pitchstats_2025.csv"),
    ("prospects__23_.csv", "prospects__24_.csv"),
    ("prospects (23).csv", "prospects (24).csv")
]

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def load_existing_cache(gclient):
    """Load existing MLB IDs"""
    print("📋 Loading existing MLB ID cache...")
    
    try:
        sheet = gclient.open_by_key(FBP_SHEET_KEY).worksheet(ID_MAP_TAB)
        records = sheet.get_all_records()
        
        cache = {}
        for row in records:
            upid = str(row.get("UPID", "")).strip()
            name = str(row.get("Player Name", "")).strip()
            mlb_id = row.get("MLB ID", "")
            
            if upid and name and mlb_id:
                try:
                    cache[upid] = {
                        "name": name,
                        "mlb_id": int(mlb_id)
                    }
                except (ValueError, TypeError):
                    pass
        
        print(f"   ✅ {len(cache)} existing MLB IDs")
        return cache
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
        return {}

def load_upid_alternate_names(gclient):
    """Load UPID database with alternate names for matching"""
    print("📚 Loading UPID database with alternate names...")
    
    sheet = gclient.open_by_key(UPID_SHEET_KEY).worksheet(UPID_TAB)
    all_data = sheet.get_all_values()
    
    # Headers in row 2, data starts row 3
    headers = all_data[1]
    
    # Column indices: Name=0, Team=1, Pos=2, UPID=3, Alt1=4, Alt2=5, Alt3=6
    upid_idx = 3
    name_idx = 0
    alt1_idx = 4
    alt2_idx = 5
    alt3_idx = 6
    
    upid_data = {}
    
    for row in all_data[2:]:  # Start at row 3
        if len(row) <= max(upid_idx, name_idx):
            continue
        
        upid = str(row[upid_idx]).strip()
        primary_name = row[name_idx].strip()
        
        if not upid or not primary_name:
            continue
        
        # Collect all names (primary + alternates)
        all_names = [primary_name]
        
        if alt1_idx < len(row) and row[alt1_idx].strip():
            all_names.append(row[alt1_idx].strip())
        if alt2_idx < len(row) and row[alt2_idx].strip():
            all_names.append(row[alt2_idx].strip())
        if alt3_idx < len(row) and row[alt3_idx].strip():
            all_names.append(row[alt3_idx].strip())
        
        upid_data[upid] = {
            'primary_name': primary_name,
            'all_names': all_names
        }
    
    print(f"   ✅ {len(upid_data)} UPIDs with alternate names")
    
    # Show example
    sample_upid = list(upid_data.keys())[0]
    sample = upid_data[sample_upid]
    print(f"   📝 Example: {sample['primary_name']}")
    if len(sample['all_names']) > 1:
        print(f"      Alternates: {', '.join(sample['all_names'][1:])}")
    
    print()
    return upid_data

def load_mlb_prospect_csvs():
    """Load MLB prospect CSVs"""
    print("📊 Loading MLB prospect CSVs...")
    
    # Try to find the CSV files
    batter_file = None
    pitcher_file = None
    
    for bat_name, pitch_name in CSV_LOCATIONS:
        print(f"   Checking: {bat_name}, {pitch_name}")
        if os.path.exists(bat_name) and os.path.exists(pitch_name):
            batter_file = bat_name
            pitcher_file = pitch_name
            print(f"   ✅ Found both files!")
            break
    
    if not batter_file:
        print("   ❌ Could not find prospect CSV files")
        print("   📁 Files in current directory:")
        csv_files = [f for f in os.listdir('.') if f.endswith('.csv')]
        for f in csv_files[:10]:
            print(f"      • {f}")
        return {}
    
    print(f"   📂 Using: {batter_file}")
    print(f"   📂 Using: {pitcher_file}")
    
    mlb_prospects = {}
    
    # Load batters
    try:
        df_bat = pd.read_csv(batter_file)
        print(f"   ✅ {len(df_bat)} batters loaded")
        for _, row in df_bat.iterrows():
            name = str(row['full_name']).strip()
            mlb_id = int(row['playerId'])
            mlb_prospects[name.lower()] = {
                'name': name,
                'mlb_id': mlb_id,
                'type': 'batter'
            }
    except Exception as e:
        print(f"   ❌ Error loading batters: {e}")
    
    # Load pitchers
    try:
        df_pitch = pd.read_csv(pitcher_file)
        print(f"   ✅ {len(df_pitch)} pitchers loaded")
        for _, row in df_pitch.iterrows():
            name = str(row['full_name']).strip()
            mlb_id = int(row['playerId'])
            if name.lower() not in mlb_prospects:
                mlb_prospects[name.lower()] = {
                    'name': name,
                    'mlb_id': mlb_id,
                    'type': 'pitcher'
                }
    except Exception as e:
        print(f"   ❌ Error loading pitchers: {e}")
    
    print(f"   📊 {len(mlb_prospects)} total unique prospects\n")
    return mlb_prospects

def get_fbp_farm_players(gclient):
    """Get FBP Farm players"""
    print("🌾 Loading FBP Farm players...")
    
    sheet = gclient.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
    all_data = sheet.get_all_values()
    headers = all_data[0]
    
    upid_idx = 0
    name_idx = 1
    player_type_idx = headers.index("Player Type")
    
    farm = []
    for row in all_data[1:]:
        if len(row) > player_type_idx and row[player_type_idx].strip() == "Farm":
            upid = str(row[upid_idx]).strip()
            name = row[name_idx].strip()
            if upid and name:
                farm.append({'upid': upid, 'name': name})
    
    print(f"   ✅ {len(farm)} Farm players\n")
    return farm

def match_with_alternates(fbp_prospect, mlb_prospects, upid_alternates):
    """Try matching using primary name + all alternates"""
    upid = fbp_prospect['upid']
    
    # Get all possible names for this UPID
    upid_info = upid_alternates.get(upid)
    if not upid_info:
        search_names = [fbp_prospect['name']]
    else:
        search_names = upid_info['all_names']
    
    # Try each name variant
    for search_name in search_names:
        # Exact match
        if search_name.lower() in mlb_prospects:
            return mlb_prospects[search_name.lower()], 'exact', search_name
        
        # Fuzzy match (90% threshold for alternates)
        for mlb_name_lower, mlb_data in mlb_prospects.items():
            ratio = SequenceMatcher(None, search_name.lower(), mlb_data['name'].lower()).ratio()
            if ratio >= 0.90:
                return mlb_data, 'fuzzy', search_name
    
    return None, None, None

def merge_with_alternates():
    """Main merge using alternate names"""
    gclient = authorize_gsheets()
    
    existing = load_existing_cache(gclient)
    upid_alternates = load_upid_alternate_names(gclient)
    mlb_prospects = load_mlb_prospect_csvs()
    fbp_farm = get_fbp_farm_players(gclient)
    
    if not mlb_prospects:
        print("❌ No MLB prospect CSVs loaded. Exiting.")
        return
    
    cache = existing.copy()
    needs_match = [p for p in fbp_farm if p['upid'] not in cache]
    
    print(f"🔗 Matching {len(needs_match)} prospects using alternate names...\n")
    
    exact = 0
    fuzzy = 0
    no_match = 0
    
    for prospect in needs_match:
        result, match_type, matched_name = match_with_alternates(
            prospect, mlb_prospects, upid_alternates
        )
        
        if result:
            cache[prospect['upid']] = {
                'name': prospect['name'],
                'mlb_id': result['mlb_id']
            }
            
            if match_type == 'exact':
                exact += 1
                if exact <= 15:
                    alt_note = f" (via '{matched_name}')" if matched_name != prospect['name'] else ""
                    print(f"   ✅ {prospect['name']:<35}{alt_note} → MLB:{result['mlb_id']}")
            else:
                fuzzy += 1
                if fuzzy <= 10:
                    print(f"   🔸 {prospect['name']:<35} ≈ {result['name']:<30}")
        else:
            no_match += 1
    
    # Save
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    
    print(f"\n" + "="*70)
    print(f"✅ MLB ID cache updated!")
    print(f"   • Started: {len(existing)}")
    print(f"   • Exact matches: {exact}")
    print(f"   • Fuzzy matches: {fuzzy}")
    print(f"   • Total added: {exact + fuzzy}")
    print(f"   • Final size: {len(cache)}")
    print(f"   • No match: {no_match}")
    print("="*70)
    
    coverage = (len(cache) / len(fbp_farm)) * 100
    print(f"\n📊 Coverage: {len(cache)}/{len(fbp_farm)} ({coverage:.1f}%)")
    
    if no_match > 0:
        print(f"\n💡 {no_match} prospects still missing are likely:")
        print(f"   - Too low level (DSL, Complex, Rookie ball only)")
        print(f"   - International signings not in 2024 stats")
        print(f"   - Name variants not covered by alternates")

if __name__ == "__main__":
    print("🚀 Enhanced MLB ID Matcher with UPID Alternates")
    print("="*70 + "\n")
    merge_with_alternates()