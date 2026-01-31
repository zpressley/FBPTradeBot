#!/usr/bin/env python3
"""
Yahoo Fantasy - Complete Player Database Fetcher
Fetches ALL available players with 2024/2025 stats from Yahoo Fantasy
"""

import json
import requests
from xml.etree import ElementTree as ET
import os
import time
from datetime import datetime
from token_manager import get_access_token

# League settings
# For 2026, league 8560 is the correct context (used mainly for ownership).
LEAGUE_ID = "8560"

# MLB game IDs by season (mirrors mapping in fetch_historical_yahoo.py)
MLB_GAME_IDS = {
    2026: 469,
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

# Default game/season for help text and no-arg runs
DEFAULT_SEASON = 2024
CURRENT_SEASON = DEFAULT_SEASON
CURRENT_GAME_ID = MLB_GAME_IDS[CURRENT_SEASON]

def fetch_all_players_batch(start=0, count=25, position_filter=None, *, _retry=False):
    """
    Fetch a batch of players from Yahoo with stats
    
    Args:
        start: Starting index for pagination
        count: Number of players per batch (max 25)
        position_filter: Position filter (e.g., 'C', '1B', 'SP') or None for all
    
    Returns:
        (players_list, total_available_count)
    """
    token = get_access_token()
    if not token:
        return None, 0
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    # Build URL
    base_url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{CURRENT_GAME_ID}.l.{LEAGUE_ID}/players"
    params = f";start={start};count={count}"
    
    if position_filter:
        params += f";position={position_filter}"
    
    # Request stats and ownership info
    out_params = ";out=stats,ownership,percent_owned"
    
    url = base_url + params + out_params
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        # If we get unauthorized/forbidden once, try a token refresh + single retry
        if response.status_code in (401, 403) and not _retry:
            print(f"  ⚠️ HTTP {response.status_code} - refreshing token and retrying batch {start}-{start+count}...")
            # Force token refresh in token_manager; implementation there handles persistence.
            _ = get_access_token()
            return fetch_all_players_batch(start=start, count=count, position_filter=position_filter, _retry=True)
        
        if response.status_code != 200:
            print(f"  ❌ HTTP {response.status_code}")
            return None, 0
        
        # Parse XML
        ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
        root = ET.fromstring(response.text)
        
        players_data = []
        
        for player in root.findall(".//y:player", ns):
            # Player key and ID
            player_key_elem = player.find("y:player_key", ns)
            player_key = player_key_elem.text if player_key_elem is not None else None
            
            player_id_elem = player.find("y:player_id", ns)
            player_id = player_id_elem.text if player_id_elem is not None else None
            
            if not player_id:
                continue
            
            # Basic info
            name_elem = player.find(".//y:full", ns)
            name = name_elem.text if name_elem is not None else "Unknown"
            
            first_elem = player.find(".//y:first", ns)
            first_name = first_elem.text if first_elem is not None else ""
            
            last_elem = player.find(".//y:last", ns)
            last_name = last_elem.text if last_elem is not None else ""
            
            # Position
            pos_elem = player.find("y:display_position", ns)
            position = pos_elem.text if pos_elem is not None else "N/A"
            
            # Eligible positions
            eligible_positions = []
            for pos in player.findall(".//y:eligible_position", ns):
                eligible_positions.append(pos.text)
            
            # Team info
            team_abbr_elem = player.find("y:editorial_team_abbr", ns)
            team = team_abbr_elem.text if team_abbr_elem is not None else "FA"
            
            team_full_elem = player.find("y:editorial_team_full_name", ns)
            team_full = team_full_elem.text if team_full_elem is not None else ""
            
            # Status (e.g., DTD - day to day, IR, etc.)
            status_elem = player.find("y:status", ns)
            status = status_elem.text if status_elem is not None else ""
            
            # Ownership in your league
            ownership = player.find("y:ownership", ns)
            ownership_type = "available"
            owned_by_team = None
            percent_owned = 0
            
            if ownership is not None:
                ownership_type_elem = ownership.find("y:ownership_type", ns)
                if ownership_type_elem is not None:
                    ownership_type = ownership_type_elem.text
                
                owner_team_key_elem = ownership.find("y:owner_team_key", ns)
                if owner_team_key_elem is not None:
                    owned_by_team = owner_team_key_elem.text
            
            # Percent owned across all Yahoo leagues
            percent_owned_elem = player.find(".//y:percent_owned//y:value", ns)
            if percent_owned_elem is not None:
                try:
                    percent_owned = int(percent_owned_elem.text)
                except:
                    pass
            
            # Parse stats
            stats = {}
            player_stats = player.find("y:player_stats", ns)
            
            if player_stats is not None:
                for stat in player_stats.findall(".//y:stat", ns):
                    stat_id_elem = stat.find("y:stat_id", ns)
                    stat_value_elem = stat.find("y:value", ns)
                    
                    if stat_id_elem is not None and stat_value_elem is not None:
                        stat_id = stat_id_elem.text
                        stat_value = stat_value_elem.text
                        
                        # Try to convert to number
                        try:
                            if '.' in stat_value:
                                stat_value = float(stat_value)
                            else:
                                stat_value = int(stat_value)
                        except:
                            pass  # Keep as string
                        
                        stats[f"stat_{stat_id}"] = stat_value
            
            # Compile complete player data
            player_data = {
                "player_key": player_key,
                "player_id": player_id,
                "name": name,
                "first_name": first_name,
                "last_name": last_name,
                "position": position,
                "eligible_positions": eligible_positions,
                "team": team,
                "team_full": team_full,
                "status": status,
                "ownership_type": ownership_type,
                "owned_by": owned_by_team,
                "percent_owned": percent_owned,
                "stats": stats
            }
            
            players_data.append(player_data)
        
        # Get total available
        count_elem = root.find(".//y:count", ns)
        total_count = int(count_elem.text) if count_elem is not None else 0
        
        return players_data, total_count
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None, 0

def fetch_complete_player_database(max_players=3000):
    """
    Fetch comprehensive player database
    
    Default 3000 players covers:
    - All rostered players (~312 across 12 teams)
    - All relevant free agents
    - All prospects in systems
    """
    print("📊 Fetching Complete Yahoo Player Database")
    print("=" * 60)
    print(f"Game: MLB {CURRENT_GAME_ID} (season {CURRENT_SEASON})")
    print(f"League: {LEAGUE_ID}")
    print(f"Target: {max_players} players")
    print("=" * 60)
    
    all_players = []
    batch_size = 25
    start = 0
    
    while len(all_players) < max_players:
        print(f"\n📥 Batch {(start//batch_size)+1}: Players {start}-{start+batch_size}...")
        
        batch, total_available = fetch_all_players_batch(start, batch_size)
        
        if batch is None or len(batch) == 0:
            print(f"  ⚠️ No more players")
            break
        
        all_players.extend(batch)
        print(f"  ✅ Got {len(batch)} players (total: {len(all_players)})")
        
        # Show progress every 250 players
        if len(all_players) % 250 == 0:
            print(f"  📊 Progress: {len(all_players)}/{max_players} ({(len(all_players)/max_players)*100:.1f}%)")
        
        start += batch_size
        
        # Stop if we've fetched all available
        if total_available > 0 and start >= total_available:
            print(f"  ✅ Reached end of available players ({total_available})")
            break
        
        # Rate limiting
        time.sleep(0.5)
    
    return all_players

def save_complete_database(players, season=2024):
    """Save complete player database with stats"""
    os.makedirs("data", exist_ok=True)
    
    # Main database file
    main_file = f"data/yahoo_all_players_{season}.json"
    with open(main_file, 'w') as f:
        json.dump(players, f, indent=2)
    
    print(f"\n💾 Saved {len(players)} players to {main_file}")
    
    # Create summary
    print(f"\n📊 Database Summary:")
    print(f"   Total players: {len(players)}")
    
    # By ownership
    owned = [p for p in players if p["ownership_type"] == "team"]
    waivers = [p for p in players if p["ownership_type"] == "waivers"]
    free_agents = [p for p in players if p["ownership_type"] == "freeagents"]
    
    print(f"\n   Ownership:")
    print(f"   - Rostered: {len(owned)}")
    print(f"   - On Waivers: {len(waivers)}")
    print(f"   - Free Agents: {len(free_agents)}")
    
    # By position
    by_position = {}
    for p in players:
        pos = p["position"]
        by_position[pos] = by_position.get(pos, 0) + 1
    
    print(f"\n   By Position:")
    for pos in sorted(by_position.keys()):
        print(f"   - {pos}: {by_position[pos]}")
    
    # Save organized by position
    print(f"\n📁 Creating position-specific files...")
    
    os.makedirs("data/players_by_position", exist_ok=True)
    
    position_groups = {}
    for player in players:
        pos = player["position"]
        if pos not in position_groups:
            position_groups[pos] = []
        position_groups[pos].append(player)
    
    for pos, pos_players in position_groups.items():
        pos_file = f"data/players_by_position/{pos.lower()}_players.json"
        with open(pos_file, 'w') as f:
            json.dump(pos_players, f, indent=2)
        print(f"   💾 {pos}: {len(pos_players)} players → {pos_file}")
    
    # Create quick lookup index
    index = {}
    for player in players:
        index[player["player_id"]] = {
            "name": player["name"],
            "team": player["team"],
            "position": player["position"],
            "owned": player["ownership_type"] == "team"
        }
    
    index_file = "data/yahoo_player_index.json"
    with open(index_file, 'w') as f:
        json.dump(index, f, indent=2)
    
    print(f"   💾 Player index → {index_file}")
    
    return main_file

if __name__ == "__main__":
    import sys
    
    print("📊 Yahoo Fantasy - Complete Player Stats Fetcher")
    print("=" * 60)
    
    if len(sys.argv) > 1 and sys.argv[1] == "full":
        # Fetch comprehensive database
        max_players = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
        season = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_SEASON

        # Configure season/game ID for this run (locally)
        game_id = MLB_GAME_IDS.get(season)
        if game_id is None:
            print(f"❌ No MLB game ID configured for season {season}")
            sys.exit(1)

        # Update module-level values so downstream printing uses the right season
        CURRENT_SEASON = season
        CURRENT_GAME_ID = game_id
        
        print(f"\n🎯 Fetching {max_players} players with stats for season {season} (game {game_id})...\n")
        players = fetch_complete_player_database(max_players)
        
        if players:
            save_complete_database(players, season)
            print(f"\n🎉 Complete database created for season {season}!")
            
    else:
        print("\n💡 Usage:")
        print("  python3 fetch_yahoo_all_players.py full                # Fetch 3000 players for 2024 (default)")
        print("  python3 fetch_yahoo_all_players.py full 1000           # Fetch 1000 players for 2024")
        print("  python3 fetch_yahoo_all_players.py full 3000 2025      # Fetch 3000 players for 2025 season")
        print("  python3 fetch_yahoo_all_players.py full 5000 2025      # Fetch 5000 players for 2025 (takes ~5 min)")
        print("\n📊 What you'll get:")
        print("  - All rostered players with stats")
        print("  - Top ranked free agents")
        print("  - Ownership status in your league")
        print("  - Season stats for all players")
        print("  - Organized by position")
        print("  - Searchable index")
        print("\n🎯 Recommended: Start with 3000 players (covers everyone relevant)")
        print("\nRun with 'full' to fetch!")
