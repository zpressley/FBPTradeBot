#!/usr/bin/env python3
"""
Build MLB ID cache by searching MLB Stats API
For prospects without MLB IDs in the Player ID Map tab
"""

import json
import os
import time
import requests
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
MLB_SEARCH_URL = "https://statsapi.mlb.com/api/v1/sports/1/players"

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def get_existing_cache(gclient):
    """Load existing MLB IDs from Player ID Map tab"""
    print("📋 Loading existing MLB IDs from Player ID Map tab...")
    
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
        
        print(f"   ✅ Loaded {len(cache)} existing MLB IDs")
        return cache
    except Exception as e:
        print(f"   ⚠️ Could not load Player ID Map: {e}")
        return {}

def get_farm_prospects(gclient):
    """Get all Farm players from FBP database"""
    print("📊 Loading Farm players from FBP database...")
    
    sheet = gclient.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
    all_data = sheet.get_all_values()
    headers = all_data[0]
    
    player_type_idx = headers.index("Player Type")
    upid_idx = 3  # Column D
    name_idx = 1  # Column B (Player Name)
    
    prospects = []
    for row in all_data[1:]:
        if len(row) <= max(player_type_idx, upid_idx, name_idx):
            continue
        
        if row[player_type_idx].strip() == "Farm":
            upid = str(row[upid_idx]).strip()
            name = row[name_idx].strip()
            
            if upid and name:
                prospects.append({
                    "upid": upid,
                    "name": name
                })
    
    print(f"   ✅ Found {len(prospects)} Farm players")
    return prospects

def search_mlb_api_by_name(player_name):
    """Search MLB Stats API for player by name"""
    try:
        # Clean up name for search
        search_name = player_name.strip()
        
        # Try exact search first
        params = {
            'season': 2024,  # Use recent season
            'sportId': 1     # MLB
        }
        
        response = requests.get(MLB_SEARCH_URL, params=params, timeout=10)
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        players = data.get('people', [])
        
        # Look for name matches
        best_match = None
        best_ratio = 0
        
        for player in players:
            api_name = player.get('fullName', '')
            
            # Calculate similarity
            ratio = SequenceMatcher(None, search_name.lower(), api_name.lower()).ratio()
            
            if ratio > best_ratio and ratio >= 0.85:  # 85% match threshold
                best_ratio = ratio
                best_match = {
                    'mlb_id': player.get('id'),
                    'api_name': api_name,
                    'match_ratio': ratio
                }
        
        return best_match
        
    except Exception as e:
        return None

def build_enhanced_cache():
    """Build complete cache using existing + MLB API lookups"""
    gclient = authorize_gsheets()
    
    # Load existing cache
    cache = get_existing_cache(gclient)
    existing_count = len(cache)
    
    # Get all farm prospects
    farm_prospects = get_farm_prospects(gclient)
    
    # Find prospects missing MLB IDs
    missing = [p for p in farm_prospects if p['upid'] not in cache]
    
    print(f"\n🔍 Analysis:")
    print(f"   Total Farm players: {len(farm_prospects)}")
    print(f"   Already have MLB ID: {existing_count}")
    print(f"   Need to find MLB ID: {len(missing)}")
    
    if len(missing) == 0:
        print("\n✅ All prospects already have MLB IDs!")
        return
    
    print(f"\n🌐 Searching MLB API for {len(missing)} prospects...")
    print(f"   (This may take a while - ~1 request per second)")
    
    found = 0
    not_found = 0
    
    for i, prospect in enumerate(missing, 1):
        # Rate limiting
        if i > 1:
            time.sleep(1.0)  # Be respectful to MLB API
        
        result = search_mlb_api_by_name(prospect['name'])
        
        if result:
            cache[prospect['upid']] = {
                "name": prospect['name'],
                "mlb_id": result['mlb_id']
            }
            found += 1
            
            if found <= 10:  # Show first 10
                match_pct = int(result['match_ratio'] * 100)
                print(f"   ✅ {prospect['name']:<25} → {result['api_name']:<25} ({match_pct}% match)")
        else:
            not_found += 1
        
        # Progress update
        if i % 50 == 0:
            print(f"      Progress: {i}/{len(missing)} ({found} found, {not_found} not found)")
    
    # Save cache
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    
    print(f"\n" + "="*60)
    print(f"✅ MLB ID cache saved to {CACHE_FILE}")
    print(f"   • Started with: {existing_count} MLB IDs")
    print(f"   • Found {found} new MLB IDs via API search")
    print(f"   • Total cache size: {len(cache)} entries")
    print(f"   • Still missing: {not_found} prospects")
    print("="*60)

if __name__ == "__main__":
    print("🚀 Building Enhanced MLB ID Cache")
    print("="*60)
    build_enhanced_cache()
