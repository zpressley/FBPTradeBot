# service_time/update_sheets.py - Google Sheets updater for service days

import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# Constants
SHEET_KEY = "172eaArOcLoViepVh14sW3JLjyDGB3yfFVxVjIG9kEak"  # FBP HUB 2.0
SERVICE_TAB = "Service Days Tracker"
STATS_FILE = "data/service_stats.json"
FLAGGED_FILE = "data/flagged_for_review.json"

class SheetsUpdater:
    def __init__(self):
        self.stats_data = self.load_stats_data()
        self.flagged_data = self.load_flagged_data()
        self.client = self.authorize_sheets()
        
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
    
    def authorize_sheets(self):
        """Authorize Google Sheets access"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"❌ Google Sheets authorization failed: {e}")
            return None
    
    def get_or_create_worksheet(self):
        """Get existing Service Days Tracker worksheet or create if needed"""
        if not self.client:
            return None
            
        try:
            sheet = self.client.open_by_key(SHEET_KEY)
            
            # Try to get existing worksheet
            try:
                worksheet = sheet.worksheet(SERVICE_TAB)
                print(f"✅ Found existing worksheet: {SERVICE_TAB}")
                return worksheet
            except gspread.exceptions.WorksheetNotFound:
                # Create new worksheet
                print(f"📝 Creating new worksheet: {SERVICE_TAB}")
                worksheet = sheet.add_worksheet(title=SERVICE_TAB, rows=1000, cols=25)
                return worksheet
                
        except gspread.exceptions.APIError as e:
            if "403" in str(e):
                print(f"❌ Permission Error: Service account needs Editor access to the sheet")
                print(f"🔧 Fix: Share the sheet with fbp-bot-service@fbp-trade-tool.iam.gserviceaccount.com")
                return None
            else:
                print(f"❌ Sheets API Error: {e}")
                return None
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return None
    
    def prepare_sheet_data(self):
        """Prepare data for Google Sheets"""
        if not self.stats_data:
            return []
        
        # Headers with color coding info
        headers = [
            "Player Name", "Manager", "Priority", "Status", 
            "2025 AB", "AB %", "Career AB", 
            "2025 IP", "IP %", "Career IP",
            "2025 Apps", "Apps %", "Career Apps",
            "Active Days", "Days %",
            "Career Games", "MLB Seasons", "Debut Year",
            "Graduation Reasons", "Last Updated", "BBRef Check"
        ]
        
        rows = [headers]
        
        # Sort players: HIGH priority first, then by alert level
        sorted_players = sorted(
            self.stats_data.items(),
            key=lambda x: (
                x[1].get("flag_priority", "LOW") != "HIGH",  # HIGH priority first
                x[1].get("flag_priority", "MEDIUM") != "MEDIUM",  # Then MEDIUM
                -max([info.get("percentage", 0) for limits in [x[1]["mlb_limits_status"], x[1]["fbp_limits_status"]] for info in limits.values()])  # Then by highest %
            )
        )
        
        for name, data in sorted_players:
            mlb = data["mlb_limits_status"]
            fbp = data["fbp_limits_status"]
            career = data.get("career_stats", {})
            flagged = self.flagged_data.get(name, {})
            
            # Determine status
            priority = data.get("flag_priority", "SAFE")
            if priority == "HIGH":
                status = "🚨 REVIEW"
            elif priority == "MEDIUM":
                status = "⚠️ WATCH"
            elif any(info["exceeded"] for limits in [mlb, fbp] for info in limits.values()):
                status = "🔴 LIMIT"
            elif any(info["percentage"] >= 90 for limits in [mlb, fbp] for info in limits.values()):
                status = "🟡 CLOSE"
            else:
                status = "🟢 SAFE"
            
            # Format percentages
            ab_pct = max(mlb["at_bats"]["percentage"], fbp["at_bats"]["percentage"])
            ip_pct = max(mlb["innings_pitched"]["percentage"], fbp["innings_pitched"]["percentage"])
            apps_pct = fbp["pitching_appearances"]["percentage"]
            days_pct = mlb["active_days"]["percentage"]
            
            # Graduation reasons
            reasons = "; ".join(data.get("flag_reasons", [])[:2])  # First 2 reasons
            if len(data.get("flag_reasons", [])) > 2:
                reasons += "..."
            
            row = [
                name,
                data.get("manager", ""),
                priority,
                status,
                
                # 2025 stats
                mlb["at_bats"]["current"],
                f"{ab_pct:.0f}%",
                career.get("career_at_bats", 0),
                
                mlb["innings_pitched"]["current"],
                f"{ip_pct:.0f}%", 
                career.get("career_innings", 0),
                
                fbp["pitching_appearances"]["current"],
                f"{apps_pct:.0f}%",
                career.get("career_appearances", 0),
                
                # Service days
                mlb["active_days"]["current"],
                f"{days_pct:.0f}%",
                
                # Career info
                career.get("career_games", 0),
                career.get("seasons_played", 0),
                career.get("debut_year", ""),
                
                # Graduation info
                reasons,
                data.get("last_updated", "")[:10],  # Date only
                "Check BBRef" if priority == "HIGH" else ""
            ]
            
            rows.append(row)
        
        return rows
    
    def apply_conditional_formatting(self, worksheet):
        """Apply color coding to the worksheet"""
        try:
            # This is advanced - basic version for now
            # You could add conditional formatting rules here
            # For now, we'll rely on the status emojis in the data
            pass
        except Exception as e:
            print(f"⚠️ Could not apply formatting: {e}")
    
    def update_worksheet(self):
        """Update the Google Sheets worksheet"""
        worksheet = self.get_or_create_worksheet()
        if not worksheet:
            return False
        
        print("📊 Preparing sheet data...")
        rows = self.prepare_sheet_data()
        
        if not rows:
            print("❌ No data to update")
            return False
        
        try:
            # Clear existing data
            worksheet.clear()
            
            # Update with new data
            print(f"📝 Updating sheet with {len(rows)-1} prospect records...")
            worksheet.update(rows, value_input_option='USER_ENTERED')
            
            # Apply basic formatting
            self.apply_conditional_formatting(worksheet)
            
            print(f"✅ Successfully updated Google Sheets!")
            print(f"🔗 View at: https://docs.google.com/spreadsheets/d/{SHEET_KEY}")
            
            return True
            
        except gspread.exceptions.APIError as e:
            if "403" in str(e):
                print(f"❌ Permission Error: Cannot write to sheet")
                print(f"🔧 Fix: Ensure fbp-bot-service@fbp-trade-tool.iam.gserviceaccount.com has Editor access")
                return False
            else:
                print(f"❌ API Error: {e}")
                return False
        except Exception as e:
            print(f"❌ Error updating worksheet: {e}")
            return False
    
    def create_summary_section(self, worksheet):
        """Add summary statistics at the top"""
        try:
            # Count by priority
            priority_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "SAFE": 0}
            
            for data in self.stats_data.values():
                priority = data.get("flag_priority", "SAFE")
                if priority in priority_counts:
                    priority_counts[priority] += 1
                else:
                    priority_counts["SAFE"] += 1
            
            # Add summary in cells A1:E3 (above the main data)
            summary_data = [
                ["📊 FBP Service Days Summary", f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", "", "", ""],
                ["🚨 High Priority", "⚠️ Medium Priority", "🔶 Low Priority", "🟢 Safe", "📈 Total"],
                [priority_counts["HIGH"], priority_counts["MEDIUM"], priority_counts["LOW"], priority_counts["SAFE"], sum(priority_counts.values())]
            ]
            
            # Insert summary at top, then add main data below
            worksheet.insert_rows(summary_data, 1)
            
        except Exception as e:
            print(f"⚠️ Could not add summary section: {e}")

def main():
    print("📊 Google Sheets Service Days Updater")
    print("=" * 50)
    
    updater = SheetsUpdater()
    
    if not updater.stats_data:
        print("❌ No service data found. Run the service tracker first:")
        print("   python3 service_time/flagged_service_tracker.py")
        return
    
    print(f"📋 Found service data for {len(updater.stats_data)} prospects")
    print(f"🏁 Found {len(updater.flagged_data)} flagged for review")
    
    success = updater.update_worksheet()
    
    if success:
        print(f"\n🎉 Google Sheets updated successfully!")
        print(f"📊 Sheet contains:")
        print(f"   • Service day progress for all prospects")
        print(f"   • Priority flagging (HIGH/MEDIUM/LOW)")
        print(f"   • Career stats and graduation reasons")
        print(f"   • Status indicators (🚨⚠️🟡🟢)")
        
        print(f"\n🔗 View your sheet:")
        print(f"   https://docs.google.com/spreadsheets/d/{SHEET_KEY}")
        
        print(f"\n📋 Next Steps:")
        print(f"   1. Review HIGH priority players (🚨)")
        print(f"   2. Check Baseball Reference for veterans")
        print(f"   3. Update prospect status as needed")
    else:
        print(f"\n❌ Failed to update Google Sheets")
        print(f"💡 Check permissions and try again")

if __name__ == "__main__":
    main()