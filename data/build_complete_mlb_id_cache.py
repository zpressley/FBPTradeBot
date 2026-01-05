#!/usr/bin/env python3
"""
Build COMPLETE MLB ID cache using the UPID master database
This should get all 2,125 prospects, not just 312
"""

import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# UPID Master Database (6,018 players with MLB IDs)
UPID_SHEET_KEY = "19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo"
UPID_TAB = "PlayerUPID"

# FBP Player Database
FBP_SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
FBP_TAB = "Player Data"

CACHE_FILE = "data/mlb_id_cache.json"

def authorize_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)

def get_fbp_farm_upids(gclient):
    """Get all UPIDs of Farm players from FBP database"""
    print("📊 Loading FBP Farm players...")
    sheet = gclient.open_by_key(FBP_SHEET_KEY).worksheet(FBP_TAB)
    
    # Use expected headers to avoid duplicate column issues
    expected_headers = [
        "UPID", "Player Name", "Team", "Pos", "Player Type", "Manager",
        "Contract Type", "Status", "Years (Simple)"
    ]
    
    all_data = sheet.get_all_values()
    headers = all_data[0]
    
    # Find column indices
    try:
        upid_idx = 3  # UPID is column D (index 3)
        player_type_idx = headers.index("Player Type")
    except ValueError:
        print("❌ Could not find required columns")
        return set()
    
    upids = set()
    player_names = {}
    
    for row in all_data[1:]:
        if len(row) <= max(upid_idx, player_type_idx):
            continue
        
        player_type = row[player_type_idx].strip() if player_type_idx < len(row) else ""
        
        if player_type == "Farm":
            upid = str(row[upid_idx]).strip() if upid_idx < len(row) else ""
            name = row[1].strip() if len(row) > 1 else ""  # Player Name in column B
            
            if upid and upid != "":
                upids.add(upid)
                if name:
                    player_names[upid] = name
    
    print(f"   ✅ Found {len(upids)} Farm player UPIDs")
    return upids, player_names

def get_upid_mlb_mappings(gclient):
    """Get MLB IDs from the UPID master database"""
    print("🔗 Loading UPID → MLB ID mappings...")
    sheet = gclient.open_by_key(UPID_SHEET_KEY).worksheet(UPID_TAB)
    
    # Get all data (headers in row 2, data starts row 3)
    all_data = sheet.get_all_values()
    
    # Headers are in row 2 (index 1)
    headers = all_data[1]
    
    # Find column indices
    # Based on earlier testing: Player Name=0, Team=1, Pos=2, UPID=3
    name_idx = 0
    upid_idx = 3
    
    # MLB ID should be around column 10-15, let's find it
    mlb_id_idx = None
    for i, header in enumerate(headers):
        if 'mlb' in header.lower() and 'id' in header.lower():
            mlb_id_idx = i
            break
    
    if mlb_id_idx is None:
        # Try common positions
        for idx in [10, 11, 12, 13, 14, 15]:
            if idx < len(headers):
                print(f"   Checking column {idx}: {headers[idx]}")
    
    mappings = {}
    
    # Data starts at row 3 (index 2)
    for row in all_data[2:]:
        if len(row) <= upid_idx:
            continue
        
        name = row[name_idx].strip() if name_idx < len(row) else ""
        upid = str(row[upid_idx]).strip() if upid_idx < len(row) else ""
        
        # Try to find MLB ID
        mlb_id = None
        
        if mlb_id_idx and mlb_id_idx < len(row):
            mlb_id_val = row[mlb_id_idx].strip()
            if mlb_id_val and mlb_id_val.isdigit():
                mlb_id = int(mlb_id_val)
        
        if upid and name:
            mappings[upid] = {
                "name": name,
                "mlb_id": mlb_id  # Will be None if not found
            }
    
    with_mlb = sum(1 for m in mappings.values() if m['mlb_id'])
    print(f"   ✅ Loaded {len(mappings)} UPID mappings")
    print(f"   ✅ {with_mlb} have MLB IDs")
    
    return mappings

def build_complete_cache():
    """Build complete MLB ID cache for all FBP prospects"""
    gclient = authorize_gsheets()
    
    # Get FBP Farm player UPIDs
    fbp_upids, fbp_names = get_fbp_farm_upids(gclient)
    
    # Get UPID → MLB ID mappings
    upid_mappings = get_upid_mlb_mappings(gclient)
    
    # Build cache
    print("\n🔨 Building MLB ID cache...")
    cache = {}
    found = 0
    missing = 0
    
    for upid in fbp_upids:
        if upid in upid_mappings:
            mapping = upid_mappings[upid]
            
            # Use FBP name if available, otherwise UPID database name
            name = fbp_names.get(upid, mapping['name'])
            mlb_id = mapping['mlb_id']
            
            if mlb_id:
                cache[upid] = {
                    "name": name,
                    "mlb_id": mlb_id
                }
                found += 1
                if found <= 10:  # Show first 10
                    print(f"   ✅ {name:<30} UPID: {upid:<10} → MLB ID: {mlb_id}")
            else:
                missing += 1
                if missing <= 5:  # Show first 5 missing
                    print(f"   ⚠️  {name:<30} UPID: {upid:<10} → No MLB ID")
        else:
            missing += 1
    
    # Save cache
    os.makedirs("data", exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    
    print(f"\n" + "="*60)
    print(f"✅ MLB ID cache saved to {CACHE_FILE}")
    print(f"   • {found} prospects with MLB IDs (can fetch MiLB stats)")
    print(f"   • {missing} prospects without MLB IDs (need manual lookup)")
    print(f"   • Total cache size: {len(cache)} entries")
    print("="*60)
    
    if missing > 0:
        print(f"\n💡 Note: {missing} prospects don't have MLB IDs yet")
        print("   These are likely:")
        print("   - Very new prospects (not yet in MLB system)")
        print("   - International prospects (no UPID → MLB ID mapping)")
        print("   - Retired/released players")

if __name__ == "__main__":
    print("🚀 Building Complete MLB ID Cache")
    print("="*60)
    build_complete_cache()