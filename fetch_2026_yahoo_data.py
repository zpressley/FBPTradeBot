#!/usr/bin/env python3
"""
Enhanced Yahoo Fantasy Data Fetcher for 2026 Season
Fetches: rosters, positions, league-specific rankings, player stats
"""

import json
import requests
from xml.etree import ElementTree as ET
import os
import sys
from datetime import datetime

# Ensure token_manager from random/ is importable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, "random"))

from token_manager import get_access_token

# League Configuration
LEAGUE_ID = "8560"
GAME_KEY = "469"  # Yahoo game key for MLB 2026

YAHOO_TEAM_MAP = {
    "1": "WIZ", "2": "B2J", "3": "CFL", "4": "HAM",
    "5": "JEP", "6": "LFB", "7": "LAW", "8": "SAD",
    "9": "DRO", "10": "RV", "11": "TBB", "12": "WAR"
}

class YahooDataFetcher:
    def __init__(self):
        self.token = get_access_token()
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json"
        }
        self.base_url = "https://fantasysports.yahooapis.com/fantasy/v2"
        
    def fetch_league_info(self):
        """Fetch basic league information including current season"""
        print("📊 Fetching league information...")
        
        url = f"{self.base_url}/league/{GAME_KEY}.l.{LEAGUE_ID}"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            print(f"❌ Failed to fetch league info: {response.status_code}")
            return None
        
        root = ET.fromstring(response.text)
        
        # Extract league details
        league_info = {
            "league_id": LEAGUE_ID,
            "league_key": f"{GAME_KEY}.l.{LEAGUE_ID}",
            "season": root.find(".//season").text if root.find(".//season") is not None else "2026",
            "name": root.find(".//name").text if root.find(".//name") is not None else "Unknown",
            "current_week": root.find(".//current_week").text if root.find(".//current_week") is not None else "0",
            "start_week": root.find(".//start_week").text if root.find(".//start_week") is not None else "1",
            "end_week": root.find(".//end_week").text if root.find(".//end_week") is not None else "26",
            "fetched_at": datetime.now().isoformat()
        }
        
        print(f"✅ League: {league_info['name']}")
        print(f"✅ Season: {league_info['season']}")
        print(f"✅ Current Week: {league_info['current_week']}")
        
        return league_info
    
    def fetch_rosters_with_details(self):
        """Fetch complete roster data including positions, stats, and rankings"""
        print("\n📋 Fetching detailed rosters...")
        
        url = f"{self.base_url}/league/{GAME_KEY}.l.{LEAGUE_ID}/teams;out=roster/players/stats"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            print(f"❌ Failed to fetch rosters: {response.status_code}")
            return {}
        
        root = ET.fromstring(response.text)
        teams_data = {}
        
        for team in root.findall(".//team"):
            team_id = team.find("team_id").text
            fbp_team = YAHOO_TEAM_MAP.get(team_id)
            
            if not fbp_team:
                continue
            
            print(f"  📝 Processing {fbp_team}...")
            
            players = []
            roster = team.find(".//roster")
            
            if roster is not None:
                for player in roster.findall(".//player"):
                    player_data = self._extract_player_data(player)
                    if player_data:
                        players.append(player_data)
            
            teams_data[fbp_team] = {
                "team_id": team_id,
                "team_name": fbp_team,
                "player_count": len(players),
                "players": players
            }
            
            print(f"    ✅ {len(players)} players loaded")
        
        return teams_data
    
    def _extract_player_data(self, player_element):
        """Extract comprehensive player data from XML element"""
        try:
            # Basic info
            player_id = player_element.find("player_id")
            name_elem = player_element.find(".//name/full")
            
            if player_id is None or name_elem is None:
                return None
            
            player_data = {
                "yahoo_id": player_id.text,
                "name": name_elem.text,
                "first_name": player_element.find(".//name/first").text if player_element.find(".//name/first") is not None else "",
                "last_name": player_element.find(".//name/last").text if player_element.find(".//name/last") is not None else "",
            }
            
            # Position data
            primary_pos = player_element.find(".//primary_position")
            if primary_pos is not None:
                player_data["primary_position"] = primary_pos.text
            
            display_pos = player_element.find(".//display_position")
            if display_pos is not None:
                player_data["display_position"] = display_pos.text
            
            eligible_positions = player_element.find(".//eligible_positions")
            if eligible_positions is not None:
                positions = []
                for pos in eligible_positions.findall(".//position"):
                    if pos.text:
                        positions.append(pos.text)
                player_data["eligible_positions"] = positions
            
            # Team info
            team_abbr = player_element.find(".//editorial_team_abbr")
            if team_abbr is not None:
                player_data["mlb_team"] = team_abbr.text
            
            team_full = player_element.find(".//editorial_team_full_name")
            if team_full is not None:
                player_data["mlb_team_full"] = team_full.text
            
            # Status
            status = player_element.find(".//status")
            if status is not None:
                player_data["status"] = status.text
            
            # Injury status
            injury_note = player_element.find(".//injury_note")
            if injury_note is not None:
                player_data["injury_note"] = injury_note.text
            
            # Rankings
            player_data["rankings"] = self._extract_rankings(player_element)
            
            # Stats
            player_data["stats"] = self._extract_stats(player_element)
            
            return player_data
            
        except Exception as e:
            print(f"⚠️ Error extracting player data: {e}")
            return None
    
    def _extract_rankings(self, player_element):
        """Extract player ranking data"""
        rankings = {}
        
        # Average draft position
        adp = player_element.find(".//average_draft_position")
        if adp is not None:
            rankings["average_draft_position"] = adp.text
        
        # Pre-season rank
        preseason_rank = player_element.find(".//preseason_rank")
        if preseason_rank is not None:
            rankings["preseason_rank"] = preseason_rank.text
        
        # Current rank
        current_rank = player_element.find(".//current_rank")
        if current_rank is not None:
            rankings["current_rank"] = current_rank.text
        
        return rankings if rankings else None
    
    def _extract_stats(self, player_element):
        """Extract player statistics"""
        stats = {}
        
        # Look for player stats
        player_stats = player_element.find(".//player_stats")
        if player_stats is not None:
            # Coverage type (season, week, etc)
            coverage = player_stats.find(".//coverage_type")
            if coverage is not None:
                stats["coverage_type"] = coverage.text
            
            # Individual stats
            stat_list = []
            for stat in player_stats.findall(".//stat"):
                stat_id = stat.find("stat_id")
                stat_value = stat.find("value")
                
                if stat_id is not None and stat_value is not None:
                    stat_list.append({
                        "stat_id": stat_id.text,
                        "value": stat_value.text
                    })
            
            if stat_list:
                stats["stats"] = stat_list
        
        return stats if stats else None
    
    def fetch_player_stats_mappings(self):
        """Fetch the stat ID to stat name mappings for the league"""
        print("\n📊 Fetching stat categories...")
        
        url = f"{self.base_url}/league/{GAME_KEY}.l.{LEAGUE_ID}/settings"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            print(f"❌ Failed to fetch stat categories: {response.status_code}")
            return {}
        
        root = ET.fromstring(response.text)
        
        stat_categories = {
            "batting": [],
            "pitching": []
        }
        
        # Extract stat categories
        for stat_cat in root.findall(".//stat_category"):
            stat_id = stat_cat.find("stat_id")
            display_name = stat_cat.find("display_name")
            position_type = stat_cat.find("position_type")
            
            if stat_id is not None and display_name is not None:
                stat_info = {
                    "stat_id": stat_id.text,
                    "name": display_name.text,
                    "abbreviation": stat_cat.find("name").text if stat_cat.find("name") is not None else ""
                }
                
                if position_type is not None:
                    if position_type.text == "B":
                        stat_categories["batting"].append(stat_info)
                    elif position_type.text == "P":
                        stat_categories["pitching"].append(stat_info)
        
        print(f"✅ Batting categories: {len(stat_categories['batting'])}")
        print(f"✅ Pitching categories: {len(stat_categories['pitching'])}")
        
        return stat_categories
    
    def save_all_data(self):
        """Fetch and save all 2026 Yahoo data"""
        print("🚀 FBP Yahoo Data Collector for 2026")
        print("=" * 50)
        
        # Create data directory
        os.makedirs("data", exist_ok=True)

        # Fetch all data
        league_info = self.fetch_league_info()
        roster_data = self.fetch_rosters_with_details()
        stat_mappings = self.fetch_player_stats_mappings()

        # Combine into single output
        output = {
            "league_info": league_info,
            "stat_categories": stat_mappings,
            "teams": roster_data,
            "fetched_at": datetime.now().isoformat()
        }

        # Save complete dataset
        complete_file = "data/yahoo_2026_complete.json"
        with open(complete_file, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n✅ Complete data saved: {complete_file}")

        # Save simplified roster-only file (backward compatible)
        simple_rosters = {}
        for team_abbr, team_data in roster_data.items():
            simple_rosters[team_abbr] = [
                {
                    "name": p["name"],
                    "position": p.get("display_position", ""),
                    "team": p.get("mlb_team", ""),
                    "yahoo_id": p["yahoo_id"]
                }
                for p in team_data["players"]
            ]

        simple_file = "data/yahoo_players.json"
        with open(simple_file, "w") as f:
            json.dump(simple_rosters, f, indent=2)
        print(f"✅ Simple rosters saved: {simple_file}")

        # Also archive copies under data/historical/2026
        hist_dir = os.path.join("data", "historical", "2026")
        os.makedirs(hist_dir, exist_ok=True)

        hist_complete_file = os.path.join(hist_dir, "yahoo_2026_complete.json")
        with open(hist_complete_file, "w") as f:
            json.dump(output, f, indent=2)
        print(f"✅ Historical complete data saved: {hist_complete_file}")

        hist_simple_file = os.path.join(hist_dir, "yahoo_players_2026.json")
        with open(hist_simple_file, "w") as f:
            json.dump(simple_rosters, f, indent=2)
        print(f"✅ Historical simple rosters saved: {hist_simple_file}")

        # Print summary
        print(f"\n📊 Data Summary:")
        print(f"  League: {league_info.get('name', 'Unknown')}")
        print(f"  Season: {league_info.get('season', 'Unknown')}")
        print(f"  Teams: {len(roster_data)}")
        print(f"  Total Players: {sum(t['player_count'] for t in roster_data.values())}")
        print(f"  Batting Stats: {len(stat_mappings.get('batting', []))}")
        print(f"  Pitching Stats: {len(stat_mappings.get('pitching', []))}")
        
        return output

def main():
    """Main execution"""
    try:
        fetcher = YahooDataFetcher()
        data = fetcher.save_all_data()
        
        print(f"\n🎉 2026 Yahoo data successfully collected!")
        print(f"📁 Files created:")
        print(f"   • data/yahoo_2026_complete.json (full dataset)")
        print(f"   • data/yahoo_players.json (simplified rosters)")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print(f"\n💡 Troubleshooting:")
        print(f"   1. Check if Yahoo token is valid (run get_token.py)")
        print(f"   2. Verify league ID is correct: {LEAGUE_ID}")
        print(f"   3. Ensure 2026 season has started in Yahoo")

if __name__ == "__main__":
    main()
