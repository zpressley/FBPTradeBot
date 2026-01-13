# service_time/enhanced_upid_mapper.py - Complete UPID mapper with Player ID Map integration

import json
import os
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import time
from difflib import get_close_matches
import re

# Google Sheets IDs
UPID_SHEET_ID = "19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo"  # External UPID database
FBP_SHEET_ID = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"   # FBP main sheet

# Output files
CACHE_FILE = "data/enhanced_mlb_id_cache.json"
PROMOTION_FILE = "data/mlb_promotions.json"

class EnhancedUPIDMapper:
    def __init__(self):
        self.client = self.authorize_sheets()
        self.upid_database = {}
        self.player_id_map = {}  # From FBP Player ID Map sheet
        self.fbp_prospects = []
        self.mlb_id_cache = {}
        self.promotion_tracking = self.load_promotion_tracking()
        
    def authorize_sheets(self):
        """Authorize Google Sheets access"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"❌ Google Sheets authorization failed: {e}")
            return None
    
    def load_promotion_tracking(self):
        """Load existing promotion tracking data"""
        try:
            with open(PROMOTION_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def save_promotion_tracking(self):
        """Save promotion tracking data"""
        os.makedirs("data", exist_ok=True)
        with open(PROMOTION_FILE, 'w') as f:
            json.dump(self.promotion_tracking, f, indent=2)
    
    def load_upid_database(self):
        """Load external UPID database"""
        try:
            sheet = self.client.open_by_key(UPID_SHEET_ID).worksheet("PlayerUPID")
            all_data = sheet.get_all_values()
            
            if len(all_data) < 2:
                print("❌ Not enough data in UPID sheet")
                return False
            
            # Headers in row 2 (index 1), data starts from row 3 (index 2)
            headers = all_data[1]
            data_rows = all_data[2:]
            
            print(f"📋 UPID Headers: {headers[:6]}")
            print(f"📊 UPID Data rows: {len(data_rows)}")
            
            # Column indices (fixed based on sheet structure)
            name_col = 0    # Player Name
            team_col = 1    # Team
            pos_col = 2     # Position
            upid_col = 3    # UPID
            alt_name_cols = [4, 5, 6]  # Alternative names
            
            valid_count = 0
            
            for row in data_rows:
                if len(row) <= upid_col:
                    continue
                
                upid = str(row[upid_col]).strip()
                name = row[name_col].strip() if len(row) > name_col else ""
                team = row[team_col].strip() if len(row) > team_col else ""
                pos = row[pos_col].strip() if len(row) > pos_col else ""
                
                if upid and name:
                    # Collect alternative names
                    alt_names = [name]
                    for alt_col in alt_name_cols:
                        if len(row) > alt_col and row[alt_col].strip():
                            alt_names.append(row[alt_col].strip())
                    
                    self.upid_database[upid] = {
                        "name": name,
                        "team": team,
                        "position": pos,
                        "alt_names": alt_names,
                        "name_variations": [n.lower().strip() for n in alt_names]
                    }
                    valid_count += 1
            
            print(f"✅ Loaded {valid_count} players from UPID database")
            return True
            
        except Exception as e:
            print(f"❌ Error loading UPID database: {e}")
            return False
    
    def load_player_id_map(self):
        """Load Player ID Map from FBP sheet with BBRef IDs"""
        try:
            sheet = self.client.open_by_key(FBP_SHEET_ID).worksheet("Player ID Map")
            data = sheet.get_all_values()
            
            if len(data) < 2:
                print("⚠️ Player ID Map sheet is empty or has no data")
                return False
            
            headers = data[0]
            print(f"📋 Player ID Map Headers: {headers}")
            
            # Find columns
            upid_col = bbref_col = name_col = mlb_id_col = None
            
            for i, header in enumerate(headers):
                h = header.lower().strip()
                if 'upid' in h:
                    upid_col = i
                elif 'bbref' in h or 'baseball reference' in h or 'player id' in h:
                    bbref_col = i
                elif 'player name' in h or 'name' in h:
                    name_col = i
                elif 'mlb id' in h:
                    mlb_id_col = i
            
            print(f"📊 Columns - UPID: {upid_col}, BBRef: {bbref_col}, Name: {name_col}, MLB ID: {mlb_id_col}")
            
            if upid_col is None:
                print("⚠️ No UPID column found in Player ID Map")
                return False
            
            id_map = {}
            valid_count = 0
            
            for row in data[1:]:
                if len(row) <= upid_col:
                    continue
                
                upid = str(row[upid_col]).strip()
                bbref_id = str(row[bbref_col]).strip() if bbref_col and len(row) > bbref_col else ""
                name = str(row[name_col]).strip() if name_col and len(row) > name_col else ""
                mlb_id = str(row[mlb_id_col]).strip() if mlb_id_col and len(row) > mlb_id_col else ""
                
                if upid and (bbref_id or mlb_id):
                    id_map[upid] = {
                        "upid": upid,
                        "name": name,
                        "bbref_id": bbref_id if bbref_id else None,
                        "mlb_id": int(mlb_id) if mlb_id.isdigit() else None
                    }
                    valid_count += 1
            
            self.player_id_map = id_map
            print(f"✅ Loaded {valid_count} ID mappings from Player ID Map")
            return True
            
        except Exception as e:
            print(f"❌ Error loading Player ID Map: {e}")
            return False
    
    def load_fbp_prospects(self):
        """Load FBP prospects from main sheet"""
        try:
            sheet = self.client.open_by_key(FBP_SHEET_ID).worksheet("Player Data")
            records = sheet.get_all_records()
            
            prospects = []
            for row in records:
                upid = str(row.get("UPID", "")).strip()
                name = str(row.get("Player Name", "")).strip()
                years_simple = str(row.get("Years (Simple)", "")).strip()
                manager = str(row.get("Manager", "")).strip()
                
                # Include prospects (P designation) with managers
                if upid and name and years_simple == "P" and manager:
                    prospects.append({
                        "upid": upid,
                        "name": name,
                        "manager": manager,
                        "team": str(row.get("Team", "")).strip(),
                        "position": str(row.get("Pos", "")).strip()
                    })
            
            self.fbp_prospects = prospects
            print(f"✅ Loaded {len(prospects)} FBP prospects")
            return True
            
        except Exception as e:
            print(f"❌ Error loading FBP prospects: {e}")
            return False
    
    def get_mlb_id_from_api(self, player_name, team_hint="", bbref_id=None):
        """Get MLB ID using various API methods"""
        try:
            # Method 1: If we have BBRef ID, try to convert it
            if bbref_id:
                # BBRef IDs sometimes map to MLB IDs via external databases
                # For now, we'll use it as backup identifier
                pass
            
            # Method 2: Direct name search
            search_url = f"https://statsapi.mlb.com/api/v1/people/search?names={player_name}"
            response = requests.get(search_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                people = data.get("people", [])
                
                if people:
                    # If multiple matches, try to pick best one
                    best_match = people[0]
                    
                    # Prefer active players
                    for person in people:
                        if person.get("active", False):
                            best_match = person
                            break
                    
                    mlb_id = best_match.get("id")
                    full_name = best_match.get("fullName", "")
                    
                    if mlb_id:
                        print(f"   🎯 Found MLB ID via API: {full_name} → {mlb_id}")
                        return mlb_id
            
            # Method 3: Try team roster search if team provided
            if team_hint:
                team_id = self.get_team_id_from_abbr(team_hint)
                if team_id:
                    roster_url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster/active"
                    response = requests.get(roster_url, timeout=10)
                    if response.status_code == 200:
                        roster_data = response.json()
                        for player in roster_data.get("roster", []):
                            person = player.get("person", {})
                            if self.names_match(person.get("fullName", ""), player_name):
                                mlb_id = person.get("id")
                                if mlb_id:
                                    print(f"   🎯 Found MLB ID via roster: {person.get('fullName')} → {mlb_id}")
                                    return mlb_id
            
            return None
            
        except Exception as e:
            print(f"   ⚠️ API search failed for {player_name}: {e}")
            return None
    
    def get_team_id_from_abbr(self, team_abbr):
        """Convert team abbreviation to MLB team ID"""
        team_mapping = {
            "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112, "CWS": 145,
            "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC": 118,
            "LAA": 108, "LAD": 119, "MIA": 146, "MIL": 158, "MIN": 142, "NYM": 121,
            "NYY": 147, "OAK": 133, "PHI": 143, "PIT": 134, "SD": 135, "SF": 137,
            "SEA": 136, "STL": 138, "TB": 139, "TEX": 140, "TOR": 141, "WSH": 120
        }
        return team_mapping.get(team_abbr.upper())
    
    def names_match(self, name1, name2, threshold=0.8):
        """Check if two names match with fuzzy matching"""
        if not name1 or not name2:
            return False
        
        name1_clean = re.sub(r'[^a-zA-Z\s]', '', name1.lower().strip())
        name2_clean = re.sub(r'[^a-zA-Z\s]', '', name2.lower().strip())
        
        # Exact match
        if name1_clean == name2_clean:
            return True
        
        # Fuzzy match
        similarity = len(get_close_matches(name1_clean, [name2_clean], n=1, cutoff=threshold))
        return similarity > 0
    
    def generate_bbref_id(self, player_name):
        """Generate Baseball Reference ID"""
        try:
            name_parts = player_name.strip().split()
            if len(name_parts) >= 2:
                first_name = re.sub(r'[^a-zA-Z]', '', name_parts[0]).lower()
                last_name = re.sub(r'[^a-zA-Z]', '', name_parts[-1]).lower()
                
                if first_name and last_name and len(last_name) >= 2:
                    # Generate BBRef ID: last5 + first2 + 01
                    bbref_id = f"{last_name[:5]}{first_name[:2]}01"
                    return bbref_id
        except:
            pass
        return None
    
    def check_mlb_promotion_status(self, player_name, mlb_id):
        """Check if player has been promoted to MLB and track status changes"""
        if not mlb_id:
            return {"status": "unknown", "promoted": False, "promotion_date": None}
        
        try:
            # Get current roster status
            url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}?hydrate=currentTeam,stats(type=[career],group=[hitting,pitching])"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return {"status": "unknown", "promoted": False, "promotion_date": None}
            
            data = response.json()
            people = data.get("people", [])
            
            if not people:
                return {"status": "unknown", "promoted": False, "promotion_date": None}
            
            player = people[0]
            current_team = player.get("currentTeam", {})
            team_name = current_team.get("name", "")
            
            # Check if on MLB roster
            is_on_mlb_roster = bool(current_team and "Major League" in current_team.get("league", {}).get("name", ""))
            
            # Get current season stats to see if they've played
            has_mlb_stats = False
            stats = player.get("stats", [])
            for stat_group in stats:
                for split in stat_group.get("splits", []):
                    stat = split.get("stat", {})
                    if (stat.get("gamesPlayed", 0) > 0 or 
                        stat.get("atBats", 0) > 0 or 
                        stat.get("appearances", 0) > 0):
                        has_mlb_stats = True
                        break
            
            # Determine promotion status
            promoted = is_on_mlb_roster or has_mlb_stats
            
            # Track promotion status changes
            previous_status = self.promotion_tracking.get(player_name, {})
            current_status = {
                "promoted": promoted,
                "on_roster": is_on_mlb_roster,
                "has_stats": has_mlb_stats,
                "team": team_name,
                "last_checked": datetime.now().isoformat(),
                "status": "promoted" if promoted else "prospect"
            }
            
            # Detect new promotions
            if promoted and not previous_status.get("promoted", False):
                current_status["promotion_date"] = datetime.now().isoformat()
                print(f"🚀 NEW PROMOTION DETECTED: {player_name} → {team_name}")
            elif previous_status.get("promotion_date"):
                current_status["promotion_date"] = previous_status["promotion_date"]
            
            self.promotion_tracking[player_name] = current_status
            return current_status
            
        except Exception as e:
            print(f"   ⚠️ Error checking promotion status for {player_name}: {e}")
            return {"status": "unknown", "promoted": False, "promotion_date": None}
    
    def build_enhanced_cache(self):
        """Build enhanced cache with all data sources"""
        print(f"🔍 Building enhanced cache with multiple data sources...")
        
        # Load all data sources
        if not self.load_upid_database():
            print("⚠️ Failed to load UPID database")
        
        if not self.load_player_id_map():
            print("⚠️ Failed to load Player ID Map")
        
        if not self.load_fbp_prospects():
            return False
        
        print(f"🔨 Processing {len(self.fbp_prospects)} FBP prospects...")
        
        exact_matches = 0
        fuzzy_matches = 0
        api_matches = 0
        id_map_matches = 0
        promotions_detected = 0
        
        for i, prospect in enumerate(self.fbp_prospects):
            prospect_upid = prospect["upid"]
            prospect_name = prospect["name"]
            
            print(f"\n[{i+1}/{len(self.fbp_prospects)}] Processing: {prospect_name} (UPID: {prospect_upid})")
            
            mlb_id = None
            bbref_id = None
            match_method = "none"
            match_source = "none"
            
            # Method 1: Check Player ID Map first (highest priority)
            if prospect_upid in self.player_id_map:
                id_map_entry = self.player_id_map[prospect_upid]
                mlb_id = id_map_entry.get("mlb_id")
                bbref_id = id_map_entry.get("bbref_id")
                
                if mlb_id or bbref_id:
                    match_method = "id_map_direct"
                    match_source = "player_id_map"
                    id_map_matches += 1
                    print(f"   ✅ ID Map match: MLB ID={mlb_id}, BBRef ID={bbref_id}")
            
            # Method 2: UPID database match
            if not mlb_id and prospect_upid in self.upid_database:
                upid_record = self.upid_database[prospect_upid]
                print(f"   ✅ UPID exact match: {upid_record['name']}")
                
                # Try to get MLB ID via API
                mlb_id = self.get_mlb_id_from_api(
                    upid_record["name"], 
                    upid_record.get("team", ""),
                    bbref_id  # Pass existing BBRef ID if we have it
                )
                
                if mlb_id:
                    match_method = "upid_exact_api"
                    match_source = "upid_database"
                    exact_matches += 1
                else:
                    # Generate BBRef ID if no MLB ID found
                    if not bbref_id:
                        bbref_id = self.generate_bbref_id(upid_record["name"])
                    match_method = "upid_bbref_generated"
                    match_source = "upid_database"
            
            # Method 3: Fuzzy name matching against UPID database
            if not mlb_id:
                prospect_name_clean = prospect_name.lower().strip()
                best_match = None
                
                for upid, upid_record in self.upid_database.items():
                    for name_variant in upid_record["name_variations"]:
                        if self.names_match(prospect_name_clean, name_variant, 0.85):
                            best_match = upid_record
                            break
                    if best_match:
                        break
                
                if best_match:
                    print(f"   🎯 UPID fuzzy match: {best_match['name']}")
                    mlb_id = self.get_mlb_id_from_api(
                        best_match["name"],
                        best_match.get("team", ""),
                        bbref_id
                    )
                    if mlb_id:
                        match_method = "upid_fuzzy_api"
                        match_source = "upid_database"
                        fuzzy_matches += 1
                    else:
                        if not bbref_id:
                            bbref_id = self.generate_bbref_id(best_match["name"])
                        match_method = "upid_fuzzy_bbref"
                        match_source = "upid_database"
            
            # Method 4: Direct API search
            if not mlb_id:
                mlb_id = self.get_mlb_id_from_api(
                    prospect_name,
                    prospect.get("team", ""),
                    bbref_id
                )
                if mlb_id:
                    match_method = "api_direct"
                    match_source = "mlb_api"
                    api_matches += 1
                else:
                    if not bbref_id:
                        bbref_id = self.generate_bbref_id(prospect_name)
                    match_method = "bbref_generated"
                    match_source = "generated"
            
            # Check promotion status
            promotion_status = self.check_mlb_promotion_status(prospect_name, mlb_id)
            if promotion_status.get("promoted") and promotion_status.get("promotion_date"):
                promotions_detected += 1
            
            # Build cache entry
            self.mlb_id_cache[prospect_upid] = {
                "name": prospect_name,
                "mlb_id": mlb_id,
                "bbref_id": bbref_id,
                "match_method": match_method,
                "match_source": match_source,
                "manager": prospect["manager"],
                "team": prospect.get("team", ""),
                "position": prospect.get("position", ""),
                "promotion_status": promotion_status,
                "last_updated": datetime.now().isoformat()
            }
            
            # Rate limiting
            if i % 10 == 0 and i > 0:
                print(f"   💤 Rate limiting pause... ({i}/{len(self.fbp_prospects)})")
                time.sleep(2)
        
        print(f"\n📊 Enhanced Matching Results:")
        print(f"   🎯 Player ID Map matches: {id_map_matches}")
        print(f"   ✅ UPID exact matches: {exact_matches}")
        print(f"   🔍 UPID fuzzy matches: {fuzzy_matches}")
        print(f"   🌐 API direct matches: {api_matches}")
        print(f"   🚀 New promotions detected: {promotions_detected}")
        print(f"   📈 Total with MLB IDs: {len([c for c in self.mlb_id_cache.values() if c.get('mlb_id')])}")
        print(f"   🔗 Total with BBRef IDs: {len([c for c in self.mlb_id_cache.values() if c.get('bbref_id')])}")
        
        return True
    
    def save_cache(self):
        """Save enhanced cache to file"""
        os.makedirs("data", exist_ok=True)
        
        with open(CACHE_FILE, 'w') as f:
            json.dump(self.mlb_id_cache, f, indent=2)
        
        print(f"✅ Enhanced cache saved to {CACHE_FILE} with {len(self.mlb_id_cache)} entries")
        
        # Save promotion tracking
        self.save_promotion_tracking()
        print(f"✅ Promotion tracking saved to {PROMOTION_FILE}")
        
        return True
    
    def generate_promotion_report(self):
        """Generate a report of recent promotions"""
        recent_promotions = []
        
        for name, status in self.promotion_tracking.items():
            if status.get("promoted") and status.get("promotion_date"):
                promotion_date = datetime.fromisoformat(status["promotion_date"].replace("Z", "+00:00"))
                days_ago = (datetime.now() - promotion_date).days
                
                if days_ago <= 30:  # Last 30 days
                    recent_promotions.append({
                        "name": name,
                        "team": status.get("team", "Unknown"),
                        "promotion_date": status["promotion_date"][:10],
                        "days_ago": days_ago
                    })
        
        if recent_promotions:
            print(f"\n🚀 Recent Promotions (Last 30 Days):")
            for promo in sorted(recent_promotions, key=lambda x: x["days_ago"]):
                print(f"   • {promo['name']} → {promo['team']} ({promo['days_ago']} days ago)")
        else:
            print(f"\n📋 No recent promotions detected in the last 30 days")
        
        return recent_promotions

def main():
    print("🚀 Enhanced UPID Mapper with Player ID Map Integration")
    print("=" * 70)
    
    mapper = EnhancedUPIDMapper()
    
    if not mapper.client:
        print("❌ Failed to authorize Google Sheets")
        return
    
    # Build enhanced cache
    print("\n🔨 Building enhanced cache with all data sources...")
    if not mapper.build_enhanced_cache():
        return
    
    # Save cache
    print("\n💾 Saving enhanced cache...")
    if not mapper.save_cache():
        return
    
    # Generate promotion report
    print("\n📊 Generating promotion report...")
    recent_promotions = mapper.generate_promotion_report()
    
    print(f"\n✅ Enhanced UPID Mapper Complete!")
    print(f"🎯 Next Steps:")
    print(f"   1. Run: python3 service_time/enhanced_service_tracker.py")
    print(f"   2. Check promotion report for graduation candidates")
    print(f"   3. Update Discord bot with promotion alerts")

if __name__ == "__main__":
    main()