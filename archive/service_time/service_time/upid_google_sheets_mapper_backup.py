# Complete working UPID mapper - save as upid_google_sheets_mapper.py

import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import get_close_matches
import re

# Constants
UPID_SHEET_KEY = "19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo"
UPID_TAB_NAME = "PlayerUPID"
FBP_SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
CACHE_FILE = "data/enhanced_mlb_id_cache.json"

class GoogleSheetsUPIDMapper:
    def __init__(self):
        self.upid_database = {}
        self.prospects = []
        self.gclient = self.authorize_gsheets()
        
    def authorize_gsheets(self):
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"❌ Google Sheets authorization failed: {e}")
            return None
    
    def load_upid_database_from_google_sheets(self):
        print("🔄 Loading UPID database from Google Sheets...")
        
        if not self.gclient:
            return False
        
        try:
            sheet = self.gclient.open_by_key(UPID_SHEET_KEY)
            worksheet = sheet.worksheet(UPID_TAB_NAME)
            data = worksheet.get_all_values()
            
            # Headers in row 2 (index 1)
            headers = data[1]
            data_rows = data[2:]
            
            print(f"📊 Headers: {headers[:8]}")
            print(f"📊 Data rows: {len(data_rows)}")
            
            # Find columns
            upid_col = name_col = team_col = pos_col = None
            alt_name_cols = []
            
            for i, header in enumerate(headers):
                h = str(header).lower().strip()
                if 'upid' in h:
                    upid_col = i
                elif 'player name' in h:
                    name_col = i
                elif 'team' in h:
                    team_col = i
                elif 'pos' in h:
                    pos_col = i
                elif 'alternate name' in h:
                    alt_name_cols.append(i)
            
            print(f"✅ Columns - UPID: {upid_col}, Name: {name_col}")
            
            if upid_col is None or name_col is None:
                return False
            
            # Process data
            upid_data = {}
            for row in data_rows:
                if len(row) <= max(upid_col, name_col):
                    continue
                
                upid = str(row[upid_col]).strip()
                name = str(row[name_col]).strip()
                
                if upid and name and len(name) > 2:
                    name_variations = [name.lower().strip()]
                    
                    # Add alternate names
                    for alt_col in alt_name_cols:
                        if alt_col < len(row) and row[alt_col].strip():
                            name_variations.append(row[alt_col].lower().strip())
                    
                    upid_data[upid] = {
                        'name': name,
                        'team': row[team_col] if team_col and team_col < len(row) else "",
                        'position': row[pos_col] if pos_col and pos_col < len(row) else "",
                        'name_variations': name_variations,
                        'upid': upid
                    }
            
            self.upid_database = upid_data
            print(f"✅ Loaded {len(upid_data)} players from UPID database")
            return True
            
        except Exception as e:
            print(f"❌ Error loading UPID database: {e}")
            return False
    
    def load_prospects_from_fbp_sheets(self):
        print("🔄 Loading prospects from FBP Google Sheets...")
        
        if not self.gclient:
            return
        
        try:
            sheet = self.gclient.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
            data = sheet.get_all_values()
            headers = data[0]
            
            # Use column 3 for UPID, find others
            upid_idx = 3
            name_idx = player_type_idx = None
            
            for i, header in enumerate(headers):
                h = header.lower().strip()
                if 'player name' in h:
                    name_idx = i
                elif 'player type' in h:
                    player_type_idx = i
            
            print(f"📊 Using columns - UPID: {upid_idx}, Name: {name_idx}, Type: {player_type_idx}")
            
            if name_idx is None or player_type_idx is None:
                print("❌ Could not find required columns")
                return
            
            prospects = []
            for row in data[1:]:
                if len(row) <= max(upid_idx, name_idx, player_type_idx):
                    continue
                
                player_type = row[player_type_idx].strip()
                if player_type in ["Farm", "MLB"]:
                    upid = str(row[upid_idx]).strip()
                    name = row[name_idx].strip()
                    
                    if upid and name:
                        prospects.append({
                            "upid": upid,
                            "name": name,
                            "player_type": player_type
                        })
            
            self.prospects = prospects
            print(f"✅ Loaded {len(prospects)} prospects from FBP sheets")
            
        except Exception as e:
            print(f"❌ Error loading FBP prospects: {e}")
    
    def match_prospect_to_upid_database(self, prospect_name):
        prospect_name_clean = prospect_name.lower().strip()
        
        # Exact match
        for upid, data in self.upid_database.items():
            if prospect_name_clean in data['name_variations']:
                return upid, data, 'exact'
        
        # Fuzzy match
        all_names = []
        upid_lookup = {}
        
        for upid, data in self.upid_database.items():
            for name_var in data['name_variations']:
                all_names.append(name_var)
                upid_lookup[name_var] = (upid, data)
        
        matches = get_close_matches(prospect_name_clean, all_names, n=1, cutoff=0.85)
        if matches:
            upid, data = upid_lookup[matches[0]]
            return upid, data, 'fuzzy'
        
        return None, None, 'none'
    
    def generate_bbref_id(self, first_name, last_name):
        if not first_name or not last_name:
            return None
        
        first_clean = re.sub(r'[^a-zA-Z]', '', first_name.lower())
        last_clean = re.sub(r'[^a-zA-Z]', '', last_name.lower())
        
        if len(first_clean) == 0 or len(last_clean) == 0:
            return None
        
        return f"{last_clean[:5]}{first_clean[:2]}01"
    
    def build_enhanced_cache(self):
        print("🚀 Building enhanced cache...")
        
        if not self.load_upid_database_from_google_sheets():
            return None
        
        self.load_prospects_from_fbp_sheets()
        
        enhanced_cache = {}
        stats = {'exact': 0, 'fuzzy': 0, 'none': 0}
        
        for prospect in self.prospects:
            upid = prospect['upid']
            name = prospect['name']
            
            cache_entry = {
                'name': name,
                'player_type': prospect['player_type'],
                'mlb_id': None,
                'bbref_id': None,
                'upid': upid,
                'match_source': 'upid_database'
            }
            
            # Match to UPID database
            matched_upid, upid_data, match_type = self.match_prospect_to_upid_database(name)
            
            if matched_upid:
                if matched_upid != upid:
                    cache_entry['upid'] = matched_upid
                    cache_entry['upid_corrected'] = True
                
                cache_entry['upid_position'] = upid_data.get('position', '')
                cache_entry['upid_team'] = upid_data.get('team', '')
                
                # Generate BBRef ID
                name_parts = upid_data['name'].split()
                if len(name_parts) >= 2:
                    bbref_id = self.generate_bbref_id(name_parts[0], name_parts[-1])
                    if bbref_id:
                        cache_entry['bbref_id'] = bbref_id
                
                stats[match_type] += 1
                print(f"✅ {match_type}: {name} → UPID {matched_upid}")
            else:
                stats['none'] += 1
                print(f"❌ No match: {name}")
            
            enhanced_cache[upid] = cache_entry
        
        # Save cache
        os.makedirs("data", exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(enhanced_cache, f, indent=2)
        
        print(f"\n📊 Results:")
        print(f"  Exact matches: {stats['exact']}")
        print(f"  Fuzzy matches: {stats['fuzzy']}")
        print(f"  No matches: {stats['none']}")
        print(f"✅ Cache saved to {CACHE_FILE}")
        
        return enhanced_cache

def main():
    print("🎯 Google Sheets UPID-Based Enhanced MLB ID Mapper")
    print("=" * 60)
    
    mapper = GoogleSheetsUPIDMapper()
    enhanced_cache = mapper.build_enhanced_cache()
    
    if enhanced_cache:
        print(f"\n🎉 Success! Next: python3 service_time/flagged_service_tracker.py")
    else:
        print(f"\n❌ Failed")

if __name__ == "__main__":
    main()