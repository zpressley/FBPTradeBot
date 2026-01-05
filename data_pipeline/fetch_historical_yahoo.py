#!/usr/bin/env python3
"""
FBP Historical Data Fetcher - Smart Team Mapping
Maps teams by manager names since Yahoo team IDs change over time
"""

import json
import requests
from xml.etree import ElementTree as ET
import os
from datetime import datetime
import sys

# Ensure project root is on sys.path so token_manager can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from token_manager import get_access_token

# Default/current league ID. For historical seasons, league IDs often change;
# override per-season values in LEAGUE_IDS_BY_SEASON below.
DEFAULT_LEAGUE_ID = "15505"

# Optional: map specific seasons to their Yahoo league IDs.
# Fill this in once you know the correct IDs for past seasons.
LEAGUE_IDS_BY_SEASON = {
    2012: "83712",   # https://baseball.fantasysports.yahoo.com/2012/b1/83712
    2013: "35612",   # https://baseball.fantasysports.yahoo.com/2013/b1/35612
    2014: "49387",   # https://baseball.fantasysports.yahoo.com/2014/b1/49387
    2015: "25115",   # https://baseball.fantasysports.yahoo.com/2015/b1/25115
    2016: "9042",    # https://baseball.fantasysports.yahoo.com/2016/b1/9042
    2017: "15081",   # https://baseball.fantasysports.yahoo.com/2017/b1/15081
    2018: "70077",   # https://baseball.fantasysports.yahoo.com/2018/b1/70077
    2019: "4517",    # https://baseball.fantasysports.yahoo.com/2019/b1/4517
    # 2020: falls back to DEFAULT_LEAGUE_ID (no specific FBP league URL provided)
    2021: "2893",    # https://baseball.fantasysports.yahoo.com/league/moneyball_est2012/2021
    2022: "46724",   # https://baseball.fantasysports.yahoo.com/2022/b1/46724
    2023: "2893",    # https://baseball.fantasysports.yahoo.com/league/moneyball_est2012/2023
    2024: "2893",    # https://baseball.fantasysports.yahoo.com/league/moneyball_est2012/2024
}

# Current FBP team abbreviations (2024-2025)
CURRENT_YAHOO_TEAM_MAP = {
    "1": "WIZ", "2": "B2J", "3": "CFL", "4": "HAM",
    "5": "JEP", "6": "LFB", "7": "LAW", "8": "SAD",
    "9": "DRO", "10": "RV", "11": "TBB", "12": "WAR"
}

# Manager name to FBP team mapping (for historical data)
# This handles team changes over the years
MANAGER_TO_TEAM = {
    # Current managers
    "zach pressley": "WAR",
    "whiz kids": "WIZ",
    
    "ben bourne": "SAD",
    "not much of a donkey": "SAD",
    
    "hammers": "HAM",
    
    "rick vaughn": "RV",
    
    "btwn2jackies": "B2J",
    
    "country fried lamb": "CFL",
    
    "law-abiding citizens": "LAW",
    "law abiding citizens": "LAW",
    
    "la flama blanca": "LFB",
    
    "jepordizers": "JEP",
    "jepordizers!": "JEP",
    
    "the bluke blokes": "TBB",
    "bluke blokes": "TBB",
    
    "andromedans": "DRO",
    
    "weekend warriors": "WAR",
    
    # Historical managers (teams that no longer exist)
    "ghost stallion": "GHOST",
    "scumbag club": "SCUM",
    "bronx bombers": "BB",
    "boner appetite": "BA",
    # Add more as you remember them
}

# Yahoo MLB Game IDs by Season
MLB_GAME_IDS = {
    2025: 458,
    2024: 404,
    2023: 412,
    2022: 404,
    2021: 398,
    2020: 388,
    2019: 378,
    2018: 370,
    2017: 363,
    2016: 357,
    2015: 346,
    2014: 346,
    2013: 328,
    2012: 328,
}

def map_manager_to_team(manager_name, team_name):
    """
    Map a manager/team to FBP team abbreviation
    
    Returns: (fbp_abbr, mapping_method)
    """
    # Try manager name first (most reliable)
    manager_lower = manager_name.lower().strip()
    if manager_lower in MANAGER_TO_TEAM:
        return MANAGER_TO_TEAM[manager_lower], "manager_name"
    
    # Try team name
    team_lower = team_name.lower().strip()
    if team_lower in MANAGER_TO_TEAM:
        return MANAGER_TO_TEAM[team_lower], "team_name"
    
    # No match - use team name as-is with warning
    return team_name.upper()[:4], "unknown"

def fetch_rosters_for_season(season, discover_managers=False):
    """
    Fetch rosters for a specific season
    
    Args:
        season: Year to fetch
        discover_managers: If True, print all manager/team names for mapping
    """
    print(f"\n📅 Fetching {season} season data...")
    
    game_id = MLB_GAME_IDS.get(season)
    if not game_id:
        print(f"  ⚠️ Unknown game_id for season {season}")
        return None, []

    # Use a per-season league ID when configured; otherwise fall back to default.
    league_id = LEAGUE_IDS_BY_SEASON.get(season, DEFAULT_LEAGUE_ID)
    
    token = get_access_token()
    if not token:
        print(f"  ❌ No valid Yahoo token")
        return None, []
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{game_id}.l.{league_id}/teams;out=roster,metadata"
    
    print(f"  🔗 Game ID: {game_id}")
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"  ❌ HTTP {response.status_code}")
            try:
                root = ET.fromstring(response.text)
                description = root.find(".//{http://www.yahooapis.com/v1/base.rng}description")
                if description is not None:
                    print(f"  📝 Yahoo says: {description.text}")
            except:
                pass
            return None, []
        
        # Parse XML response
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
        root = ET.fromstring(response.text)
        
        teams_data = {}
        team_mappings = []
        team_count = 0
        
        for team in root.findall(".//y:team", ns):
            team_id = team.find("y:team_id", ns).text
            team_name_elem = team.find("y:name", ns)
            team_name = team_name_elem.text if team_name_elem is not None else "Unknown Team"
            
            # Get manager info
            managers = team.findall(".//y:manager", ns)
            manager_name = "Unknown"
            if managers:
                manager_elem = managers[0]
                nickname = manager_elem.find("y:nickname", ns)
                manager_name = nickname.text if nickname is not None else "Unknown"
            
            # Map to FBP team
            fbp_team, mapping_method = map_manager_to_team(manager_name, team_name)
            
            # Store mapping info for discovery
            team_mappings.append({
                "yahoo_team_id": team_id,
                "team_name": team_name,
                "manager_name": manager_name,
                "fbp_abbr": fbp_team,
                "mapping_method": mapping_method
            })
            
            if discover_managers:
                status = "✅" if mapping_method != "unknown" else "⚠️"
                print(f"  {status} Team {team_id}: '{team_name}' (Manager: '{manager_name}') → {fbp_team} [{mapping_method}]")
            
            # Get roster
            players = []
            roster = team.find(".//y:roster", ns)
            
            if roster is not None:
                for player in roster.findall(".//y:player", ns):
                    name_elem = player.find(".//y:full", ns)
                    pos_elem = player.find("y:display_position", ns)
                    team_elem = player.find("y:editorial_team_abbr", ns)
                    player_id_elem = player.find("y:player_id", ns)
                    
                    name = name_elem.text if name_elem is not None else "Unknown"
                    pos = pos_elem.text if pos_elem is not None else "N/A"
                    mlb_team = team_elem.text if team_elem is not None else "N/A"
                    player_id = player_id_elem.text if player_id_elem is not None else "?"
                    
                    players.append({
                        "name": name,
                        "position": pos,
                        "team": mlb_team,
                        "yahoo_id": player_id
                    })
            
            teams_data[fbp_team] = {
                "players": players,
                "team_name": team_name,
                "manager_name": manager_name,
                "yahoo_team_id": team_id,
                "mapping_method": mapping_method
            }
            team_count += 1
        
        print(f"  ✅ Fetched {team_count} teams with rosters")
        
        # Warn about unmapped teams
        unmapped = [m for m in team_mappings if m["mapping_method"] == "unknown"]
        if unmapped:
            print(f"  ⚠️ {len(unmapped)} teams need manual mapping:")
            for m in unmapped:
                print(f"     - '{m['team_name']}' (Manager: '{m['manager_name']}')")
        
        return teams_data, team_mappings
        
    except requests.exceptions.Timeout:
        print(f"  ⏰ Request timed out")
        return None, []
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None, []

def fetch_standings_for_season(season):
    """Fetch standings for a specific season"""
    game_id = MLB_GAME_IDS.get(season)
    if not game_id:
        return None

    league_id = LEAGUE_IDS_BY_SEASON.get(season, DEFAULT_LEAGUE_ID)
    
    token = get_access_token()
    if not token:
        return None
    
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{game_id}.l.{league_id}/standings"
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            return None
        
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
        root = ET.fromstring(response.text)
        
        team_nodes = root.findall(".//y:team", ns)
        standings = []
        
        for team in team_nodes:
            team_id = team.find("y:team_id", ns).text
            
            # Get team/manager names for mapping
            team_name_elem = team.find("y:name", ns)
            team_name = team_name_elem.text if team_name_elem is not None else f"Team {team_id}"
            
            managers = team.findall(".//y:manager", ns)
            manager_name = "Unknown"
            if managers:
                nickname = managers[0].find("y:nickname", ns)
                manager_name = nickname.text if nickname is not None else "Unknown"
            
            # Map to FBP team
            fbp_abbr, _ = map_manager_to_team(manager_name, team_name)
            
            standings_node = team.find("y:team_standings", ns)
            if standings_node is None:
                continue
            
            wins = int(standings_node.attrib.get("wins", 0))
            losses = int(standings_node.attrib.get("losses", 0))
            ties = int(standings_node.attrib.get("ties", 0))
            total = wins + losses + ties
            win_pct = wins + 0.5 * ties
            pct_display = round(win_pct / total, 3) if total > 0 else 0.000
            
            rank_elem = team.find("y:team_standings/y:rank", ns)
            rank = int(rank_elem.text) if rank_elem is not None else 0
            
            standings.append({
                "rank": rank,
                "team": fbp_abbr,
                "team_name": team_name,
                "manager": manager_name,
                "record": f"{wins}-{losses}-{ties}",
                "win_pct": pct_display
            })
        
        standings.sort(key=lambda x: x["rank"])
        print(f"  ✅ Fetched standings for {len(standings)} teams")
        return standings
        
    except Exception as e:
        print(f"  ❌ Standings error: {e}")
        return None

def list_leagues_for_season(season):
    """List all MLB leagues for the logged-in user for a given season.

    This helps you discover the correct league_id to plug into
    LEAGUE_IDS_BY_SEASON for historical fetching.
    """
    print(f"\n🔍 Listing leagues for season {season}...")
    game_id = MLB_GAME_IDS.get(season)
    if not game_id:
        print(f"  ⚠️ Unknown game_id for season {season}")
        return

    token = get_access_token()
    if not token:
        print("  ❌ No valid Yahoo token")
        return

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_keys={game_id}/leagues"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"  ❌ HTTP {resp.status_code}")
            print(resp.text[:1000])
            return

        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
        root = ET.fromstring(resp.text)

        leagues = []
        for league in root.findall(".//y:league", ns):
            league_id = league.find("y:league_id", ns).text
            name_elem = league.find("y:name", ns)
            url_elem = league.find("y:url", ns)
            name = name_elem.text if name_elem is not None else "(no name)"
            league_url = url_elem.text if url_elem is not None else ""
            leagues.append({"league_id": league_id, "name": name, "url": league_url})

        if not leagues:
            print("  ⚠️ No leagues found for this season.")
            return

        print("\n📋 Leagues for this user and season:")
        for lg in leagues:
            print(f"  • league_id={lg['league_id']} | name='{lg['name']}'")
            if lg["url"]:
                print(f"    URL: {lg['url']}")

    except Exception as e:
        print(f"  ❌ Error listing leagues: {e}")


def save_historical_data(season, teams_data, standings, team_mappings):
    """Save historical data with team mapping info"""
    hist_dir = f"data/historical/{season}"
    os.makedirs(hist_dir, exist_ok=True)
    
    # Save rosters (simplified format for consistency)
    if teams_data:
        # Convert to simple format: {abbr: [players]}
        roster_data = {}
        for fbp_abbr, team_info in teams_data.items():
            roster_data[fbp_abbr] = team_info["players"]
        
        roster_file = f"{hist_dir}/yahoo_players_{season}.json"
        with open(roster_file, 'w') as f:
            json.dump(roster_data, f, indent=2)
        print(f"  💾 Saved rosters: {roster_file}")
        
        # Save team mapping info separately
        mapping_file = f"{hist_dir}/team_mappings_{season}.json"
        with open(mapping_file, 'w') as f:
            json.dump(team_mappings, f, indent=2)
        print(f"  💾 Saved mappings: {mapping_file}")
    
    # Save standings
    if standings:
        standings_data = {
            "season": season,
            "date": f"{season}-09-30",
            "standings": standings
        }
        standings_file = f"{hist_dir}/standings_{season}.json"
        with open(standings_file, 'w') as f:
            json.dump(standings_data, f, indent=2)
        print(f"  💾 Saved standings: {standings_file}")
    
    return teams_data is not None or standings is not None

def discover_manager_names(season):
    """
    Discover mode: Just fetch team/manager names for a season
    Use this to build your MANAGER_TO_TEAM mapping
    """
    print(f"\n🔍 DISCOVERY MODE: {season} Season Team/Manager Names")
    print("=" * 60)
    
    teams_data, team_mappings = fetch_rosters_for_season(season, discover_managers=True)
    
    if team_mappings:
        print(f"\n📋 Copy these to your MANAGER_TO_TEAM mapping:")
        print("=" * 60)
        for mapping in team_mappings:
            manager = mapping["manager_name"].lower()
            team = mapping["team_name"].lower()
            fbp = mapping["fbp_abbr"]
            
            print(f'    "{manager}": "{fbp}",  # {mapping["team_name"]}')
            if manager != team:
                print(f'    "{team}": "{fbp}",')
    
    return team_mappings

def fetch_all_historical_data(start_year=2012, end_year=2025):
    """
    Fetch all historical data with smart team mapping
    """
    print("🏆 FBP Historical Data Fetcher (Smart Mapping)")
    print("=" * 60)
    print(f"Fetching seasons: {start_year} - {end_year}")
    print("=" * 60)
    
    results = {
        "successful": [],
        "failed": [],
        "needs_mapping": []
    }
    
    all_unmapped = []
    
    for season in range(start_year, end_year + 1):
        print(f"\n{'='*60}")
        print(f"Season {season} (FBP Season {season - 2011})")
        print(f"{'='*60}")
        
        if season not in MLB_GAME_IDS:
            print(f"  ⚠️ No game_id configured for {season}")
            continue
        
        # Fetch rosters with team mapping
        teams_data, team_mappings = fetch_rosters_for_season(season, discover_managers=False)
        
        # Check for unmapped teams
        unmapped = [m for m in team_mappings if m["mapping_method"] == "unknown"]
        if unmapped:
            results["needs_mapping"].append(season)
            all_unmapped.extend([(season, m) for m in unmapped])
        
        # Fetch standings
        standings = fetch_standings_for_season(season)
        
        # Save data
        if teams_data or standings:
            saved = save_historical_data(season, teams_data, standings, team_mappings)
            if saved:
                results["successful"].append(season)
                print(f"  🎉 Season {season} data saved!")
            else:
                results["failed"].append(season)
        else:
            results["failed"].append(season)
            print(f"  ❌ Could not fetch season {season}")
        
        # Be nice to Yahoo's API
        import time
        time.sleep(2)
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 Historical Data Fetch Summary")
    print(f"{'='*60}")
    print(f"✅ Successful: {len(results['successful'])} seasons")
    if results['successful']:
        print(f"   {results['successful']}")
    
    print(f"\n❌ Failed: {len(results['failed'])} seasons")
    if results['failed']:
        print(f"   {results['failed']}")
    
    if all_unmapped:
        print(f"\n⚠️ Teams needing manual mapping: {len(all_unmapped)}")
        print("=" * 60)
        print("Add these to MANAGER_TO_TEAM dictionary:")
        print("=" * 60)
        
        seen = set()
        for season, mapping in all_unmapped:
            key = (mapping["manager_name"].lower(), mapping["team_name"].lower())
            if key not in seen:
                seen.add(key)
                print(f'    "{mapping["manager_name"].lower()}": "???",  # {season}: {mapping["team_name"]}')
    
    return results

def fetch_current_season_only(season=2024):
    """Fetch just one season (for testing or current data)"""
    print(f"🎯 Fetching {season} season data")
    print("=" * 60)
    
    teams_data, team_mappings = fetch_rosters_for_season(season, discover_managers=True)
    standings = fetch_standings_for_season(season)
    
    if teams_data or standings:
        save_historical_data(season, teams_data, standings, team_mappings)
        
        # Also save to main data directory for current use
        if teams_data:
            # Simplified format for current data
            roster_data = {abbr: info["players"] for abbr, info in teams_data.items()}
            with open("data/yahoo_players.json", 'w') as f:
                json.dump(roster_data, f, indent=2)
            print(f"  💾 Also saved to data/yahoo_players.json")
        
        if standings:
            standings_data = {
                "season": season,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "standings": standings
            }
            with open("data/standings.json", 'w') as f:
                json.dump(standings_data, f, indent=2)
            print(f"  💾 Also saved to data/standings.json")
        
        return True
    else:
        print(f"  ❌ Could not fetch {season} data")
        return False

if __name__ == "__main__":
    import sys
    
    print("🏆 FBP Yahoo Historical Data Fetcher (Smart Team Mapping)")
    print("=" * 60)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "discover":
            # Discovery mode - show all manager/team names
            season = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
            discover_manager_names(season)

        elif sys.argv[1] == "leagues":
            # List leagues for the logged-in user for a season
            season = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
            list_leagues_for_season(season)
            
        elif sys.argv[1] == "all":
            # Fetch all historical data for FBP seasons with known game keys/league IDs
            fetch_all_historical_data(2012, 2024)
            
        elif sys.argv[1] == "range":
            # Fetch specific range
            start = int(sys.argv[2]) if len(sys.argv) > 2 else 2012
            end = int(sys.argv[3]) if len(sys.argv) > 3 else 2024
            fetch_all_historical_data(start, end)
            
        elif sys.argv[1].isdigit():
            # Fetch specific year
            fetch_current_season_only(int(sys.argv[1]))
    else:
        # Default: Show usage and fetch 2024
        print("\n💡 Usage:")
        print("  python3 fetch_historical_yahoo.py                    # Fetch 2024")
        print("  python3 fetch_historical_yahoo.py 2023               # Fetch specific year")
        print("  python3 fetch_historical_yahoo.py discover 2023      # Show manager names for mapping")
        print("  python3 fetch_historical_yahoo.py range 2020 2024    # Fetch range")
        print("  python3 fetch_historical_yahoo.py all                # Fetch all FBP seasons 2012–2024")
        print("\nRunning default: Fetching 2024 season...\n")
        fetch_current_season_only(2024)