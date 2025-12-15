# service_time/progress_bar_sheets.py - Enhanced with FBP styling and dual progress bars

import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import requests
import time
import re

# Constants
SHEET_KEY = "172eaArOcLoViepVh14sW3JLjyDGB3yfFVxVjIG9kEak"  # FBP HUB 2.0
SERVICE_TAB = "Service Days Tracker"
STATS_FILE = "data/service_stats.json"
FLAGGED_FILE = "data/flagged_for_review.json"

# FBP Color Scheme
COLORS = {
    "dark_bg": "#2C2C2C",
    "light_bg": "#404040", 
    "orange_accent": "#FF8800",
    "red_accent": "#FF4444",
    "yellow_accent": "#FFCC00",
    "green_safe": "#00AA00",
    "white_text": "#FFFFFF",
    "light_gray": "#E0E0E0",
    "exceeded_red": "#FF0000",
    "critical_orange": "#FF4444",
    "warning_yellow": "#FF8800",
    "caution_light": "#FFCC00"
}

class EnhancedProgressBarSheetsUpdater:
    def __init__(self):
        self.stats_data = self.load_stats_data()
        self.flagged_data = self.load_flagged_data()
        self.client = self.authorize_sheets()
        self.manager_map = self.load_manager_mapping()
        self.all_prospects = self.load_all_prospects()  # Load both owned and unowned
        
    def load_all_prospects(self):
        """Load all prospects from multiple sources"""
        prospects = []
        
        # Load owned prospects from combined data
        try:
            with open("data/combined_players.json", 'r') as f:
                players = json.load(f)
            
            owned_prospects = [p for p in players 
                             if p.get('player_type') == 'Farm' 
                             and p.get('name')]
            
            for prospect in owned_prospects:
                prospects.append({
                    'name': prospect.get('name'),
                    'upid': prospect.get('upid', ''),
                    'position': prospect.get('position', ''),
                    'manager': prospect.get('manager', ''),
                    'contract_type': prospect.get('years_simple', ''),
                    'team': prospect.get('team', ''),
                    'is_owned': True
                })
            
            print(f"📊 Loaded {len(owned_prospects)} owned prospects")
            
        except FileNotFoundError:
            print("⚠️ No combined_players.json found")
        
        # Load unowned prospects from MLB rosters/transactions
        # This would require additional API calls to get all MLB prospects
        # For now, we'll focus on prospects that have MLB activity (in stats_data)
        
        # Add unowned prospects that appear in stats_data but not in owned list
        owned_names = {p['name'] for p in prospects}
        
        for name, data in self.stats_data.items():
            if name not in owned_names:
                # This is an unowned prospect with MLB activity
                prospects.append({
                    'name': name,
                    'upid': data.get('upid', ''),
                    'position': '',  # We'd need to determine this from MLB data
                    'manager': 'UNOWNED',
                    'contract_type': 'UC',  # Uncontracted
                    'team': '',
                    'is_owned': False
                })
        
        print(f"📊 Total prospects loaded: {len(prospects)}")
        return prospects
        
    def authorize_sheets(self):
        """Authorize Google Sheets access"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"❌ Google Sheets authorization failed: {e}")
            return None
        
    def load_stats_data(self):
        """Load service statistics data"""
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print("❌ No service_stats.json found. Run service tracker first.")
            return {}
    
    def load_flagged_data(self):
        """Load flagged players data"""
        try:
            with open(FLAGGED_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def load_manager_mapping(self):
        """Load manager name mappings from FBP HUB tab B2:C13"""
        try:
            sheet = self.client.open_by_key(SHEET_KEY)
            hub_sheet = sheet.worksheet("FBP HUB")
            
            # Get manager mapping from B2:C13
            manager_data = hub_sheet.get('B2:C13')
            
            # Create mapping from full name to abbreviation
            manager_map = {}
            for row in manager_data:
                if len(row) >= 2:
                    full_name = row[0].strip()
                    abbr = row[1].strip()
                    manager_map[full_name] = abbr
                    manager_map[abbr] = abbr  # Self-mapping
                    
            print(f"📋 Loaded {len(manager_map)} manager mappings")
            return manager_map
            
        except Exception as e:
            print(f"⚠️ Could not load manager mappings: {e}")
            return {
                "Hammers": "HAM", "Rick Vaughn": "RV", "Btwn2Jackies": "B2J", 
                "Country Fried Lamb": "CFL", "Law-Abiding Citizens": "LAW", 
                "La Flama Blanca": "LFB", "Jepordizers!": "JEP", "The Bluke Blokes": "TBB",
                "Whiz Kids": "WIZ", "Andromedans": "DRO", "not much of a donkey": "SAD", 
                "Weekend Warriors": "WAR"
            }
    
    def get_bbref_url_from_enhanced_cache(self, name, upid):
        """Generate Baseball Reference URL from enhanced cache"""
        # Try to get BBRef ID from enhanced cache first
        try:
            with open("data/enhanced_mlb_id_cache.json", 'r') as f:
                enhanced_cache = json.load(f)
            
            if str(upid) in enhanced_cache:
                cache_entry = enhanced_cache[str(upid)]
                bbref_id = cache_entry.get('bbref_id')
                
                if bbref_id:
                    # Use proper BBRef URL with the cached ID
                    first_letter = bbref_id[0].lower()
                    bbref_url = f"https://www.baseball-reference.com/players/{first_letter}/{bbref_id}.shtml"
                    return bbref_url, bbref_id
        except:
            pass
        
        # Fallback: search URL
        search_name = name.replace(' ', '+')
        return f"https://www.baseball-reference.com/search/search.fcgi?search={search_name}", None
    
    def calculate_dual_progress_bars(self, data):
        """Calculate both MLB and FBP progress bars"""
        career = data.get("career_stats", {})
        mlb_limits_current = data["mlb_limits_status"]
        
        # Use career totals for limit calculations
        career_ab = career.get("career_at_bats", 0)
        career_ip = career.get("career_innings", 0)
        career_apps = career.get("career_appearances", 0)
        active_days = mlb_limits_current["active_days"]["current"]
        
        # Determine if pitcher or position player
        is_pitcher = self.is_pitcher(data, career)
        
        mlb_progress = {}
        fbp_progress = {}
        
        if is_pitcher:
            # MLB limits for pitchers
            mlb_ip_pct = min(100, (career_ip / 50) * 100)  # 50 IP MLB limit
            mlb_days_pct = min(100, (active_days / 45) * 100)  # 45 days MLB limit
            mlb_max_pct = max(mlb_ip_pct, mlb_days_pct)
            
            mlb_progress = {
                "percentage": mlb_max_pct,
                "stat_name": "MLB IP" if mlb_ip_pct >= mlb_days_pct else "MLB Days",
                "current": career_ip if mlb_ip_pct >= mlb_days_pct else active_days,
                "limit": 50 if mlb_ip_pct >= mlb_days_pct else 45,
                "player_type": "Pitcher"
            }
            
            # FBP limits for pitchers
            fbp_ip_pct = min(100, (career_ip / 100) * 100)  # 100 IP FBP limit
            fbp_apps_pct = min(100, (career_apps / 30) * 100)  # 30 appearances FBP limit
            fbp_max_pct = max(fbp_ip_pct, fbp_apps_pct)
            
            fbp_progress = {
                "percentage": fbp_max_pct,
                "stat_name": "FBP IP" if fbp_ip_pct >= fbp_apps_pct else "FBP Apps",
                "current": career_ip if fbp_ip_pct >= fbp_apps_pct else career_apps,
                "limit": 100 if fbp_ip_pct >= fbp_apps_pct else 30,
                "player_type": "Pitcher"
            }
            
        else:
            # Position players (batters)
            mlb_ab_pct = min(100, (career_ab / 130) * 100)  # 130 AB MLB limit
            mlb_days_pct = min(100, (active_days / 45) * 100)  # 45 days MLB limit
            mlb_max_pct = max(mlb_ab_pct, mlb_days_pct)
            
            mlb_progress = {
                "percentage": mlb_max_pct,
                "stat_name": "MLB AB" if mlb_ab_pct >= mlb_days_pct else "MLB Days",
                "current": career_ab if mlb_ab_pct >= mlb_days_pct else active_days,
                "limit": 130 if mlb_ab_pct >= mlb_days_pct else 45,
                "player_type": "Batter"
            }
            
            # FBP limits for batters
            fbp_ab_pct = min(100, (career_ab / 300) * 100)  # 300 AB FBP limit
            fbp_progress = {
                "percentage": fbp_ab_pct,
                "stat_name": "FBP AB",
                "current": career_ab,
                "limit": 300,
                "player_type": "Batter"
            }
        
        return mlb_progress, fbp_progress
    
    def is_pitcher(self, data, career_stats):
        """Determine if player is primarily a pitcher"""
        career_ip = career_stats.get("career_innings", 0) if career_stats else 0
        career_apps = career_stats.get("career_appearances", 0) if career_stats else 0
        career_ab = career_stats.get("career_at_bats", 0) if career_stats else 0
        
        if career_ip > 5 or career_apps > 3:
            return True
        elif career_ab > 20:
            return False
        else:
            current_ip = data.get("innings_pitched", 0)
            current_apps = data.get("pitching_appearances", 0)
            return current_ip > 0 or current_apps > 0
    
    def create_styled_sparkline(self, percentage, chart_type="mlb", mlb_percentage=0, is_owned=True):
        """Create a styled sparkline chart with FBP color scheme"""
        percentage = max(0, min(100, percentage))
        remaining = 100 - percentage
        
        # For FBP charts, only show if:
        # 1. MLB limits are at 100% AND
        # 2. Player is owned (only owned prospects get FBP limits)
        if chart_type == "fbp" and (mlb_percentage < 100 or not is_owned):
            return ""  # Return empty string for blank cell
        
        # Color coding based on percentage and chart type
        if chart_type == "mlb":
            if percentage >= 100:
                color = COLORS["exceeded_red"]
            elif percentage >= 90:
                color = COLORS["critical_orange"] 
            elif percentage >= 75:
                color = COLORS["warning_yellow"]
            elif percentage >= 50:
                color = COLORS["caution_light"]
            else:
                color = COLORS["green_safe"]
        else:  # FBP limits
            if percentage >= 100:
                color = COLORS["exceeded_red"]
            elif percentage >= 90:
                color = COLORS["red_accent"]
            elif percentage >= 75:
                color = COLORS["orange_accent"]
            elif percentage >= 50:
                color = COLORS["yellow_accent"]
            else:
                color = COLORS["green_safe"]
        
        # Create sparkline with FBP styling
        sparkline_formula = f'=SPARKLINE({{{percentage};{remaining}}},{{"charttype","bar";"color1","{color}";"color2","{COLORS["light_gray"]}";"max",100}})'
        
        return sparkline_formula
    
    def get_prospect_info_from_all_data(self, player_name):
        """Get prospect info from the comprehensive prospect list"""
        for prospect in self.all_prospects:
            if prospect['name'] == player_name:
                return {
                    'upid': prospect['upid'],
                    'position': prospect['position'],
                    'contract_type': prospect['contract_type'],
                    'team': prospect['team'],
                    'manager': prospect['manager'],
                    'is_owned': prospect['is_owned']
                }
        
        # Fallback if not found
        return {
            'upid': '', 
            'position': '', 
            'contract_type': 'UC',  # Uncontracted
            'team': '',
            'manager': 'UNOWNED',
            'is_owned': False
        }
    
    def prepare_enhanced_sheet_data(self):
        """Prepare data with dual progress bars and enhanced styling"""
        if not self.stats_data:
            return []
        
        # Updated headers with dual progress bars
        headers = [
            "UPID", "Player Name", "Position", "Manager", "Player Type", "Contract", 
            "MLB Progress", "MLB Current/Limit", "FBP Progress", "FBP Current/Limit", 
            "Priority", "Career Games", "MLB Debut", "BBRef Link", "Notes", "Last Updated"
        ]
        
        rows = [headers]
        
        # Sort by FBP progress percentage first (owned prospects with FBP eligibility), then MLB progress
        def sort_key(item):
            name, data = item
            mlb_progress, fbp_progress = self.calculate_dual_progress_bars(data)
            
            # Get prospect ownership info
            prospect_info = self.get_prospect_info_from_all_data(name)
            is_owned = prospect_info.get('is_owned', False)
            
            priority_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "SAFE": 0}
            
            # Only owned prospects with MLB limits at 100% get FBP sorting priority
            fbp_eligible = is_owned and mlb_progress["percentage"] >= 100
            fbp_sort_value = fbp_progress["percentage"] if fbp_eligible else -1
            
            return (
                -fbp_sort_value,  # FBP-eligible prospects first (highest percentage)
                -mlb_progress["percentage"],  # Then by MLB percentage
                -priority_order.get(data.get("flag_priority", "SAFE"), 0)
            )
        
        sorted_players = sorted(self.stats_data.items(), key=sort_key)
        
        for name, data in sorted_players:
            mlb_progress, fbp_progress = self.calculate_dual_progress_bars(data)
            career = data.get("career_stats", {})
            mlb_id = data.get("mlb_id")
            
            # Get prospect info from comprehensive data
            prospect_info = self.get_prospect_info_from_all_data(name)
            is_owned = prospect_info.get('is_owned', False)
            
            # Get manager abbreviation - handle unowned prospects
            manager_full = prospect_info.get("manager", "")
            if manager_full == "UNOWNED":
                manager_abbr = "UNOWNED"
            else:
                manager_abbr = self.manager_map.get(manager_full, manager_full)
            
            # Create dual sparkline charts
            mlb_sparkline = self.create_styled_sparkline(mlb_progress["percentage"], "mlb")
            fbp_sparkline = self.create_styled_sparkline(fbp_progress["percentage"], "fbp", mlb_progress["percentage"], is_owned)
            
            # Get BBRef URL using enhanced cache
            prospect_upid = prospect_info.get('upid', '')
            bbref_url, bbref_id = self.get_bbref_url_from_enhanced_cache(name, prospect_upid)
            bbref_link = f'=HYPERLINK("{bbref_url}","BBRef")' if bbref_url else ""
            
            # Format current/limit displays - FBP only for owned prospects at 100% MLB
            mlb_current_limit = f"{mlb_progress['current']}/{mlb_progress['limit']}"
            fbp_current_limit = ""
            if is_owned and mlb_progress["percentage"] >= 100:
                fbp_current_limit = f"{fbp_progress['current']}/{fbp_progress['limit']}"
            
            # Get debut year instead of seasons count
            debut_year = career.get("debut_year", "")
            if debut_year and len(debut_year) == 4:  # Valid year format
                debut_display = debut_year
            else:
                debut_display = ""
            
            priority = data.get("flag_priority", "SAFE")
            
            reasons = data.get("flag_reasons", [])
            notes = reasons[0] if reasons else "Within limits"
            if len(reasons) > 1:
                notes += "..."
            
            # Extract contract status - handle unowned prospects
            contract_raw = prospect_info['contract_type']
            if contract_raw == "UC":
                contract_status = "UC"  # Uncontracted
            elif "PC" in contract_raw or "Purchased" in contract_raw:
                contract_status = "PC"
            elif "DC" in contract_raw or "Development" in contract_raw:
                contract_status = "DC"
            elif "FC" in contract_raw or "Farm" in contract_raw:
                contract_status = "FC"
            else:
                contract_status = "FC"  # Default to FC for owned prospects
            
            row = [
                prospect_info['upid'],  # UPID in column A
                name,
                prospect_info['position'],
                manager_abbr,
                mlb_progress["player_type"],
                contract_status,
                mlb_sparkline,  # MLB Progress chart
                mlb_current_limit,  # MLB Current/Limit
                fbp_sparkline,  # FBP Progress chart (blank if MLB < 100%)
                fbp_current_limit,  # FBP Current/Limit (blank if MLB < 100%)
                priority,
                career.get("career_games", 0),
                debut_display,  # MLB Debut year instead of seasons
                bbref_link,
                notes,
                data.get("last_updated", "")[:10]
            ]
            
            rows.append(row)
        
        return rows
    
    def create_enhanced_summary_stats(self):
        """Create enhanced summary statistics"""
        if not self.stats_data:
            return {}
        
        total = len(self.stats_data)
        
        # Count by FBP progress levels (since that's the primary concern)
        fbp_progress_counts = {"Exceeded": 0, "Critical": 0, "Warning": 0, "Caution": 0, "Safe": 0}
        mlb_progress_counts = {"Exceeded": 0, "Critical": 0, "Warning": 0, "Caution": 0, "Safe": 0}
        priority_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        
        for data in self.stats_data.values():
            mlb_progress, fbp_progress = self.calculate_dual_progress_bars(data)
            
            # Count FBP progress
            fbp_pct = fbp_progress["percentage"]
            if fbp_pct >= 100:
                fbp_progress_counts["Exceeded"] += 1
            elif fbp_pct >= 90:
                fbp_progress_counts["Critical"] += 1
            elif fbp_pct >= 75:
                fbp_progress_counts["Warning"] += 1
            elif fbp_pct >= 50:
                fbp_progress_counts["Caution"] += 1
            else:
                fbp_progress_counts["Safe"] += 1
            
            # Count MLB progress
            mlb_pct = mlb_progress["percentage"]
            if mlb_pct >= 100:
                mlb_progress_counts["Exceeded"] += 1
            elif mlb_pct >= 90:
                mlb_progress_counts["Critical"] += 1
            elif mlb_pct >= 75:
                mlb_progress_counts["Warning"] += 1
            elif mlb_pct >= 50:
                mlb_progress_counts["Caution"] += 1
            else:
                mlb_progress_counts["Safe"] += 1
            
            priority = data.get("flag_priority", "")
            if priority in priority_counts:
                priority_counts[priority] += 1
        
        return {
            "total": total,
            "fbp_progress": fbp_progress_counts,
            "mlb_progress": mlb_progress_counts,
            "priority": priority_counts
        }
    
    def apply_sheet_formatting(self, worksheet):
        """Apply FBP-style formatting to the worksheet"""
        try:
            # Header row formatting (row 6 - the actual headers)
            worksheet.format('A6:P6', {
                'backgroundColor': {'red': 0.17, 'green': 0.17, 'blue': 0.17},  # Dark gray
                'textFormat': {
                    'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0},  # White text
                    'fontSize': 11,
                    'bold': True
                },
                'horizontalAlignment': 'CENTER',
                'borders': {
                    'top': {'style': 'SOLID', 'width': 1},
                    'bottom': {'style': 'SOLID', 'width': 2},
                    'left': {'style': 'SOLID', 'width': 1},
                    'right': {'style': 'SOLID', 'width': 1}
                }
            })
            
            # Title formatting (row 1)
            worksheet.format('A1:P1', {
                'backgroundColor': {'red': 0.17, 'green': 0.17, 'blue': 0.17},
                'textFormat': {
                    'foregroundColor': {'red': 1.0, 'green': 0.53, 'blue': 0.0},  # Orange
                    'fontSize': 14,
                    'bold': True
                },
                'horizontalAlignment': 'CENTER'
            })
            
            # Summary rows formatting (rows 2-5)
            worksheet.format('A2:P5', {
                'backgroundColor': {'red': 0.25, 'green': 0.25, 'blue': 0.25},
                'textFormat': {
                    'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
                    'fontSize': 10
                }
            })
            
            # Alternating row colors for data
            # Light gray for even rows
            worksheet.format('A7:P1000', {
                'backgroundColor': {'red': 0.95, 'green': 0.95, 'blue': 0.95}
            })
            
            print("✅ Applied FBP-style formatting to worksheet")
            
        except Exception as e:
            print(f"⚠️ Could not apply formatting: {e}")
    
    def get_or_create_worksheet(self):
        """Get existing Service Days Tracker worksheet or create if needed"""
        if not self.client:
            return None
            
        try:
            sheet = self.client.open_by_key(SHEET_KEY)
            
            try:
                worksheet = sheet.worksheet(SERVICE_TAB)
                print(f"✅ Found existing worksheet: {SERVICE_TAB}")
                return worksheet
            except gspread.exceptions.WorksheetNotFound:
                print(f"📝 Creating new worksheet: {SERVICE_TAB}")
                worksheet = sheet.add_worksheet(title=SERVICE_TAB, rows=1000, cols=20)
                return worksheet
                
        except gspread.exceptions.APIError as e:
            if "403" in str(e):
                print(f"❌ Permission Error: Service account needs Editor access")
                return None
            else:
                print(f"❌ Sheets API Error: {e}")
                return None
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return None
    
    def update_worksheet(self):
        """Update the Google Sheets worksheet with enhanced dual progress bars"""
        worksheet = self.get_or_create_worksheet()
        if not worksheet:
            return False
        
        print("📊 Preparing enhanced sheet data with dual progress bars...")
        rows = self.prepare_enhanced_sheet_data()
        
        if not rows:
            print("❌ No data to update")
            return False
        
        try:
            # Clear existing data
            worksheet.clear()
            
            # Add enhanced summary at top (starting in column A)
            summary = self.create_enhanced_summary_stats()
            summary_rows = [
                [f"🏟️ FBP Service Days Tracker - Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                [f"📊 Total: {summary['total']} | 🚨 FBP Exceeded: {summary['fbp_progress']['Exceeded']} | ⚠️ FBP Critical (90%+): {summary['fbp_progress']['Critical']} | 🟡 FBP Warning (75%+): {summary['fbp_progress']['Warning']} | 🟢 FBP Safe (<50%): {summary['fbp_progress']['Safe']}"],
                [f"🏁 Flagged - HIGH: {summary['priority']['HIGH']} | MEDIUM: {summary['priority']['MEDIUM']} | LOW: {summary['priority']['LOW']}"],
                [""],
                [""]
            ]
            
            # Combine summary and main data
            final_rows = summary_rows + rows
            
            # Update sheet
            print(f"📝 Updating sheet with {len(rows)-1} prospect records...")
            worksheet.update(final_rows, value_input_option='USER_ENTERED')
            
            # Apply FBP-style formatting
            self.apply_sheet_formatting(worksheet)
            
            print(f"✅ Enhanced Google Sheets updated with proper FBP eligibility rules!")
            print(f"📊 Sorting priority:")
            print(f"   1. Owned prospects with FBP limits (MLB 100%+ and owned)")
            print(f"   2. All other prospects by MLB limits")
            print(f"   3. Unowned prospects show blank FBP (ineligible)")
            print(f"🔗 View at: https://docs.google.com/spreadsheets/d/{SHEET_KEY}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error updating worksheet: {e}")
            return False

def main():
    print("🎨 Enhanced FBP-Style Progress Bar Google Sheets Updater")
    print("=" * 60)
    
    updater = EnhancedProgressBarSheetsUpdater()
    
    if not updater.stats_data:
        print("❌ No service data found. Run the service tracker first:")
        print("   python3 service_time/flagged_service_tracker.py")
        return
    
    print(f"📋 Found service data for {len(updater.stats_data)} prospects")
    
    success = updater.update_worksheet()
    
    if success:
        print(f"\n🎉 Enhanced Google Sheets updated with FBP styling!")
        print(f"🔗 View: https://docs.google.com/spreadsheets/d/{SHEET_KEY}")
    else:
        print(f"\n❌ Failed to update Google Sheets")

if __name__ == "__main__":
    main()