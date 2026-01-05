#!/usr/bin/env python3
"""
Diagnostic script to find the 15 missing farm players
Compares Google Sheet (2,128 Farm) vs combined_players.json (2,113 Farm)
"""

import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Config
SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
COMBINED_FILE = "data/combined_players.json"
YAHOO_FILE = "data/yahoo_players.json"

def get_sheet_farm_players():
    """Get all Farm players from Google Sheet"""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_KEY).worksheet(PLAYER_TAB)
    
    # Expected headers to handle duplicates
    expected_headers = [
        "UPID", "Player Name", "Team", "Pos", "Player Type", "Manager",
        "Contract Type", "Status", "Years (Simple)"
    ]
    
    try:
        records = sheet.get_all_records(expected_headers=expected_headers)
    except Exception as e:
        print(f"   ⚠️ Warning: Using fallback method due to: {e}")
        # Fallback: manual parsing
        all_data = sheet.get_all_values()
        headers = all_data[0]
        records = []
        for row in all_data[1:]:
            record = {}
            for i, header in enumerate(expected_headers):
                if i < len(row):
                    record[header] = row[i]
                else:
                    record[header] = ""
            records.append(record)
    
    farm_players = []
    for record in records:
        if str(record.get("Player Type", "")).strip() == "Farm":
            # Handle UPID as int or string
            upid = record.get("UPID", "")
            upid_str = str(upid).strip() if upid else ""
            
            # Handle other fields safely
            name = str(record.get("Player Name", "")).strip()
            manager = str(record.get("Manager", "")).strip()
            years = str(record.get("Years (Simple)", "")).strip()
            
            farm_players.append({
                "name": name,
                "upid": upid_str,
                "manager": manager,
                "years": years
            })
    
    return farm_players

def get_combined_farm_players():
    """Get Farm players from combined_players.json"""
    with open(COMBINED_FILE, 'r') as f:
        data = json.load(f)
    
    return [p for p in data if p.get('player_type') == 'Farm']

def get_yahoo_players():
    """Get all players from Yahoo rosters"""
    with open(YAHOO_FILE, 'r') as f:
        data = json.load(f)
    
    all_names = []
    for manager, roster in data.items():
        for player in roster:
            all_names.append(player.get('name', '').strip())
    
    return all_names

def normalize_name(name):
    """Normalize name for comparison"""
    return name.lower().strip()

def main():
    print("🔍 Diagnosing Missing Farm Players\n")
    print("=" * 60)
    
    # Load data
    print("📊 Loading data...")
    sheet_farm = get_sheet_farm_players()
    combined_farm = get_combined_farm_players()
    yahoo_names = get_yahoo_players()
    
    print(f"   Google Sheet Farm players: {len(sheet_farm):,}")
    print(f"   Combined JSON Farm players: {len(combined_farm):,}")
    print(f"   Yahoo roster players: {len(yahoo_names):,}")
    print(f"   Missing: {len(sheet_farm) - len(combined_farm)} players\n")
    
    # Create lookup sets
    sheet_names = {normalize_name(p['name']): p for p in sheet_farm}
    combined_names = {normalize_name(p['name']) for p in combined_farm}
    yahoo_names_norm = {normalize_name(n) for n in yahoo_names}
    
    # Find missing players
    missing = []
    for sheet_name, player_data in sheet_names.items():
        if sheet_name not in combined_names:
            # Check if they're on a Yahoo roster
            on_yahoo = sheet_name in yahoo_names_norm
            missing.append({
                **player_data,
                "on_yahoo_roster": on_yahoo
            })
    
    print(f"🔎 Found {len(missing)} missing farm players:\n")
    print("=" * 60)
    
    # Categorize
    on_yahoo = [p for p in missing if p['on_yahoo_roster']]
    not_on_yahoo = [p for p in missing if not p['on_yahoo_roster']]
    
    if on_yahoo:
        print(f"\n📋 Category 1: On Yahoo Roster ({len(on_yahoo)} players)")
        print("   → These are on a Yahoo roster, so merge skipped them")
        print("   → Likely graduated from Farm but sheet not updated\n")
        for p in on_yahoo:
            manager_text = f"Manager: {p['manager']}" if p['manager'] else "Unowned"
            print(f"   • {p['name']:<30} {manager_text}")
    
    if not_on_yahoo:
        print(f"\n📋 Category 2: NOT on Yahoo Roster ({len(not_on_yahoo)} players)")
        print("   → These should have been added but weren't")
        print("   → Possible name mismatch or data issue\n")
        for p in not_on_yahoo:
            manager_text = f"Manager: {p['manager']}" if p['manager'] else "Unowned"
            print(f"   • {p['name']:<30} {manager_text}")
    
    # Two-way player check
    print("\n" + "=" * 60)
    print("\n🔄 Checking for two-way players (Batter + Pitcher versions)...")
    
    # Look for duplicate names in Yahoo
    from collections import Counter
    yahoo_counts = Counter(yahoo_names)
    duplicates = {name: count for name, count in yahoo_counts.items() if count > 1}
    
    if duplicates:
        print(f"\n   Found {len(duplicates)} players with multiple Yahoo entries:")
        for name, count in duplicates.items():
            print(f"   • {name}: {count} versions (likely two-way player)")
    else:
        print("   No two-way players detected")
    
    print("\n" + "=" * 60)
    print("\n💡 Recommendations:")
    
    if on_yahoo:
        print(f"\n1. Update Google Sheet for {len(on_yahoo)} players:")
        print("   - Change Player Type from 'Farm' to 'MLB'")
        print("   - These have graduated but sheet is outdated")
    
    if not_on_yahoo:
        print(f"\n2. Investigate {len(not_on_yahoo)} players NOT on Yahoo:")
        print("   - Check for name spelling differences")
        print("   - Verify they should actually be in combined_players.json")
    
    if duplicates:
        print(f"\n3. Handle {len(duplicates)} two-way players:")
        print("   - These create duplicate entries in Yahoo")
        print("   - Consider special handling in merge logic")
    
    print("\n✅ Diagnosis complete!")

if __name__ == "__main__":
    main()