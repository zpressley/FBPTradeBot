#!/usr/bin/env python3
"""
FBP Prospect Tracker - Age & Performance Based
Simplified system: Age 25 & under + Performance limits
NO service time tracking needed!
"""

import json
import os
import requests
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Constants
SHEET_KEY = "172eaArOcLoViepVh14sW3JLjyDGB3yfFVxVjIG9kEak"
SERVICE_TAB = "Service Days Tracker"
STATS_FILE = "data/prospect_stats.json"
FLAGGED_FILE = "data/graduation_eligible.json"
CACHE_FILE = "data/mlb_id_cache.json"
CURRENT_SEASON = 2025

# NEW SIMPLIFIED LIMITS
PROSPECT_ELIGIBILITY = {
    "max_age": 25,  # Turn 26 = no longer prospect
}

# Performance-based graduation (Age 25 & under)
GRADUATION_LIMITS = {
    "batters": {
        "plate_appearances": 350,
        "games_played": 80
    },
    "pitchers": {
        "innings_pitched": 100,
        "pitching_appearances": 30
    }
}

class SimplifiedProspectTracker:
    def __init__(self):
        self.prospects = self.load_prospects()
        self.mlb_cache = self.load_mlb_cache()
        self.stats_data = {}
        self.graduation_eligible = {}
        
    def load_prospects(self):
        """Load ALL prospects from combined players data (owned and unowned)"""
        try:
            with open("data/combined_players.json", 'r') as f:
                players = json.load(f)
            
            # Get ALL Farm players (owned and unowned)
            prospects = [p for p in players if p.get('player_type') == 'Farm']
            
            owned = [p for p in prospects if p.get('manager') and p.get('manager').strip()]
            unowned = [p for p in prospects if not p.get('manager') or not p.get('manager').strip()]
            
            print(f"📊 Loaded {len(prospects)} total Farm prospects")
            print(f"  ✅ Owned: {len(owned)}")
            print(f"  ⚪ Unowned: {len(unowned)}")
            
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
            return {}
    
    def get_player_age(self, mlb_id):
        """Get current player age from MLB API"""
        try:
            url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                people = data.get('people', [])
                if people:
                    return people[0].get('currentAge')
        except Exception as e:
            print(f"⚠️ Could not get age for MLB ID {mlb_id}: {e}")
        
        return None
    
    def get_current_stats(self, mlb_id, player_name):
        """Get current season stats"""
        try:
            stats = {
                # Batting
                'games_played': 0,
                'plate_appearances': 0,
                'at_bats': 0,
                
                # Pitching
                'pitching_games': 0,
                'pitching_appearances': 0,
                'innings_pitched': 0.0,
                
                # Meta
                'age': None,
                'last_updated': datetime.now().isoformat()
            }
            
            # Get age first
            stats['age'] = self.get_player_age(mlb_id)
            
            # Get hitting stats
            hitting_url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=season&season={CURRENT_SEASON}&group=hitting"
            hitting_response = requests.get(hitting_url, timeout=10)
            
            if hitting_response.status_code == 200:
                hitting_data = hitting_response.json()
                if 'stats' in hitting_data and hitting_data['stats']:
                    for stat_group in hitting_data['stats']:
                        if 'splits' in stat_group:
                            for split in stat_group['splits']:
                                stat = split.get('stat', {})
                                stats.update({
                                    'games_played': stat.get('gamesPlayed', 0),
                                    'plate_appearances': stat.get('plateAppearances', 0),
                                    'at_bats': stat.get('atBats', 0)
                                })
                                break
            
            # Get pitching stats
            pitching_url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=season&season={CURRENT_SEASON}&group=pitching"
            pitching_response = requests.get(pitching_url, timeout=10)
            
            if pitching_response.status_code == 200:
                pitching_data = pitching_response.json()
                if 'stats' in pitching_data and pitching_data['stats']:
                    for stat_group in pitching_data['stats']:
                        if 'splits' in stat_group:
                            for split in stat_group['splits']:
                                stat = split.get('stat', {})
                                stats.update({
                                    'pitching_games': stat.get('gamesPlayed', 0),
                                    'pitching_appearances': stat.get('appearances', 0),
                                    'innings_pitched': float(stat.get('inningsPitched', '0.0'))
                                })
                                break
            
            return stats
            
        except Exception as e:
            print(f"❌ Error fetching stats for {player_name}: {e}")
            return None
    
    def is_pitcher(self, stats):
        """Determine if player is primarily a pitcher"""
        pitching_apps = stats.get('pitching_appearances', 0)
        plate_apps = stats.get('plate_appearances', 0)
        innings = stats.get('innings_pitched', 0)
        
        if pitching_apps > 5 or innings > 10:
            return True
        if plate_apps > pitching_apps * 2:
            return False
        return pitching_apps > 0
    
    def check_eligibility(self, stats, prospect_age):
        """Check if still prospect-eligible and if should graduate"""
        
        # Use API age if available, fallback to prospect data age
        age = stats.get('age') or prospect_age
        
        # Age check - Turn 26 = automatic graduation
        if age and age >= 26:
            return {
                'is_prospect': False,
                'should_graduate': True,
                'reason': 'age',
                'details': f'Age {age} (26+ = automatic graduation)'
            }
        
        # Still age-eligible, check performance limits
        is_pitcher = self.is_pitcher(stats)
        
        if is_pitcher:
            # Pitcher: 100 IP -or- 30 Appearances
            ip_exceeded = stats.get('innings_pitched', 0) >= GRADUATION_LIMITS['pitchers']['innings_pitched']
            app_exceeded = stats.get('pitching_appearances', 0) >= GRADUATION_LIMITS['pitchers']['pitching_appearances']
            
            if ip_exceeded or app_exceeded:
                exceeded_stat = 'IP' if ip_exceeded else 'Appearances'
                value = stats.get('innings_pitched' if ip_exceeded else 'pitching_appearances')
                limit = GRADUATION_LIMITS['pitchers']['innings_pitched' if ip_exceeded else 'pitching_appearances']
                
                return {
                    'is_prospect': False,
                    'should_graduate': True,
                    'reason': 'performance',
                    'details': f'{exceeded_stat}: {value}/{limit}'
                }
        else:
            # Batter: 350 PA -or- 80 GP
            pa_exceeded = stats.get('plate_appearances', 0) >= GRADUATION_LIMITS['batters']['plate_appearances']
            gp_exceeded = stats.get('games_played', 0) >= GRADUATION_LIMITS['batters']['games_played']
            
            if pa_exceeded or gp_exceeded:
                exceeded_stat = 'PA' if pa_exceeded else 'Games'
                value = stats.get('plate_appearances' if pa_exceeded else 'games_played')
                limit = GRADUATION_LIMITS['batters']['plate_appearances' if pa_exceeded else 'games_played']
                
                return {
                    'is_prospect': False,
                    'should_graduate': True,
                    'reason': 'performance',
                    'details': f'{exceeded_stat}: {value}/{limit}'
                }
        
        # Still prospect-eligible
        return {
            'is_prospect': True,
            'should_graduate': False,
            'reason': None,
            'details': None
        }
    
    def calculate_progress(self, stats, prospect_age):
        """Calculate progress toward graduation"""
        age = stats.get('age') or prospect_age
        is_pitcher = self.is_pitcher(stats)
        
        progress = {
            'age': age,
            'age_percentage': (age / 25) * 100 if age else 0,
            'is_pitcher': is_pitcher
        }
        
        if is_pitcher:
            # Pitcher progress
            ip = stats.get('innings_pitched', 0)
            app = stats.get('pitching_appearances', 0)
            
            ip_pct = (ip / GRADUATION_LIMITS['pitchers']['innings_pitched']) * 100
            app_pct = (app / GRADUATION_LIMITS['pitchers']['pitching_appearances']) * 100
            
            progress.update({
                'primary_stat': 'IP' if ip_pct > app_pct else 'Appearances',
                'ip_current': ip,
                'ip_limit': GRADUATION_LIMITS['pitchers']['innings_pitched'],
                'ip_percentage': ip_pct,
                'app_current': app,
                'app_limit': GRADUATION_LIMITS['pitchers']['pitching_appearances'],
                'app_percentage': app_pct,
                'max_percentage': max(ip_pct, app_pct)
            })
        else:
            # Batter progress
            pa = stats.get('plate_appearances', 0)
            gp = stats.get('games_played', 0)
            
            pa_pct = (pa / GRADUATION_LIMITS['batters']['plate_appearances']) * 100
            gp_pct = (gp / GRADUATION_LIMITS['batters']['games_played']) * 100
            
            progress.update({
                'primary_stat': 'PA' if pa_pct > gp_pct else 'Games',
                'pa_current': pa,
                'pa_limit': GRADUATION_LIMITS['batters']['plate_appearances'],
                'pa_percentage': pa_pct,
                'gp_current': gp,
                'gp_limit': GRADUATION_LIMITS['batters']['games_played'],
                'gp_percentage': gp_pct,
                'max_percentage': max(pa_pct, gp_pct)
            })
        
        return progress
    
    def update_all_prospects(self):
        """Update stats for all prospects"""
        print("🔄 Updating prospect stats (Age + Performance model)...")
        print("=" * 60)
        
        updated_count = 0
        age_graduated = 0
        perf_graduated = 0
        
        for prospect in self.prospects:
            name = prospect.get('name')
            upid = prospect.get('upid')
            prospect_age = prospect.get('age')
            
            if not upid or not name:
                continue
            
            # Get MLB ID
            mlb_info = self.mlb_cache.get(str(upid))
            if not mlb_info or not mlb_info.get('mlb_id'):
                continue
            
            mlb_id = mlb_info['mlb_id']
            
            # Get stats
            stats = self.get_current_stats(mlb_id, name)
            if not stats:
                continue
            
            # Check eligibility
            eligibility = self.check_eligibility(stats, prospect_age)
            
            # Calculate progress
            progress = self.calculate_progress(stats, prospect_age)
            
            # Store data
            self.stats_data[name] = {
                **stats,
                'mlb_id': mlb_id,
                'upid': upid,
                'manager': prospect.get('manager'),
                'contract_type': prospect.get('contract_type'),
                'is_prospect': eligibility['is_prospect'],
                'should_graduate': eligibility['should_graduate'],
                'graduation_reason': eligibility['reason'],
                'progress': progress
            }
            
            # Flag for graduation
            if eligibility['should_graduate']:
                is_owned = prospect.get('manager') and prospect.get('manager').strip()
                
                self.graduation_eligible[name] = {
                    'flagged_date': datetime.now().isoformat(),
                    'manager': prospect.get('manager') or 'UNOWNED',
                    'is_owned': is_owned,
                    'reason': eligibility['reason'],
                    'details': eligibility['details'],
                    'stats': stats,
                    'progress': progress,
                    'contract_type': prospect.get('contract_type'),
                    'action_required': 'NOTIFY_MANAGER' if is_owned else 'AUTO_CONVERT_TO_MLB'
                }
                
                if eligibility['reason'] == 'age':
                    age_graduated += 1
                    action = f"({prospect.get('manager') or 'AUTO-CONVERT'})"
                    print(f"  🎂 {name}: AGE OUT - {eligibility['details']} {action}")
                else:
                    perf_graduated += 1
                    action = f"({prospect.get('manager') or 'AUTO-CONVERT'})"
                    print(f"  📊 {name}: PERFORMANCE - {eligibility['details']} {action}")
            
            updated_count += 1
            
            if updated_count % 10 == 0:
                print(f"  ✅ Processed {updated_count} prospects...")
        
        print(f"\n✅ Updated {updated_count} prospects")
        print(f"🎂 Age-based graduations: {age_graduated}")
        print(f"📊 Performance-based graduations: {perf_graduated}")
        print(f"🎓 Total graduation-eligible: {len(self.graduation_eligible)}")
        
        return updated_count
    
    def save_all_data(self):
        """Save all data files"""
        os.makedirs("data", exist_ok=True)
        
        with open(STATS_FILE, 'w') as f:
            json.dump(self.stats_data, f, indent=2)
        
        with open(FLAGGED_FILE, 'w') as f:
            json.dump(self.graduation_eligible, f, indent=2)
        
        print(f"\n💾 Data saved:")
        print(f"  - {STATS_FILE}")
        print(f"  - {FLAGGED_FILE}")
    
    def display_summary(self):
        """Display summary by manager (owned) and auto-removals (unowned)"""
        print(f"\n📋 Graduation Summary:")
        print("=" * 60)
        
        owned = {k: v for k, v in self.graduation_eligible.items() if v.get('is_owned')}
        unowned = {k: v for k, v in self.graduation_eligible.items() if not v.get('is_owned')}
        
        # Owned prospects by manager
        if owned:
            print(f"\n🔔 NOTIFY MANAGERS ({len(owned)} prospects):")
            
            by_manager = {}
            for name, data in owned.items():
                manager = data.get('manager', 'Unknown')
                if manager not in by_manager:
                    by_manager[manager] = []
                by_manager[manager].append((name, data))
            
            for manager in sorted(by_manager.keys()):
                prospects = by_manager[manager]
                print(f"\n{manager} ({len(prospects)} to graduate):")
                
                for name, data in sorted(prospects, key=lambda x: x[1]['reason']):
                    reason_icon = "🎂" if data['reason'] == 'age' else "📊"
                    contract = data.get('contract_type', 'Unknown')
                    print(f"  {reason_icon} {name}: {data['details']} - {contract} (NO REFUND)")
        
        # Unowned prospects to auto-convert
        if unowned:
            print(f"\n🔄 AUTO-CONVERT TO MLB ({len(unowned)} prospects):")
            print("These unowned prospects exceeded limits and will convert to MLB:")
            
            for name, data in sorted(unowned.items(), key=lambda x: x[1]['reason']):
                reason_icon = "🎂" if data['reason'] == 'age' else "📊"
                print(f"  {reason_icon} {name}: {data['details']}")
        
        if not owned and not unowned:
            print("\n✅ No graduations needed!")
    
    def update_google_sheets(self):
        """Update Google Sheets with progress bars"""
        print(f"\n📊 Updating Google Sheets...")
        
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
            client = gspread.authorize(creds)
            
            sheet = client.open_by_key(SHEET_KEY)
            
            try:
                worksheet = sheet.worksheet(SERVICE_TAB)
            except:
                worksheet = sheet.add_worksheet(title=SERVICE_TAB, rows=1000, cols=12)
            
            # Prepare headers
            headers = [
                "UPID", "Player Name", "Manager", "Age", "Type", "Contract",
                "Primary Stat", "Current/Limit", "Progress %", "Status", "Details", "Last Updated"
            ]
            
            # Prepare data rows
            rows = [headers]
            
            # Sort by progress percentage (highest first)
            sorted_prospects = sorted(
                self.stats_data.items(),
                key=lambda x: x[1]['progress']['max_percentage'],
                reverse=True
            )
            
            for name, data in sorted_prospects:
                progress = data['progress']
                eligibility = self.check_eligibility(data, prospect_age=None)
                
                # Determine status
                if not eligibility['is_prospect']:
                    status = "GRADUATE"
                    status_emoji = "🎓"
                elif progress['max_percentage'] >= 75:
                    status = "WARNING"
                    status_emoji = "⚠️"
                elif progress['max_percentage'] >= 50:
                    status = "CAUTION"
                    status_emoji = "🟡"
                else:
                    status = "SAFE"
                    status_emoji = "🟢"
                
                # Build row
                player_type = "Pitcher" if progress['is_pitcher'] else "Batter"
                primary_stat = progress['primary_stat']
                
                if progress['is_pitcher']:
                    if primary_stat == 'IP':
                        current = progress['ip_current']
                        limit = progress['ip_limit']
                    else:
                        current = progress['app_current']
                        limit = progress['app_limit']
                else:
                    if primary_stat == 'PA':
                        current = progress['pa_current']
                        limit = progress['pa_limit']
                    else:
                        current = progress['gp_current']
                        limit = progress['gp_limit']
                
                row = [
                    data.get('upid', ''),
                    name,
                    data.get('manager', 'UNOWNED'),
                    data.get('age', ''),
                    player_type,
                    data.get('contract_type', ''),
                    primary_stat,
                    f"{current}/{limit}",
                    f"{progress['max_percentage']:.1f}%",
                    f"{status_emoji} {status}",
                    eligibility.get('details', ''),
                    data.get('last_updated', '')[:10]
                ]
                
                rows.append(row)
            
            # Clear and update sheet
            worksheet.clear()
            worksheet.update(rows, value_input_option='USER_ENTERED')
            
            print(f"✅ Google Sheets updated with {len(rows)-1} prospects")
            
        except Exception as e:
            print(f"⚠️ Could not update Google Sheets: {e}")
    
    def auto_convert_unowned_graduates(self):
        """Convert unowned prospects that graduated to MLB player_type"""
        unowned_graduates = [
            name for name, data in self.graduation_eligible.items() 
            if not data.get('is_owned')
        ]
        
        if not unowned_graduates:
            print("\n✅ No unowned prospects to convert")
            return []
        
        print(f"\n🔄 Auto-converting {len(unowned_graduates)} unowned prospects to MLB:")
        
        # Load combined_players.json
        try:
            with open("data/combined_players.json", 'r') as f:
                all_players = json.load(f)
        except FileNotFoundError:
            print("❌ Cannot load combined_players.json")
            return []
        
        # Track conversions
        converted = []
        
        # Convert unowned graduates from Farm → MLB
        for player in all_players:
            if player.get('name') in unowned_graduates and player.get('player_type') == 'Farm':
                grad_data = self.graduation_eligible[player['name']]
                
                # Update to MLB with TC-1 contract (ready for keeper system)
                player['player_type'] = 'MLB'
                player['contract_type'] = None
                player['years_simple'] = 'TC-1'
                
                converted.append({
                    'name': player['name'],
                    'reason': grad_data['details'],
                    'age': grad_data['stats'].get('age')
                })
                
                print(f"  ✅ {player['name']}: Farm → MLB (TC-1) - {grad_data['details']}")
        
        # Save updated file
        if converted:
            # Create backup first
            backup_file = f"data/combined_players_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(backup_file, 'w') as f:
                json.dump(all_players, f, indent=2)
            print(f"\n💾 Backup saved to {backup_file}")
            
            # Save updated file
            with open("data/combined_players.json", 'w') as f:
                json.dump(all_players, f, indent=2)
            
            print(f"✅ Converted {len(converted)} unowned prospects to MLB (TC-1)")
            print(f"📝 No logging for unowned (automatic system process)")
        
        return converted
    

def main():
    print("⚾ FBP Simplified Prospect Tracker")
    print("Age-Based Eligibility + Performance Limits")
    print("=" * 60)
    print(f"\nProspect Criteria:")
    print(f"  ✅ Age 25 & under (Turn 26 = automatic graduation)")
    print(f"\nGraduation Limits (Age 25 & under):")
    print(f"  Batters: 350 PA -or- 80 GP")
    print(f"  Pitchers: 100 IP -or- 30 Appearances")
    print(f"\nUnowned Prospects:")
    print(f"  🗑️  Automatically removed when limits exceeded")
    print("=" * 60)
    
    tracker = SimplifiedProspectTracker()
    
    if not tracker.prospects:
        print("❌ No prospects found")
        return
    
    if not tracker.mlb_cache:
        print("❌ No MLB ID cache. Run build_mlb_id_cache.py first")
        return
    
    # Update all prospects
    updated = tracker.update_all_prospects()
    
    if updated == 0:
        print("❌ No stats updated")
        return
    
    # Save data
    tracker.save_all_data()
    
    # Display summary
    tracker.display_summary()
    
    # Auto-convert unowned graduates to MLB
    converted = tracker.auto_convert_unowned_graduates()
    
    # Update sheets
    tracker.update_google_sheets()
    
    print(f"\n✅ Tracking complete!")
    print(f"\n📊 Summary:")
    print(f"  Total prospects checked: {updated}")
    print(f"  Graduation-eligible: {len(tracker.graduation_eligible)}")
    print(f"  Unowned converted to MLB: {len(converted)}")
    
    print(f"\n💡 Key Benefits of New System:")
    print(f"  ✅ No service time tracking needed")
    print(f"  ✅ Age-based eligibility is clear and simple")
    print(f"  ✅ Uses Plate Appearances (more accurate than AB)")
    print(f"  ✅ Games Played alternative for late-season call-ups")
    print(f"  ✅ Pitching appearances for relievers")
    print(f"  ✅ Unowned prospects auto-convert to MLB (TC-1)")
    
    print(f"\n📋 Next Steps:")
    print(f"  1. Review graduation_eligible.json")
    print(f"  2. Notify managers of owned prospect graduations")
    print(f"  3. Managers graduate prospects (no refunds)")
    print(f"  4. Unowned prospects now MLB (TC-1) - available in keeper draft")

if __name__ == "__main__":
    main()