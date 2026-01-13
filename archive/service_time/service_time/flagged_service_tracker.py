# flagged_service_tracker.py - Updated to use enhanced MLB ID cache

import json
import os
import requests
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Constants
SHEET_KEY = "172eaArOcLoViepVh14sW3JLjyDGB3yfFVxVjIG9kEak"  # FBP HUB 2.0
SERVICE_TAB = "Service Days Tracker"
STATS_FILE = "data/service_stats.json"
FLAGGED_FILE = "data/flagged_for_review.json"
ENHANCED_CACHE_FILE = "data/enhanced_mlb_id_cache.json"  # Updated cache file

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

# Thresholds for flagging manual review
FLAG_THRESHOLDS = {
    "high_games_played": 100,  # 100+ games suggests veteran status
    "multiple_seasons": 2,     # Appeared in 2+ MLB seasons
    "high_at_bats": 200,       # Well beyond rookie limits
    "high_innings": 75,        # Well beyond rookie IP limits
    "veteran_appearances": 50   # Many appearances suggests veteran
}

class FlaggedServiceTracker:
    def __init__(self):
        self.prospects = self.load_prospects()
        self.enhanced_cache = self.load_enhanced_cache()  # Updated to use enhanced cache
        self.stats_data = self.load_stats_data()
        self.flagged_players = self.load_flagged_players()
        
    def load_prospects(self):
        """Load prospects from combined players data"""
        try:
            with open("data/combined_players.json", 'r') as f:
                players = json.load(f)
            
            prospects = [p for p in players 
                        if p.get('player_type') == 'Farm' 
                        and p.get('manager') 
                        and p.get('manager').strip()]
            
            print(f"📊 Loaded {len(prospects)} assigned prospects")
            return prospects
        except FileNotFoundError:
            print("❌ No combined_players.json found")
            return []
    
    def load_enhanced_cache(self):
        """Load enhanced MLB ID mappings"""
        try:
            with open(ENHANCED_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            print(f"📊 Loaded {len(cache)} enhanced ID mappings")
            return cache
        except FileNotFoundError:
            print("❌ Enhanced cache not found. Run enhanced_id_mapper.py first")
            return {}
    
    def load_stats_data(self):
        """Load existing stats data"""
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def load_flagged_players(self):
        """Load flagged players for manual review"""
        try:
            with open(FLAGGED_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def save_stats_data(self):
        """Save stats data to file"""
        with open(STATS_FILE, 'w') as f:
            json.dump(self.stats_data, f, indent=2)
    
    def save_flagged_players(self):
        """Save flagged players list"""
        with open(FLAGGED_FILE, 'w') as f:
            json.dump(self.flagged_players, f, indent=2)
    
    def get_mlb_id_from_enhanced_cache(self, prospect):
        """Get MLB ID from enhanced cache using UPID"""
        upid = str(prospect.get('upid', ''))
        if upid in self.enhanced_cache:
            cache_entry = self.enhanced_cache[upid]
            return cache_entry.get('mlb_id'), cache_entry.get('bbref_id')
        return None, None
    
    def get_career_stats(self, mlb_id):
        """Get career statistics to help determine veteran status"""
        try:
            # Get career stats
            url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=career&group=hitting,pitching"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            career_stats = {
                "career_games": 0,
                "career_at_bats": 0,
                "career_innings": 0.0,
                "career_appearances": 0,
                "seasons_played": 0,
                "debut_year": None
            }
            
            if 'stats' in data and data['stats']:
                for stat_group in data['stats']:
                    if 'splits' in stat_group:
                        for split in stat_group['splits']:
                            stat = split.get('stat', {})
                            
                            # Hitting career stats
                            if 'gamesPlayed' in stat:
                                career_stats["career_games"] = stat['gamesPlayed']
                            if 'atBats' in stat:
                                career_stats["career_at_bats"] = stat['atBats']
                            
                            # Pitching career stats
                            if 'inningsPitched' in stat:
                                career_stats["career_innings"] = float(stat['inningsPitched'])
                            if 'appearances' in stat:
                                career_stats["career_appearances"] = stat['appearances']
            
            # Try to get season-by-season data to count seasons
            seasons_url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=yearByYear&group=hitting,pitching"
            seasons_response = requests.get(seasons_url, timeout=10)
            
            if seasons_response.status_code == 200:
                seasons_data = seasons_response.json()
                mlb_seasons = set()
                
                if 'stats' in seasons_data and seasons_data['stats']:
                    for stat_group in seasons_data['stats']:
                        if 'splits' in stat_group:
                            for split in stat_group['splits']:
                                season = split.get('season')
                                league = split.get('league', {}).get('name', '')
                                
                                # Only count MLB seasons
                                if season and 'Major League' in league:
                                    mlb_seasons.add(season)
                                    if not career_stats["debut_year"] or season < career_stats["debut_year"]:
                                        career_stats["debut_year"] = season
                
                career_stats["seasons_played"] = len(mlb_seasons)
            
            return career_stats
            
        except Exception as e:
            print(f"⚠️ Could not get career stats for MLB ID {mlb_id}: {e}")
            return None
    
    def get_current_season_stats(self, mlb_id, season=2025):
        """Get current season stats"""
        try:
            url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats?stats=season&season={season}"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            stats = {
                "at_bats": 0,
                "innings_pitched": 0.0,
                "pitching_appearances": 0,
                "games_played": 0,
                "last_updated": datetime.now().isoformat()
            }
            
            if 'stats' in data and data['stats']:
                for stat_group in data['stats']:
                    if 'splits' in stat_group:
                        for split in stat_group['splits']:
                            stat = split.get('stat', {})
                            
                            if 'atBats' in stat:
                                stats["at_bats"] = stat['atBats']
                            if 'gamesPlayed' in stat:
                                stats["games_played"] = stat['gamesPlayed']
                            if 'inningsPitched' in stat:
                                stats["innings_pitched"] = float(stat['inningsPitched'])
                            if 'appearances' in stat:
                                stats["pitching_appearances"] = stat['appearances']
            
            return stats
            
        except Exception as e:
            print(f"❌ Error fetching current stats for MLB ID {mlb_id}: {e}")
            return None
    
    def calculate_active_days(self, player_name):
        """Calculate days on active MLB roster"""
        try:
            with open("data/roster_events.json", 'r') as f:
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
        
        if callup_date:
            total_days += (datetime.now() - callup_date).days
        
        return total_days
    
    def check_flag_criteria(self, player_name, current_stats, career_stats):
        """Check if player should be flagged for manual review"""
        flags = []
        priority = "LOW"
        
        if not career_stats:
            return [], priority
        
        # High games played suggests veteran status
        if career_stats["career_games"] >= FLAG_THRESHOLDS["high_games_played"]:
            flags.append(f"Career games: {career_stats['career_games']} (veteran level)")
            priority = "HIGH"
        
        # Multiple seasons in MLB
        if career_stats["seasons_played"] >= FLAG_THRESHOLDS["multiple_seasons"]:
            flags.append(f"MLB seasons: {career_stats['seasons_played']} (multi-year veteran)")
            priority = "HIGH"
        
        # Well beyond rookie limits
        if career_stats["career_at_bats"] >= FLAG_THRESHOLDS["high_at_bats"]:
            flags.append(f"Career ABs: {career_stats['career_at_bats']} (well beyond rookie limits)")
            priority = "HIGH"
        
        if career_stats["career_innings"] >= FLAG_THRESHOLDS["high_innings"]:
            flags.append(f"Career IP: {career_stats['career_innings']} (well beyond rookie limits)")
            priority = "HIGH"
        
        if career_stats["career_appearances"] >= FLAG_THRESHOLDS["veteran_appearances"]:
            flags.append(f"Career appearances: {career_stats['career_appearances']} (veteran level)")
            priority = "HIGH"
        
        # Debut year suggests they've been around
        if career_stats["debut_year"] and career_stats["debut_year"] <= "2022":
            flags.append(f"MLB debut: {career_stats['debut_year']} (3+ years ago)")
            if priority != "HIGH":
                priority = "MEDIUM"
        
        # Current season exceeding limits
        mlb_limits = self.check_mlb_limits({**current_stats, "active_days": 0})  # Use 0 for active days
        fbp_limits = self.check_fbp_limits(current_stats)
        
        for stat_type, info in mlb_limits.items():
            if info["exceeded"]:
                flags.append(f"2025 {stat_type.replace('_', ' ')}: {info['current']}/{info['limit']} (MLB limit exceeded)")
                if priority == "LOW":
                    priority = "MEDIUM"
        
        for stat_type, info in fbp_limits.items():
            if info["exceeded"]:
                flags.append(f"2025 {stat_type.replace('_', ' ')}: {info['current']}/{info['limit']} (FBP limit exceeded)")
                priority = "HIGH"
        
        return flags, priority
    
    def check_mlb_limits(self, stats):
        """Check against MLB rookie limits"""
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
        """Check against FBP rookie limits"""
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
    
    def update_prospect_stats(self):
        """Update stats and flag players for manual review"""
        print("🔄 Updating prospect service statistics with flagging...")
        updated_count = 0
        flagged_count = 0
        
        for prospect in self.prospects:
            upid = prospect.get('upid')
            name = prospect.get('name')
            
            if not upid or not name:
                continue
            
            # Get MLB ID from enhanced cache
            mlb_id, bbref_id = self.get_mlb_id_from_enhanced_cache(prospect)
            if not mlb_id:
                # Skip prospects without MLB IDs - they're still in minors
                continue
            
            # Get current season stats
            current_stats = self.get_current_season_stats(mlb_id)
            if not current_stats:
                continue
            
            # Get career stats for flagging
            career_stats = self.get_career_stats(mlb_id)
            
            # Calculate active days
            active_days = self.calculate_active_days(name)
            current_stats['active_days'] = active_days
            
            # Check flag criteria
            flags, priority = self.check_flag_criteria(name, current_stats, career_stats)
            
            # Store in stats data
            self.stats_data[name] = {
                **current_stats,
                "career_stats": career_stats,
                "mlb_id": mlb_id,
                "bbref_id": bbref_id,  # Include BBRef ID
                "upid": upid,
                "manager": prospect.get('manager'),
                "mlb_limits_status": self.check_mlb_limits(current_stats),
                "fbp_limits_status": self.check_fbp_limits(current_stats),
                "flag_priority": priority,
                "flag_reasons": flags
            }
            
            # Flag for manual review if criteria met
            if flags:
                # Generate proper BBRef URL using the BBRef ID from cache
                bbref_url = f"https://www.baseball-reference.com/players/{bbref_id[0]}/{bbref_id}.shtml" if bbref_id else f"https://www.baseball-reference.com/register/player.fcgi?id={mlb_id}"
                
                self.flagged_players[name] = {
                    "flagged_date": datetime.now().isoformat(),
                    "priority": priority,
                    "reasons": flags,
                    "manager": prospect.get('manager'),
                    "current_stats": current_stats,
                    "career_stats": career_stats,
                    "bbref_url": bbref_url
                }
                flagged_count += 1
                
                priority_emoji = "🚨" if priority == "HIGH" else "⚠️" if priority == "MEDIUM" else "🔶"
                print(f"{priority_emoji} {name} flagged ({priority}): {flags[0] if flags else 'Multiple reasons'}")
            
            updated_count += 1
            
            if updated_count % 10 == 0:
                print(f"  📊 Updated {updated_count} prospects...")
        
        self.save_stats_data()
        self.save_flagged_players()
        
        print(f"✅ Updated stats for {updated_count} prospects")
        print(f"🏁 Flagged {flagged_count} prospects for manual review")
        
        return flagged_count
    
    def show_flagged_summary(self):
        """Show summary of flagged players by priority"""
        if not self.flagged_players:
            print("✅ No players flagged for manual review")
            return
        
        # Group by priority
        by_priority = {"HIGH": [], "MEDIUM": [], "LOW": []}
        
        for name, data in self.flagged_players.items():
            priority = data.get("priority", "LOW")
            by_priority[priority].append(name)
        
        print(f"\n🏁 Flagged Players Summary:")
        print(f"🚨 HIGH Priority: {len(by_priority['HIGH'])} players (likely should graduate)")
        print(f"⚠️ MEDIUM Priority: {len(by_priority['MEDIUM'])} players (review recommended)")
        print(f"🔶 LOW Priority: {len(by_priority['LOW'])} players (monitor)")
        
        # Show HIGH priority players
        if by_priority["HIGH"]:
            print(f"\n🚨 HIGH PRIORITY - Likely Graduations:")
            for name in by_priority["HIGH"][:5]:  # Show first 5
                data = self.flagged_players[name]
                print(f"   • {name} ({data['manager']}): {data['reasons'][0]}")
            
            if len(by_priority["HIGH"]) > 5:
                print(f"   ... and {len(by_priority['HIGH']) - 5} more")

def main():
    print("🚀 FBP Service Days Tracker with Enhanced ID Cache")
    print("=" * 50)
    
    tracker = FlaggedServiceTracker()
    
    # Update all prospect stats and flag for review
    flagged_count = tracker.update_prospect_stats()
    
    # Show flagged summary
    tracker.show_flagged_summary()
    
    # Update Google Sheets with progress bars
    print(f"\n📊 Updating Google Sheets with progress bars...")
    try:
        # Import progress bar sheets updater
        from progress_bar_sheets import EnhancedProgressBarSheetsUpdater
        
        sheets_updater = EnhancedProgressBarSheetsUpdater()
        sheets_success = sheets_updater.update_worksheet()
        
        if sheets_success:
            print(f"✅ Google Sheets updated with progress bars!")
        else:
            print(f"⚠️ Google Sheets update failed (check permissions)")
            
    except ImportError as e:
        print(f"⚠️ Progress bar sheets updater import error: {e}")
        print(f"   Make sure progress_bar_sheets.py is in the same directory")
    except Exception as e:
        print(f"⚠️ Sheets update error: {e}")
    
    # Show overall summary
    total_prospects = len(tracker.stats_data)
    
    alerts = sum(1 for data in tracker.stats_data.values() 
                if data.get("flag_priority") == "HIGH" or
                any(info["percentage"] >= 90 or info["exceeded"] 
                    for limits in [data["mlb_limits_status"], data["fbp_limits_status"]]
                    for info in limits.values()))
    
    print(f"\n📊 Final Summary:")
    print(f"  Total prospects tracked: {total_prospects}")
    print(f"  Flagged for manual review: {flagged_count}")
    print(f"  High priority flags: {len([p for p in tracker.flagged_players.values() if p['priority'] == 'HIGH'])}")
    print(f"  Players with alerts: {alerts}")
    
    print(f"\n📋 Next Steps:")
    print(f"  1. Review flagged_for_review.json for graduation candidates")
    print(f"  2. Check Baseball Reference for HIGH priority players")  
    print(f"  3. Update prospect status in Google Sheet as needed")
    print(f"  4. Use Discord /alerts command to see current status")
    
    print(f"✅ Service days tracking with enhanced cache complete!")

    def is_pitcher(self, current_stats, career_stats=None):
        """Determine if player is primarily a pitcher based on their stats"""
        # Check current season stats first
        current_ip = current_stats.get("innings_pitched", 0)
        current_apps = current_stats.get("pitching_appearances", 0)
        current_ab = current_stats.get("at_bats", 0)
        
        # If they have meaningful pitching stats, they're a pitcher
        if current_ip > 0 or current_apps > 0:
            return True
        
        # If they have meaningful hitting stats and no pitching, they're a batter
        if current_ab > 0 and current_ip == 0 and current_apps == 0:
            return False
        
        # Check career stats if available
        if career_stats:
            career_ip = career_stats.get("career_innings", 0)
            career_apps = career_stats.get("career_appearances", 0)
            career_ab = career_stats.get("career_at_bats", 0)
            
            # If they have career pitching stats, they're a pitcher
            if career_ip > 0 or career_apps > 0:
                return True
            
            # If they have career hitting stats and no pitching, they're a batter
            if career_ab > 0 and career_ip == 0 and career_apps == 0:
                return False
        
        # Default to batter if unclear (most prospects start as position players)
        return False

    def check_mlb_limits(self, stats, is_pitcher_flag=None):
        """Check against MLB rookie limits - position aware"""
        if is_pitcher_flag is None:
            is_pitcher_flag = self.is_pitcher(stats)
        
        if is_pitcher_flag:
            # Pitchers: Only check IP and Active Days
            return {
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
        else:
            # Batters: Only check AB and Active Days
            return {
                "at_bats": {
                    "current": stats["at_bats"],
                    "limit": MLB_LIMITS["at_bats"],
                    "exceeded": stats["at_bats"] >= MLB_LIMITS["at_bats"],
                    "percentage": min(100, (stats["at_bats"] / MLB_LIMITS["at_bats"]) * 100)
                },
                "active_days": {
                    "current": stats["active_days"],
                    "limit": MLB_LIMITS["active_days"],
                    "exceeded": stats["active_days"] >= MLB_LIMITS["active_days"],
                    "percentage": min(100, (stats["active_days"] / MLB_LIMITS["active_days"]) * 100)
                }
            }

    def check_fbp_limits(self, stats, is_pitcher_flag=None):
        """Check against FBP rookie limits - position aware"""
        if is_pitcher_flag is None:
            is_pitcher_flag = self.is_pitcher(stats)
        
        if is_pitcher_flag:
            # Pitchers: Only check IP and Appearances
            return {
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
        else:
            # Batters: Only check AB
            return {
                "at_bats": {
                    "current": stats["at_bats"],
                    "limit": FBP_LIMITS["at_bats"],
                    "exceeded": stats["at_bats"] >= FBP_LIMITS["at_bats"],
                    "percentage": min(100, (stats["at_bats"] / FBP_LIMITS["at_bats"]) * 100)
                }
            }

if __name__ == "__main__":
    main()