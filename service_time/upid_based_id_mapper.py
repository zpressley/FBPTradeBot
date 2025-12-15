# upid_based_id_mapper.py - Use Google Sheets UPID database as primary source

import json
import os
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import get_close_matches
import re
from datetime import datetime
import time
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Constants
UPID_SHEET_KEY = "19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo"  # UPID Database Sheet
UPID_TAB_NAME = "PlayerUPID"  # Adjust this if the tab name is different

FBP_SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"  # FBP Player Database
PLAYER_TAB = "Player Data"
MAP_TAB = "Player ID Map"
CACHE_FILE = "data/enhanced_mlb_id_cache.json"

# External database URLs (as fallback)
EXTERNAL_SOURCES = [
    {
        'name': 'Chadwick Register',
        'url': 'https://raw.githubusercontent.com/chadwickbureau/register/master/data/people.csv',
        'backup_url': 'https://raw.githubusercontent.com/chadwickbureau/register/main/data/people.csv',
        'columns': {
            'mlb_id': 'key_mlbam',
            'bbref_id': 'key_bbref', 
            'first_name': 'name_first',
            'last_name': 'name_last',
            'birth_year': 'birth_year',
            'debut': 'mlb_played_first'
        }
    }
]

class GoogleSheetsUPIDMapper:
    def __init__(self):
        self.upid_database = {}
        self.external_mappings = {}
        self.google_mappings = {}
        self.prospects = []
        self.session = requests.Session()
        self.gclient = self.authorize_gsheets()
        self.setup_session()
        
    def authorize_gsheets(self):
        """Authorize Google Sheets access"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"❌ Google Sheets authorization failed: {e}")
            return None
        
    def setup_session(self):
        """Configure requests session"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        })
    
    def load_upid_database_from_google_sheets(self):
        """Load the UPID database from Google Sheets"""
        print("🔄 Loading UPID database from Google Sheets...")
        
        if not self.gclient:
            print("❌ Google Sheets client not available")
            return False
        
        try:
            # Open the UPID database sheet
            sheet = self.gclient.open_by_key(UPID_SHEET_KEY)
            
            # Try to find the correct worksheet
            worksheet = None
            worksheet_names = [ws.title for ws in sheet.worksheets()]
            print(f"📋 Available worksheets: {worksheet_names}")
            
            # Try common names for the UPID worksheet
            possible_names = [UPID_TAB_NAME, "PlayerUPID", "Player UPID", "UPID", "Sheet1", "Data"]
            
            for name in possible_names:
                try:
                    worksheet = sheet.worksheet(name)
                    print(f"✅ Found worksheet: {name}")
                    break
                except gspread.exceptions.WorksheetNotFound:
                    continue
            
            if not worksheet:
                # Use the first worksheet as fallback
                worksheet = sheet.worksheets()[0]
                print(f"📝 Using first worksheet: {worksheet.title}")
            
            # Get all data from the worksheet
            data = worksheet.get_all_values()
            
            if not data:
                print("❌ No data found in worksheet")
                return False
            
            headers = data[0]
            print(f"📊 Headers found: {headers}")
            
            # Find the relevant columns
            upid_col = None
            name_col = None
            team_col = None
            pos_col = None
            alt_name_cols = []
            
            for i, header in enumerate(headers):
                header_lower = header.lower().strip()
                if 'upid' in header_lower:
                    upid_col = i
                elif 'player name' in header_lower or header_lower == 'name':
                    name_col = i
                elif 'team' in header_lower:
                    team_col = i
                elif 'pos' in header_lower or 'position' in header_lower:
                    pos_col = i
                elif 'alternate' in header_lower and 'name' in header_lower:
                    alt_name_cols.append(i)
            
            if upid_col is None or name_col is None:
                print(f"❌ Could not find UPID or Player Name columns")
                print(f"   UPID column: {upid_col}")
                print(f"   Name column: {name_col}")
                return False
            
            print(f"✅ Found columns - UPID: {upid_col}, Name: {name_col}, Team: {team_col}, Pos: {pos_col}")
            
            # Process the data
            upid_data = {}
            processed_count = 0
            
            for row in data[1:]:  # Skip header row
                if len(row) <= max(upid_col, name_col):
                    continue
                
                upid = row[upid_col].strip() if upid_col < len(row) else ""
                name = row[name_col].strip() if name_col < len(row) else ""
                team = row[team_col].strip() if team_col is not None and team_col < len(row) else ""
                pos = row[pos_col].strip() if pos_col is not None and pos_col < len(row) else ""
                
                if upid and name:
                    # Create name variations for matching
                    name_variations = [name.lower().strip()]
                    
                    # Add alternate names if available
                    for alt_col in alt_name_cols:
                        if alt_col < len(row):
                            alt_name = row[alt_col].strip()
                            if alt_name:
                                name_variations.append(alt_name.lower().strip())
                    
                    upid_data[upid] = {
                        'name': name,
                        'team': team,
                        'position': pos,
                        'name_variations': name_variations,
                        'upid': upid
                    }
                    processed_count += 1
            
            self.upid_database = upid_data
            print(f"✅ Loaded {processed_count} players from UPID database")
            return True
            
        except gspread.exceptions.APIError as e:
            if "403" in str(e):
                print(f"❌ Permission Error: Service account needs access to the UPID sheet")
                print(f"🔧 Please share the sheet with: fbp-bot-service@fbp-trade-tool.iam.gserviceaccount.com")
                print(f"🔗 Sheet URL: https://docs.google.com/spreadsheets/d/{UPID_SHEET_KEY}")
                return False
            else:
                print(f"❌ API Error accessing UPID sheet: {e}")
                return False
        except Exception as e:
            print(f"❌ Error loading UPID database: {e}")
            return False
    
    def load_external_databases(self):
        """Load external databases for MLB ID mapping"""
        print("🔄 Loading external player ID databases...")
        
        for source in EXTERNAL_SOURCES:
            print(f"  📥 Attempting to load {source['name']}...")
            
            csv_content = self.download_with_retry(source['url'])
            if not csv_content and 'backup_url' in source:
                csv_content = self.download_with_retry(source['backup_url'])
            
            if csv_content:
                try:
                    from io import StringIO
                    df = pd.read_csv(StringIO(csv_content), low_memory=False)
                    loaded = self.process_external_source(df, source)
                    print(f"    ✅ Loaded {loaded} mappings from {source['name']}")
                except Exception as e:
                    print(f"    ❌ Error processing {source['name']}: {e}")
            else:
                print(f"    ❌ Failed to download {source['name']}")
        
        print(f"📊 Total external mappings: {len(self.external_mappings)}")
    
    def download_with_retry(self, url, max_retries=3, timeout=30):
        """Download with retry logic"""
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=timeout, verify=False)
                response.raise_for_status()
                return response.text
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                continue
        return None
    
    def process_external_source(self, df, source_config):
        """Process external source data"""
        columns = source_config['columns']
        loaded_count = 0
        
        for _, row in df.iterrows():
            try:
                first_name = self.safe_get(row, columns.get('first_name'))
                last_name = self.safe_get(row, columns.get('last_name'))
                
                if not first_name or not last_name:
                    continue
                
                name_variations = [
                    f"{first_name} {last_name}",
                    f"{last_name}, {first_name}",
                    f"{first_name[0]}. {last_name}" if first_name else last_name,
                ]
                
                mapping_data = {
                    'mlb_id': self.safe_get(row, columns.get('mlb_id')),
                    'bbref_id': self.safe_get(row, columns.get('bbref_id')),
                    'first_name': first_name,
                    'last_name': last_name,
                    'birth_year': self.safe_get(row, columns.get('birth_year')),
                    'debut': self.safe_get(row, columns.get('debut')),
                    'source': source_config['name']
                }
                
                for name_var in name_variations:
                    if name_var and len(name_var.strip()) > 2:
                        key = name_var.lower().strip()
                        if key not in self.external_mappings:
                            self.external_mappings[key] = mapping_data
                            loaded_count += 1
                
            except Exception:
                continue
        
        return loaded_count
    
    def safe_get(self, row, column_name):
        """Safely get value from pandas row"""
        if not column_name or column_name not in row:
            return None
        value = row[column_name]
        if pd.isna(value) or value == '':
            return None
        return str(value).strip()
    
    def load_prospects_from_fbp_sheets(self):
        """Load prospects from FBP Google Sheets"""
        print("🔄 Loading prospects from FBP Google Sheets...")
        
        if not self.gclient:
            return
        
        try:
            sheet = self.gclient.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
            data = sheet.get_all_values()
            headers = data[0]
            
            upid_idx = headers.index("UPID")
            name_idx = headers.index("Player Name")
            player_type_idx = headers.index("Player Type")
            
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
            print(f"✅ Loaded {len(prospects)} prospects from FBP Google Sheets")
            
        except Exception as e:
            print(f"❌ Error loading FBP prospects: {e}")
    
    def load_existing_google_mappings(self):
        """Load existing mappings from FBP Google Sheets Player ID Map tab"""
        print("🔄 Loading existing FBP Google Sheets mappings...")
        
        if not self.gclient:
            return
        
        try:
            map_sheet = self.gclient.open_by_key(FBP_SHEET_KEY).worksheet(MAP_TAB)
            id_rows = map_sheet.get_all_records()

            google_mappings = {}
            for row in id_rows:
                upid = str(row.get("UPID", "")).strip()
                name = row.get("Player Name", "").strip()
                mlb_id = row.get("MLB ID")
                bbref_id = row.get("BBRef ID", "").strip()

                if upid and name and mlb_id:
                    google_mappings[upid] = {
                        "name": name,
                        "mlb_id": int(mlb_id) if str(mlb_id).isdigit() else mlb_id,
                        "bbref_id": bbref_id,
                        "source": "fbp_google_sheets"
                    }

            self.google_mappings = google_mappings
            print(f"✅ Loaded {len(google_mappings)} existing mappings from FBP Google Sheets")
            
        except Exception as e:
            print(f"❌ Error loading FBP Google mappings: {e}")
    
    def match_prospect_to_upid_database(self, prospect_name):
        """Match a prospect name to the UPID database"""
        prospect_name_clean = prospect_name.lower().strip()
        
        # Look for exact matches in name variations
        for upid, data in self.upid_database.items():
            if prospect_name_clean in data['name_variations']:
                return upid, data, 'exact'
        
        # Try fuzzy matching
        all_names = []
        upid_lookup = {}
        
        for upid, data in self.upid_database.items():
            for name_var in data['name_variations']:
                all_names.append(name_var)
                upid_lookup[name_var] = (upid, data)
        
        matches = get_close_matches(prospect_name_clean, all_names, n=3, cutoff=0.85)
        if matches:
            upid, data = upid_lookup[matches[0]]
            return upid, data, 'fuzzy'
        
        return None, None, 'none'
    
    def find_mlb_id_for_name(self, name, upid_data):
        """Find MLB ID for a player using external databases"""
        name_clean = name.lower().strip()
        
        # Try exact match first
        if name_clean in self.external_mappings:
            return self.external_mappings[name_clean]
        
        # Try all name variations
        for name_var in upid_data.get('name_variations', []):
            if name_var in self.external_mappings:
                return self.external_mappings[name_var]
        
        # Try fuzzy matching
        all_external_names = list(self.external_mappings.keys())
        matches = get_close_matches(name_clean, all_external_names, n=1, cutoff=0.85)
        if matches:
            match_data = self.external_mappings[matches[0]].copy()
            match_data['fuzzy_match'] = True
            match_data['matched_name'] = matches[0]
            return match_data
        
        return None
    
    def generate_bbref_id(self, first_name, last_name):
        """Generate BBRef ID using naming convention"""
        if not first_name or not last_name:
            return None
        
        first_clean = re.sub(r'[^a-zA-Z]', '', first_name.lower())
        last_clean = re.sub(r'[^a-zA-Z]', '', last_name.lower())
        
        if len(first_clean) == 0 or len(last_clean) == 0:
            return None
        
        last_part = last_clean[:5]
        first_part = first_clean[:2]
        return f"{last_part}{first_part}01"
    
    def build_enhanced_cache(self):
        """Build enhanced cache using Google Sheets UPID database as primary source"""
        print("🚀 Building enhanced cache using Google Sheets UPID database...")
        
        # Load all data sources
        if not self.load_upid_database_from_google_sheets():
            print("❌ Failed to load UPID database. Cannot continue.")
            return None
        
        self.load_external_databases()
        self.load_prospects_from_fbp_sheets()
        self.load_existing_google_mappings()
        
        enhanced_cache = {}
        stats = {
            'upid_exact_matches': 0,
            'upid_fuzzy_matches': 0,
            'fbp_google_matches': 0,
            'external_mlb_matches': 0,
            'generated_bbref_ids': 0,
            'no_matches': 0
        }
        
        for prospect in self.prospects:
            upid = prospect['upid']
            name = prospect['name']
            player_type = prospect['player_type']
            
            cache_entry = {
                'name': name,
                'player_type': player_type,
                'mlb_id': None,
                'bbref_id': None,
                'upid': upid,
                'match_source': None,
                'match_confidence': 'none'
            }
            
            # First check if we already have this in FBP Google Sheets mappings
            if upid in self.google_mappings:
                google_data = self.google_mappings[upid]
                cache_entry.update({
                    'mlb_id': google_data.get('mlb_id'),
                    'bbref_id': google_data.get('bbref_id'),
                    'match_source': 'fbp_google_sheets',
                    'match_confidence': 'high'
                })
                stats['fbp_google_matches'] += 1
                print(f"✅ FBP Google Sheets: {name} → MLB ID {google_data.get('mlb_id')}")
                
            else:
                # Try to match to UPID database
                matched_upid, upid_data, match_type = self.match_prospect_to_upid_database(name)
                
                if matched_upid:
                    # Update the UPID if we found a better match
                    if matched_upid != upid:
                        cache_entry['upid'] = matched_upid
                        cache_entry['upid_corrected'] = True
                    
                    cache_entry['upid_match_type'] = match_type
                    cache_entry['upid_position'] = upid_data.get('position', '')
                    cache_entry['upid_team'] = upid_data.get('team', '')
                    
                    if match_type == 'exact':
                        stats['upid_exact_matches'] += 1
                    else:
                        stats['upid_fuzzy_matches'] += 1
                    
                    print(f"✅ UPID {match_type}: {name} → UPID {matched_upid} ({upid_data.get('position', '')})")
                    
                    # Now try to find MLB ID using the matched name
                    mlb_data = self.find_mlb_id_for_name(upid_data['name'], upid_data)
                    
                    if mlb_data:
                        cache_entry.update({
                            'mlb_id': mlb_data.get('mlb_id'),
                            'bbref_id': mlb_data.get('bbref_id'),
                            'match_source': mlb_data.get('source', 'external'),
                            'match_confidence': 'medium' if mlb_data.get('fuzzy_match') else 'high'
                        })
                        stats['external_mlb_matches'] += 1
                        print(f"  → Found MLB ID: {mlb_data.get('mlb_id')}")
                    
                    # Generate BBRef ID if we don't have one
                    if not cache_entry.get('bbref_id'):
                        name_parts = upid_data['name'].split()
                        if len(name_parts) >= 2:
                            bbref_id = self.generate_bbref_id(name_parts[0], name_parts[-1])
                            if bbref_id:
                                cache_entry['bbref_id'] = bbref_id
                                cache_entry['bbref_generated'] = True
                                stats['generated_bbref_ids'] += 1
                
                else:
                    # No UPID match found
                    stats['no_matches'] += 1
                    print(f"❌ No UPID match: {name}")
            
            enhanced_cache[upid] = cache_entry
        
        # Save cache
        os.makedirs("data", exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(enhanced_cache, f, indent=2)
        
        # Print statistics
        print(f"\n📊 Google Sheets UPID-Based Cache Statistics:")
        print(f"  Total prospects processed: {len(self.prospects)}")
        print(f"  FBP Google Sheets matches: {stats['fbp_google_matches']}")
        print(f"  UPID exact matches: {stats['upid_exact_matches']}")
        print(f"  UPID fuzzy matches: {stats['upid_fuzzy_matches']}")
        print(f"  External MLB ID matches: {stats['external_mlb_matches']}")
        print(f"  Generated BBRef IDs: {stats['generated_bbref_ids']}")
        print(f"  No matches found: {stats['no_matches']}")
        
        total_upid_matches = stats['upid_exact_matches'] + stats['upid_fuzzy_matches']
        total_mlb_matches = stats['fbp_google_matches'] + stats['external_mlb_matches']
        
        upid_coverage = (total_upid_matches / len(self.prospects)) * 100 if self.prospects else 0
        mlb_coverage = (total_mlb_matches / len(self.prospects)) * 100 if self.prospects else 0
        
        print(f"  UPID match coverage: {upid_coverage:.1f}%")
        print(f"  MLB ID coverage: {mlb_coverage:.1f}%")
        print(f"✅ Enhanced cache saved to {CACHE_FILE}")
        
        return enhanced_cache

def main():
    print("🎯 Google Sheets UPID-Based Enhanced MLB ID Mapper")
    print("=" * 60)
    
    mapper = GoogleSheetsUPIDMapper()
    enhanced_cache = mapper.build_enhanced_cache()
    
    if enhanced_cache:
        print(f"\n🎉 UPID-based ID mapping complete!")
        print(f"📁 Cache saved to: {CACHE_FILE}")
        print(f"🔗 Key improvements:")
        print(f"   • Uses Google Sheets UPID database as primary source")
        print(f"   • Corrects wrong MLB ID mappings from FBP sheets")
        print(f"   • Provides proper UPID → MLB ID → BBRef ID mapping")
        print(f"   • Fixes issues like Juan Mateo → Tony Zych")
        
        print(f"\n📋 Next steps:")
        print(f"   1. Run service tracker with corrected IDs")
        print(f"   2. Update progress bar sheets")
        print(f"   3. Verify player position classifications")
    else:
        print(f"\n❌ ID mapping failed")
        print(f"💡 Make sure the service account has access to the UPID sheet")

if __name__ == "__main__":
    main()