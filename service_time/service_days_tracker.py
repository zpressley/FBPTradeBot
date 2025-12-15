# service_time/service_days_tracker.py - Complete service days tracking system

import json
import os
import requests
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict

# Constants
SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
SERVICE_TAB = "Service Days Tracker"  # New tab for service tracking
SNAPSHOT_DIR = "data/roster_snapshots"
EVENTS_FILE = "data/roster_events.json"
CACHE_FILE = "data/mlb_id_cache.json"
STATS_FILE = "data/service_stats.json"

# Service limits
MLB_LIMITS = {
    "at_bats": 130,
    "innings_pitched": 50,
    "active_days": 45
}

FBP_LIMITS = {
    "at_bats": 300,
    "innings_pitched": 100,
    "pitching_appearances": 30
}

class ServiceDaysTracker:
    def __init__(self):
        self.prospects = self.load_prospects()
        self.mlb_cache = self.load_mlb_cache()
        self.stats_data = self.load_stats_data()
        
    def load_prospects(self):
        """Load prospects from combined players data"""
        try:
            with open("data/combined_players.json", 'r') as f:
                players = json.load(f)
            
            # Filter for actual prospects with managers (exclude the 1815 unassigned)
            prospects = [p for p in players 
                        if p.get('player_type') == 'Farm' 
                        and p.get('manager') 
                        and p.get('manager').strip()]
            
            print(f"📊 Loaded {len(prospects)} assigned prospects")
            return prospects
        except FileNotFoundError:
            print("❌ No combined_players.json found")
            return []
    
    def load_mlb_cache(self):
        """Load MLB ID mappings"""
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print("❌ No MLB ID cache found")
            return {}
    
    def load_stats_data(self):
        """Load existing stats data"""
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def save_stats_data(self):
        """Save stats data to file"""
        with open(STATS_FILE, 'w') as f:
            json.dump(self.stats_data, f, indent=2)
    
    def get_mlb_player_stats(self, mlb_id, season=2025):
        """Get player stats from MLB API"""
        try:
            # Get player info and stats
            url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=season&season={season}"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            # Initialize stats
            stats = {
                "at_bats": 0,
                "innings_pitched": 0.0,
                "pitching_appearances": 0,
                "last_updated": datetime.now().isoformat()
            }
            
            # Parse stats
            if 'stats' in data and data['stats']:
                for stat_group in data['stats']:
                    if 'splits' in stat_group:
                        for split in stat_group['splits']:
                            stat = split.get('stat', {})
                            
                            # Batting stats
                            if 'atBats' in stat:
                                stats["at_bats"] = stat['atBats']
                            
                            # Pitching stats  
                            if 'inningsPitched' in stat:
                                stats["innings_pitched"] = float(stat['inningsPitched'])
                            if 'appearances' in stat:
                                stats["pitching_appearances"] = stat['appearances']
            
            return stats
            
        except Exception as e:
            print(f"❌ Error fetching stats for MLB ID {mlb_id}: {e}")
            return None
    
    def calculate_active_days(self, player_name):
        """Calculate days on active MLB roster"""
        try:
            with open(EVENTS_FILE, 'r') as f:
                events = json.load(f)
        except FileNotFoundError:
            return 0
        
        if player_name not in events:
            return 0
        
        player_events = sorted(events[player_name], key=lambda x: x['date'])
        total_days = 0
        callup_date = None
        
        for event in player_events:
            event_date = datetime.strptime(event['date'], '%Y-%m-%d')
            
            if event['event'] == 'called_up':
                callup_date = event_date
            elif event['event'] == 'sent_down' and callup_date:
                days = (event_date - callup_date).days
                total_days += days
                callup_date = None
        
        # If still on roster, count up to today
        if callup_date:
            total_days += (datetime.now() - callup_date).days
        
        return total_days
    
    def update_prospect_stats(self):
        """Update stats for all prospects"""
        print("🔄 Updating prospect service statistics...")
        updated_count = 0
        
        for prospect in self.prospects:
            upid = prospect.get('upid')
            name = prospect.get('name')
            
            if not upid or not name:
                continue
            
            # Get MLB ID
            mlb_info = self.mlb_cache.get(str(upid))
            if not mlb_info or not mlb_info.get('mlb_id'):
                continue
            
            mlb_id = mlb_info['mlb_id']
            
            # Get current stats
            mlb_stats = self.get_mlb_player_stats(mlb_id)
            if not mlb_stats:
                continue
            
            # Calculate active days
            active_days = self.calculate_active_days(name)
            mlb_stats['active_days'] = active_days
            
            # Store in stats data
            self.stats_data[name] = {
                **mlb_stats,
                "mlb_id": mlb_id,
                "upid": upid,
                "manager": prospect.get('manager'),
                "mlb_limits_status": self.check_mlb_limits(mlb_stats),
                "fbp_limits_status": self.check_fbp_limits(mlb_stats)
            }
            
            updated_count += 1
            
            if updated_count % 10 == 0:
                print(f"  📊 Updated {updated_count} prospects...")
        
        self.save_stats_data()
        print(f"✅ Updated stats for {updated_count} prospects")
    
    def check_mlb_limits(self, stats):
        """Check if player has exceeded MLB limits"""
        return {
            "at_bats": {
                "current": stats["at_bats"],
                "limit": MLB_LIMITS["at_bats"],
                "exceeded": stats["at_bats"] >= MLB_LIMITS["at_bats"],
                "percentage": min(100, (stats["at_bats"] / MLB_LIMITS["at_bats"]) * 100)
            },
            "innings_pitched": {
                "current": stats["innings_pitched"],
                "limit": MLB_LIMITS["innings_pitched"],
                "exceeded": stats["innings_pitched"] >= MLB_LIMITS["innings_pitched"],
                "percentage": min(100, (stats["innings_pitched"] / MLB_LIMITS["innings_pitched"]) * 100)
            },
            "active_days": {
                "current": stats["active_days"],
                "limit": MLB_LIMITS["active_days"],
                "exceeded": stats["active_days"] >= MLB_LIMITS["active_days"],
                "percentage": min(100, (stats["active_days"] / MLB_LIMITS["active_days"]) * 100)
            }
        }
    
    def check_fbp_limits(self, stats):
        """Check if player has exceeded FBP limits"""
        return {
            "at_bats": {
                "current": stats["at_bats"],
                "limit": FBP_LIMITS["at_bats"],
                "exceeded": stats["at_bats"] >= FBP_LIMITS["at_bats"],
                "percentage": min(100, (stats["at_bats"] / FBP_LIMITS["at_bats"]) * 100)
            },
            "innings_pitched": {
                "current": stats["innings_pitched"],
                "limit": FBP_LIMITS["innings_pitched"],
                "exceeded": stats["innings_pitched"] >= FBP_LIMITS["innings_pitched"],
                "percentage": min(100, (stats["innings_pitched"] / FBP_LIMITS["innings_pitched"]) * 100)
            },
            "pitching_appearances": {
                "current": stats["pitching_appearances"],
                "limit": FBP_LIMITS["pitching_appearances"],
                "exceeded": stats["pitching_appearances"] >= FBP_LIMITS["pitching_appearances"],
                "percentage": min(100, (stats["pitching_appearances"] / FBP_LIMITS["pitching_appearances"]) * 100)
            }
        }
    
    def get_player_service_report(self, player_name):
        """Get detailed service report for a player"""
        if player_name not in self.stats_data:
            return None
        
        data = self.stats_data[player_name]
        
        report = {
            "name": player_name,
            "manager": data.get("manager"),
            "mlb_id": data.get("mlb_id"),
            "last_updated": data.get("last_updated"),
            "mlb_limits": data["mlb_limits_status"],
            "fbp_limits": data["fbp_limits_status"],
            "alerts": []
        }
        
        # Check for alerts
        mlb = data["mlb_limits_status"]
        fbp = data["fbp_limits_status"]
        
        # MLB limit alerts
        for stat_type, info in mlb.items():
            if info["percentage"] >= 90:
                report["alerts"].append(f"⚠️ MLB {stat_type.replace('_', ' ').title()}: {info['current']}/{info['limit']} ({info['percentage']:.1f}%)")
            elif info["exceeded"]:
                report["alerts"].append(f"🚨 MLB {stat_type.replace('_', ' ').title()} EXCEEDED: {info['current']}/{info['limit']}")
        
        # FBP limit alerts  
        for stat_type, info in fbp.items():
            if info["percentage"] >= 90:
                report["alerts"].append(f"⚠️ FBP {stat_type.replace('_', ' ').title()}: {info['current']}/{info['limit']} ({info['percentage']:.1f}%)")
            elif info["exceeded"]:
                report["alerts"].append(f"🚨 FBP {stat_type.replace('_', ' ').title()} EXCEEDED: {info['current']}/{info['limit']}")
        
        return report
    
    def get_team_service_summary(self, team_abbr):
        """Get service summary for a team's prospects"""
        team_prospects = []
        
        for name, data in self.stats_data.items():
            if data.get("manager") == team_abbr:
                report = self.get_player_service_report(name)
                if report:
                    team_prospects.append(report)
        
        return sorted(team_prospects, key=lambda x: len(x["alerts"]), reverse=True)
    
    def update_google_sheets(self):
        """Update Google Sheets with service days data"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            client = gspread.authorize(creds)
            
            # Open the sheet
            sheet = client.open_by_key(SHEET_KEY)
            
            # Try to get existing service tab or create new one
            try:
                worksheet = sheet.worksheet(SERVICE_TAB)
                worksheet.clear()
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sheet.add_worksheet(title=SERVICE_TAB, rows=1000, cols=20)
            
            # Prepare headers
            headers = [
                "Player Name", "Manager", "MLB ID", "Last Updated",
                "AB (Current)", "AB MLB Limit", "AB MLB %", "AB FBP Limit", "AB FBP %",
                "IP (Current)", "IP MLB Limit", "IP MLB %", "IP FBP Limit", "IP FBP %", 
                "Days (Current)", "Days MLB Limit", "Days MLB %",
                "Appearances", "App FBP Limit", "App FBP %", "Alerts"
            ]
            
            # Prepare data rows
            rows = [headers]
            
            for name, data in self.stats_data.items():
                mlb = data["mlb_limits_status"]
                fbp = data["fbp_limits_status"]
                
                row = [
                    name,
                    data.get("manager", ""),
                    data.get("mlb_id", ""),
                    data.get("last_updated", "")[:10],  # Date only
                    
                    # At Bats
                    mlb["at_bats"]["current"],
                    mlb["at_bats"]["limit"],
                    f"{mlb['at_bats']['percentage']:.1f}%",
                    fbp["at_bats"]["limit"],
                    f"{fbp['at_bats']['percentage']:.1f}%",
                    
                    # Innings Pitched
                    mlb["innings_pitched"]["current"],
                    mlb["innings_pitched"]["limit"],
                    f"{mlb['innings_pitched']['percentage']:.1f}%",
                    fbp["innings_pitched"]["limit"],
                    f"{fbp['innings_pitched']['percentage']:.1f}%",
                    
                    # Active Days
                    mlb["active_days"]["current"],
                    mlb["active_days"]["limit"],
                    f"{mlb['active_days']['percentage']:.1f}%",
                    
                    # Pitching Appearances
                    fbp["pitching_appearances"]["current"],
                    fbp["pitching_appearances"]["limit"],
                    f"{fbp['pitching_appearances']['percentage']:.1f}%",
                    
                    # Alerts
                    "; ".join(self.get_player_service_report(name)["alerts"])
                ]
                
                rows.append(row)
            
            # Update the sheet
            worksheet.update(rows, value_input_option='USER_ENTERED')
            
            print(f"✅ Updated Google Sheets with {len(rows)-1} prospect records")
            
        except Exception as e:
            print(f"❌ Error updating Google Sheets: {e}")

def main():
    print("🚀 FBP Service Days Tracker")
    print("=" * 50)
    
    tracker = ServiceDaysTracker()
    
    # Update all prospect stats
    tracker.update_prospect_stats()
    
    # Update Google Sheets
    tracker.update_google_sheets()
    
    # Show summary
    total_prospects = len(tracker.stats_data)
    alerts = sum(1 for data in tracker.stats_data.values() 
                if any(info["percentage"] >= 90 or info["exceeded"] 
                      for limits in [data["mlb_limits_status"], data["fbp_limits_status"]]
                      for info in limits.values()))
    
    print(f"\n📊 Summary:")
    print(f"  Total prospects tracked: {total_prospects}")
    print(f"  Prospects with alerts: {alerts}")
    print(f"✅ Service days tracking complete!")

if __name__ == "__main__":
    main()