#!/usr/bin/env python3
"""
Build complete MLB ID cache for all 2,128 Farm players
Uses MLB Stats API to search by player name since UPID sheet doesn't have MLB IDs
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

CACHE_FILE = "data/mlb_id_cache.json"

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def load_existing_id_map(gclient):
    """Load existing MLB IDs from Player ID Map tab (the 312 you already have)"""
    print("📋 Loading existing MLB IDs from 'Player ID Map' tab...")
    
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
        
        print(f"   ✅ Found {len(cache)} existing MLB IDs")
        return cache
    except Exception as e:
        print(f"   ⚠️ Could not load Player ID Map tab: {e}")
        return {}

def get_all_farm_players(gclient):
    """Get ALL Farm players with correct UPID from column 0"""
    print("📊 Loading all Farm players from FBP database...")
    
    sheet = gclient.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
    all_data = sheet.get_all_values()
    headers = all_data[0]
    
    # Correct column indices based on actual structure
    upid_idx = 0  # ← FIXED! Column 0, not 3
    name_idx = 1
    player_type_idx = headers.index("Player Type")
    
    prospects = []
    for row in all_data[1:]:
        if len(row) <= max(upid_idx, name_idx, player_type_idx):
            continue
        
        if row[player_type_idx].strip() == "Farm":
            upid = str(row[upid_idx]).strip()
            name = row[name_idx].strip()
            
            if upid and name:
                prospects.append({
                    "upid": upid,
                    "name": name
                })
    
    print(f"   ✅ Found {len(prospects)} Farm players with UPIDs")
    return prospects

def search_mlb_id_by_name(player_name):
    """Search MLB Stats API for player ID by name"""
    try:
        # Search endpoint
        url = "https://lookup-service-prod.mlb.com/json/named.search_player_all.bam"
        params = {
            'sport_code': "'mlb'",
            'active_sw': "'Y'",  # Active players
            'name_part': f"'{player_name.split()[0]}%'"  # Search by first name
        }
        
        response = requests.get(url, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('search_player_all', {}).get('queryResults', {}).get('row', [])
            
            # Handle single result (dict) or multiple (list)
            if isinstance(results, dict):
                results = [results]
            
            # Find best match
            for player in results:
                api_name = player.get('name_display_first_last', '')
                ratio = SequenceMatcher(None, player_name.lower(), api_name.lower()).ratio()
                
                if ratio >= 0.90:  # 90% match
                    return {
                        'mlb_id': int(player.get('player_id')),
                        'api_name': api_name,
                        'match_ratio': ratio
                    }
        
        # Fallback: try statsapi endpoint
        url2 = "https://statsapi.mlb.com/api/v1/sports/1/players"
        params2 = {'season': 2024}
        
        response2 = requests.get(url2, params=params2, timeout=10)
        if response2.status_code == 200:
            players = response2.json().get('people', [])
            
            for player in players:
                api_name = player.get('fullName', '')
                ratio = SequenceMatcher(None, player_name.lower(), api_name.lower()).ratio()
                
                if ratio >= 0.85:
                    return {
                        'mlb_id': player.get('id'),
                        'api_name': api_name,
                        'match_ratio': ratio
                    }
        
        return None
        
    except Exception as e:
        return None

def build_complete_cache():
    """Build complete MLB ID cache"""
    gclient = authorize_gsheets()
    
    # Start with existing IDs from Player ID Map tab
    cache = load_existing_id_map(gclient)
    starting_count = len(cache)
    
    # Get all Farm players
    farm_players = get_all_farm_players(gclient)
    
    # Find prospects that need MLB IDs
    missing = [p for p in farm_players if p['upid'] not in cache]
    
    print(f"\n🔍 Status:")
    print(f"   Total Farm players: {len(farm_players)}")
    print(f"   Already have MLB ID: {starting_count}")
    print(f"   Need to find MLB ID: {len(missing)}")
    
    if len(missing) == 0:
        print("\n✅ All prospects already have MLB IDs!")
        return
    
    print(f"\n🌐 Searching MLB API for {len(missing)} prospects...")
    print(f"   ⏱️  This will take ~{len(missing)} seconds (1 per second)")
    print(f"   (Showing first 20 matches)\n")
    
    found = 0
    not_found = 0
    
    for i, prospect in enumerate(missing, 1):
        # Rate limiting - be respectful
        if i > 1:
            time.sleep(1.0)
        
        result = search_mlb_id_by_name(prospect['name'])
        
        if result:
            cache[prospect['upid']] = {
                "name": prospect['name'],
                "mlb_id": result['mlb_id']
            }
            found += 1
            
            if found <= 20:  # Show first 20
                match_pct = int(result['match_ratio'] * 100)
                print(f"   ✅ {prospect['name']:<30} UPID:{prospect['upid']:<6} → MLB:{result['mlb_id']:>7} ({match_pct}%)")
        else:
            not_found += 1
        
        # Progress updates every 100
        if i % 100 == 0:
            elapsed = i
            remaining = len(missing) - i
            print(f"\n   ⏱️  Progress: {i}/{len(missing)} ({found} found, {not_found} not found, ~{remaining}s remaining)\n")
    
    # Save cache
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    
    print(f"\n" + "="*70)
    print(f"✅ MLB ID cache saved to {CACHE_FILE}")
    print(f"   • Started with: {starting_count} MLB IDs")
    print(f"   • Found {found} new MLB IDs via API")
    print(f"   • Total in cache: {len(cache)} entries")
    print(f"   • Still missing: {not_found} prospects")
    print("="*70)
    
    if not_found > 0:
        print(f"\n💡 {not_found} prospects without MLB IDs are likely:")
        print(f"   - International prospects not yet in MLB system")
        print(f"   - Very recent draftees (2024-2025)")
        print(f"   - Retired/released players")
        print(f"   - Name spelling mismatches")
    
    print(f"\n🚀 Next step: Run fetch_complete_stats.py")
    print(f"   Expected MiLB stats for ~{len(cache)} prospects")

if __name__ == "__main__":
    print("🚀 Building Complete MLB ID Cache")
    print("="*70)
    build_complete_cache()