# enhanced_id_mapper.py - Robust MLB ID mapper with external sources

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

# Disable SSL warnings for development
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Constants
SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
MAP_TAB = "Player ID Map"
CACHE_FILE = "data/enhanced_mlb_id_cache.json"

# Multiple external sources with fallbacks
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
    },
    {
        'name': 'Baseball Databank People',
        'url': 'https://raw.githubusercontent.com/chadwickbureau/baseballdatabank/master/core/People.csv',
        'backup_url': 'https://raw.githubusercontent.com/chadwickbureau/baseballdatabank/main/core/People.csv',
        'columns': {
            'bbref_id': 'bbrefID',
            'first_name': 'nameFirst',
            'last_name': 'nameLast',
            'birth_year': 'birthYear',
            'debut': 'debut'
        }
    }
]

class EnhancedIDMapper:
    def __init__(self):
        self.external_mappings = {}
        self.google_mappings = {}
        self.prospects = []
        self.session = requests.Session()
        self.setup_session()
        
    def setup_session(self):
        """Configure requests session with proper headers and timeouts"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
    def download_with_retry(self, url, max_retries=3, timeout=30):
        """Download with retry logic and better error handling"""
        for attempt in range(max_retries):
            try:
                print(f"      Attempt {attempt + 1}/{max_retries}: {url}")
                
                response = self.session.get(
                    url, 
                    timeout=timeout,
                    verify=False,  # Disable SSL verification for development
                    stream=True
                )
                response.raise_for_status()
                
                # Check if it's actually CSV content
                content_type = response.headers.get('content-type', '').lower()
                if 'text/csv' not in content_type and 'text/plain' not in content_type:
                    print(f"      ⚠️ Unexpected content type: {content_type}")
                
                return response.text
                
            except requests.exceptions.Timeout:
                print(f"      ⏰ Timeout on attempt {attempt + 1}")
            except requests.exceptions.ConnectionError:
                print(f"      🔌 Connection error on attempt {attempt + 1}")
            except requests.exceptions.HTTPError as e:
                print(f"      🚫 HTTP error {e.response.status_code} on attempt {attempt + 1}")
            except Exception as e:
                print(f"      ❌ Unexpected error on attempt {attempt + 1}: {e}")
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"      ⏳ Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
        
        return None

    def authorize_gsheets(self):
        """Authorize Google Sheets access"""
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
        return gspread.authorize(creds)
    
    def load_external_databases(self):
        """Load external player ID databases with robust error handling"""
        print("🔄 Loading external player ID databases...")
        
        total_loaded = 0
        
        for source in EXTERNAL_SOURCES:
            print(f"  📥 Attempting to load {source['name']}...")
            
            # Try primary URL first
            csv_content = self.download_with_retry(source['url'])
            
            # Try backup URL if primary fails
            if not csv_content and 'backup_url' in source:
                print(f"    🔄 Trying backup URL...")
                csv_content = self.download_with_retry(source['backup_url'])
            
            if csv_content:
                try:
                    # Parse CSV content
                    from io import StringIO
                    df = pd.read_csv(StringIO(csv_content), low_memory=False)
                    
                    print(f"    ✅ Loaded DataFrame with {len(df)} rows")
                    
                    # Process the data
                    source_loaded = self.process_external_source(df, source)
                    total_loaded += source_loaded
                    
                    print(f"    ✅ Processed {source_loaded} player mappings from {source['name']}")
                    
                except Exception as e:
                    print(f"    ❌ Error processing {source['name']}: {e}")
            else:
                print(f"    ❌ Failed to download {source['name']}")
        
        if total_loaded == 0:
            print("  ⚠️ No external databases loaded successfully")
            print("  🔄 Trying alternative approach with local BBRef ID generation...")
            self.generate_bbref_ids_locally()
        else:
            print(f"  ✅ Total external mappings loaded: {total_loaded}")
        
        print(f"📊 Total external mappings available: {len(self.external_mappings)}")

    def process_external_source(self, df, source_config):
        """Process a DataFrame from an external source"""
        columns = source_config['columns']
        loaded_count = 0
        
        for _, row in df.iterrows():
            try:
                # Extract names
                first_name = self.safe_get(row, columns.get('first_name'))
                last_name = self.safe_get(row, columns.get('last_name'))
                
                if not first_name or not last_name:
                    continue
                
                # Create name variations
                name_variations = self.create_name_variations(first_name, last_name)
                
                # Extract other data
                mapping_data = {
                    'mlb_id': self.safe_get(row, columns.get('mlb_id')),
                    'bbref_id': self.safe_get(row, columns.get('bbref_id')),
                    'first_name': first_name,
                    'last_name': last_name,
                    'birth_year': self.safe_get(row, columns.get('birth_year')),
                    'debut': self.safe_get(row, columns.get('debut')),
                    'source': source_config['name']
                }
                
                # Store all name variations
                for name_var in name_variations:
                    if name_var and len(name_var.strip()) > 2:
                        key = name_var.lower().strip()
                        if key not in self.external_mappings:  # Don't overwrite better matches
                            self.external_mappings[key] = mapping_data
                            loaded_count += 1
                
            except Exception as e:
                continue  # Skip problematic rows
        
        return loaded_count

    def safe_get(self, row, column_name):
        """Safely get a value from a pandas row"""
        if not column_name or column_name not in row:
            return None
        
        value = row[column_name]
        if pd.isna(value) or value == '' or str(value).strip() == '':
            return None
        
        return str(value).strip()

    def create_name_variations(self, first_name, last_name):
        """Create multiple name variations for matching"""
        variations = []
        
        if first_name and last_name:
            variations.extend([
                f"{first_name} {last_name}",
                f"{last_name}, {first_name}",
            ])
            
            # Add initial variations
            if len(first_name) > 0:
                variations.append(f"{first_name[0]}. {last_name}")
                variations.append(f"{first_name[0]} {last_name}")
        
        return variations

    def generate_bbref_ids_locally(self):
        """Generate BBRef IDs locally for prospects when external sources fail"""
        print("  🎯 Generating BBRef IDs locally for known prospects...")
        
        # Load prospects if not already loaded
        if not self.prospects:
            self.load_google_sheet_data()
        
        generated_count = 0
        
        for prospect in self.prospects:
            name = prospect['name']
            
            # Try to extract first and last name
            name_parts = name.strip().split()
            if len(name_parts) >= 2:
                first_name = name_parts[0]
                last_name = name_parts[-1]
                
                # Generate BBRef ID
                bbref_id = self.generate_bbref_id(first_name, last_name)
                
                if bbref_id:
                    mapping_data = {
                        'mlb_id': None,
                        'bbref_id': bbref_id,
                        'first_name': first_name,
                        'last_name': last_name,
                        'source': 'generated_local',
                        'generated': True
                    }
                    
                    key = name.lower().strip()
                    if key not in self.external_mappings:
                        self.external_mappings[key] = mapping_data
                        generated_count += 1

        print(f"    ✅ Generated {generated_count} BBRef IDs locally")
    
    def load_google_sheet_data(self):
        """Load prospect data from Google Sheets"""
        print("🔄 Loading prospect data from Google Sheets...")
        
        try:
            gclient = self.authorize_gsheets()
            sheet = gclient.open_by_key(SHEET_KEY).worksheet(PLAYER_TAB)
            data = sheet.get_all_values()
            headers = data[0]
            
            # Find required columns
            upid_idx = headers.index("UPID")
            name_idx = headers.index("Player Name")
            player_type_idx = headers.index("Player Type")
            
            prospects = []
            for row in data[1:]:
                if len(row) <= max(upid_idx, name_idx, player_type_idx):
                    continue
                
                player_type = row[player_type_idx].strip()
                # Only include Farm prospects (owned) and MLB players
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
            print(f"✅ Loaded {len(prospects)} prospects from Google Sheets")
            
            # Also load existing mappings
            try:
                map_sheet = gclient.open_by_key(SHEET_KEY).worksheet(MAP_TAB)
                map_rows = map_sheet.get_all_records()
                
                for row in map_rows:
                    upid = str(row.get("UPID", "")).strip()
                    name = row.get("Player Name", "").strip()
                    mlb_id = row.get("MLB ID")
                    
                    if upid and name and mlb_id:
                        self.google_mappings[upid] = {
                            "name": name,
                            "mlb_id": int(mlb_id),
                            "source": "google_sheet"
                        }
                
                print(f"✅ Loaded {len(self.google_mappings)} existing mappings from Google Sheets")
                
            except Exception as e:
                print(f"⚠️ Could not load existing mappings: {e}")
                
        except Exception as e:
            print(f"❌ Error loading Google Sheet data: {e}")

    def fuzzy_match_player(self, prospect_name):
        """Find best match for a prospect in external databases"""
        prospect_name_clean = prospect_name.lower().strip()
        
        # Try exact match first
        if prospect_name_clean in self.external_mappings:
            return self.external_mappings[prospect_name_clean]
        
        # Try fuzzy matching
        all_names = list(self.external_mappings.keys())
        matches = get_close_matches(prospect_name_clean, all_names, n=3, cutoff=0.85)
        
        if matches:
            best_match = matches[0]
            match_data = self.external_mappings[best_match].copy()
            match_data['fuzzy_match'] = True
            match_data['original_query'] = prospect_name
            match_data['matched_name'] = best_match
            return match_data
        
        return None

    def generate_bbref_id(self, first_name, last_name):
        """Generate Baseball Reference ID using their naming convention"""
        if not first_name or not last_name:
            return None
        
        # Clean names - remove accents and special characters
        first_clean = re.sub(r'[^a-zA-Z]', '', first_name.lower())
        last_clean = re.sub(r'[^a-zA-Z]', '', last_name.lower())
        
        if len(first_clean) == 0 or len(last_clean) == 0:
            return None
        
        # BBRef format: last name (up to 5 chars) + first name (up to 2 chars) + number
        last_part = last_clean[:5]
        first_part = first_clean[:2]
        
        # Default to 01
        bbref_id = f"{last_part}{first_part}01"
        
        return bbref_id
    
    def build_enhanced_cache(self):
        """Build enhanced MLB ID cache using all sources"""
        print("🚀 Building enhanced MLB ID cache...")
        
        # Load external databases first
        self.load_external_databases()
        
        if not self.prospects:
            self.load_google_sheet_data()
        
        enhanced_cache = {}
        stats = {
            'google_sheet_matches': 0,
            'external_exact_matches': 0,
            'external_fuzzy_matches': 0,
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
                'fangraphs_id': None,
                'match_source': None,
                'match_confidence': 'none'
            }
            
            # First, check existing Google Sheet mappings
            if upid in self.google_mappings:
                google_data = self.google_mappings[upid]
                cache_entry.update({
                    'mlb_id': google_data['mlb_id'],
                    'match_source': 'google_sheet',
                    'match_confidence': 'high'
                })
                stats['google_sheet_matches'] += 1
                print(f"✅ Google Sheet: {name} → MLB ID {google_data['mlb_id']}")
            
            # Then try external database matching
            else:
                match_data = self.fuzzy_match_player(name)
                
                if match_data:
                    if match_data.get('fuzzy_match'):
                        cache_entry.update({
                            'mlb_id': match_data.get('mlb_id'),
                            'bbref_id': match_data.get('bbref_id'),
                            'fangraphs_id': match_data.get('fangraphs_id'),
                            'match_source': f"{match_data['source']}_fuzzy",
                            'match_confidence': 'medium',
                            'matched_name': match_data.get('matched_name')
                        })
                        stats['external_fuzzy_matches'] += 1
                        print(f"🔍 Fuzzy: {name} → {match_data.get('matched_name')} (BBRef: {match_data.get('bbref_id')})")
                    else:
                        cache_entry.update({
                            'mlb_id': match_data.get('mlb_id'),
                            'bbref_id': match_data.get('bbref_id'),
                            'fangraphs_id': match_data.get('fangraphs_id'),
                            'match_source': match_data['source'],
                            'match_confidence': 'high'
                        })
                        stats['external_exact_matches'] += 1
                        print(f"✅ External: {name} → BBRef {match_data.get('bbref_id')}")
                
                # Generate BBRef ID if we don't have one yet
                if not cache_entry.get('bbref_id'):
                    # Try to generate one from the name
                    name_parts = name.split()
                    if len(name_parts) >= 2:
                        first_name = name_parts[0]
                        last_name = name_parts[-1]
                        generated_bbref = self.generate_bbref_id(first_name, last_name)
                        
                        if generated_bbref:
                            cache_entry['bbref_id'] = generated_bbref
                            cache_entry['bbref_generated'] = True
                            stats['generated_bbref_ids'] += 1
            
            if not cache_entry['mlb_id'] and not cache_entry['bbref_id']:
                stats['no_matches'] += 1
                print(f"❌ No match: {name}")
            
            enhanced_cache[upid] = cache_entry
        
        # Save enhanced cache
        os.makedirs("data", exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(enhanced_cache, f, indent=2)
        
        # Print statistics
        print(f"\n📊 Enhanced Cache Statistics:")
        print(f"  Total prospects processed: {len(self.prospects)}")
        print(f"  Google Sheet matches: {stats['google_sheet_matches']}")
        print(f"  External exact matches: {stats['external_exact_matches']}")
        print(f"  External fuzzy matches: {stats['external_fuzzy_matches']}")
        print(f"  Generated BBRef IDs: {stats['generated_bbref_ids']}")
        print(f"  No matches found: {stats['no_matches']}")
        
        total_with_ids = (stats['google_sheet_matches'] + stats['external_exact_matches'] + 
                         stats['external_fuzzy_matches'] + stats['generated_bbref_ids'])
        coverage = (total_with_ids / len(self.prospects)) * 100
        
        print(f"  Overall ID coverage: {coverage:.1f}%")
        print(f"✅ Enhanced cache saved to {CACHE_FILE}")
        
        return enhanced_cache

def main():
    print("🎯 Enhanced MLB ID Mapper with External Sources")
    print("=" * 60)
    
    mapper = EnhancedIDMapper()
    enhanced_cache = mapper.build_enhanced_cache()
    
    print(f"\n🎉 Enhanced MLB ID mapping complete!")
    print(f"📁 Cache saved to: {CACHE_FILE}")
    print(f"🔗 Next steps:")
    print(f"   1. Update service tracker to use enhanced cache")
    print(f"   2. Fix BBRef links with proper player IDs")
    print(f"   3. Re-run progress bar sheets updater")

if __name__ == "__main__":
    main()