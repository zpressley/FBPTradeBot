#!/usr/bin/env python3
"""
Add Yahoo Current Season Stats to player_stats.json
Appends current MLB stats for all rostered players
"""

import json
import os
import sys
import requests
from xml.etree import ElementTree as ET

# Import token manager from data_pipeline folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'data_pipeline'))
sys.path.insert(0, os.path.dirname(__file__))

try:
    from token_manager import get_access_token
    print("✅ token_manager imported")
except ImportError:
    print("❌ Cannot import token_manager")
    print("💡 Make sure token_manager.py exists in:")
    print("   ./token_manager.py OR")
    print("   ./data_pipeline/token_manager.py")
    sys.exit(1)

# Config
LEAGUE_ID = "15505"
STATS_FILE = "data/player_stats.json"
CACHE_FILE = "data/mlb_id_cache.json"
COMBINED_FILE = "data/combined_players.json"

YAHOO_TEAM_MAP = {
    "1": "WIZ", "2": "B2J", "3": "CFL", "4": "HAM",
    "5": "JEP", "6": "LFB", "7": "LAW", "8": "SAD",
    "9": "DRO", "10": "RV", "11": "TBB", "12": "WAR"
}


def _parse_yahoo_stat_value(stat_key: str, raw: str):
    """Best-effort parser for Yahoo stat strings.

    Handles integers, floats, and composite values like "37/169" that
    occasionally show up in the API. For composite values we pick the
    most meaningful numeric component for the given stat_key, or return
    None if we cannot safely interpret it.
    """

    if raw is None:
        return None

    raw = str(raw).strip()
    if not raw:
        return None

    # Handle split forms like "37/169"
    if "/" in raw:
        left, right = [p.strip() for p in raw.split("/", 1)]

        # For counting stats we usually want the denominator (e.g. AB)
        if stat_key == "atBats" and right.isdigit():
            return int(right)

        # Fallback: pick the last numeric component if any
        for part in (right, left):
            if part.isdigit():
                return int(part)
        return None

    # Plain numeric value
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return None


def load_existing_stats():
    """Load existing player_stats.json"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    return []

def load_combined_players():
    """Load combined_players.json for UPID lookup"""
    with open(COMBINED_FILE, 'r') as f:
        return json.load(f)

def create_name_to_upid_map(combined_players):
    """Create player name → UPID lookup"""
    name_map = {}
    for player in combined_players:
        name = player.get('name', '').strip().lower()
        upid = player.get('upid', '').strip()
        if name and upid:
            name_map[name] = {
                'upid': upid,
                'mlb_id': player.get('yahoo_id'),  # Yahoo player ID
                'manager': player.get('manager', ''),
                'player_type': player.get('player_type', '')
            }
    return name_map

def fetch_yahoo_rosters_with_stats():
    """Fetch Yahoo stats using working API format"""
    print("📥 Fetching Yahoo player stats...")
    
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/xml"
    }
    
    # Use 2025 game key (458) and correct URL format
    GAME_ID = 458
    LEAGUE_NUM = "15505"
    LEAGUE_KEY = f"{GAME_ID}.l.{LEAGUE_NUM}"
    
    # Fetch from league players endpoint (not teams/roster)
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/players;start=0;count=1000/stats"
    
    print(f"   URL: {url}")
    
    response = requests.get(url, headers=headers, timeout=30)
    
    if response.status_code != 200:
        print(f"   ❌ Yahoo API error: {response.status_code}")
        print(f"   Response: {response.text[:500]}")
        return {}
    
    # Parse XML with namespace (like working script)
    ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
    root = ET.fromstring(response.text)
    
    players_data = []
    
    # Find all players
    for player in root.findall(".//y:player", ns):
        # Get player info
        name_elem = player.find(".//y:name/y:full", ns)
        team_elem = player.find("y:editorial_team_full_name", ns)
        
        # Get eligible positions
        pos_nodes = player.findall(".//y:eligible_positions/y:position", ns)
        positions = [p.text for p in pos_nodes if p is not None and p.text]
        position_str = positions[0] if positions else "N/A"
        
        name = name_elem.text if name_elem is not None else ""
        mlb_team = team_elem.text if team_elem is not None else ""
        
        # Get stats
        stats = {}
        stats_parent = player.find("y:player_stats", ns)
        
        if stats_parent is not None:
            for stat in stats_parent.findall(".//y:stat", ns):
                stat_id_elem = stat.find("y:stat_id", ns)
                value_elem = stat.find("y:value", ns)
                
                if stat_id_elem is None or value_elem is None:
                    continue
                
                stat_id = stat_id_elem.text
                value = value_elem.text
                
                # Map Yahoo stat IDs to standard field names
                stat_map = {
                    # Batting
                    "7": "runs",
                    "60": "atBats",
                    "8": "hits",
                    "10": "doubles",
                    "11": "triples",
                    "12": "homeRuns",
                    "13": "rbi",
                    "16": "stolenBases",
                    "17": "caughtStealing",
                    "18": "baseOnBalls",
                    "42": "strikeOuts",
                    "3": "avg",
                    "55": "obp",
                    "56": "slg",
                    "57": "ops",
                    "65": "plateAppearances",
                    "23": "totalBases",
                    
                    # Pitching
                    "50": "inningsPitched",
                    "28": "wins",
                    "29": "losses",
                    "32": "saves",
                    "48": "holds",
                    "84": "blownSaves",
                    "26": "era",
                    "27": "whip",
                    "42": "strikeOuts_pitch",  # Same ID as batting K
                    "39": "baseOnBalls_pitch"
                }
                
                if stat_id in stat_map:
                    parsed = _parse_yahoo_stat_value(stat_map[stat_id], value)
                    if parsed is not None:
                        stats[stat_map[stat_id]] = parsed
        
        if stats:  # Only add if we got stats
            players_data.append({
                'name': name,
                'position': position_str,
                'mlb_team': mlb_team.lower() if mlb_team else '',
                'fbp_manager': "",  # Will map later
                'stats': stats
            })
    
    print(f"   ✅ Fetched {len(players_data)} players with stats\n")
    return players_data

def append_yahoo_stats():
    """Append Yahoo stats to player_stats.json"""
    print("🚀 Adding Yahoo Stats to player_stats.json")
    print("=" * 70 + "\n")
    
    # Load existing data
    existing_stats = load_existing_stats()
    combined_players = load_combined_players()
    name_to_upid = create_name_to_upid_map(combined_players)
    
    print(f"📊 Current stats database: {len(existing_stats)} records")
    
    # Fetch Yahoo data
    yahoo_players = fetch_yahoo_rosters_with_stats()
    
    if not yahoo_players:
        print("❌ No Yahoo data retrieved")
        return
    
    # Match and append
    print("🔗 Matching Yahoo players to UPIDs...")
    
    added = 0
    no_upid = 0
    already_exists = 0
    
    for yahoo_player in yahoo_players:
        name_lower = yahoo_player['name'].lower()
        
        # Find UPID
        upid_info = name_to_upid.get(name_lower)
        
        if not upid_info:
            no_upid += 1
            continue
        
        upid = upid_info['upid']
        
        # Check if already have 2025 stats for this UPID
        if any(s['upid'] == upid and s['season'] == 2025 and s['source'] != 'mlb_prospect_csv' 
               for s in existing_stats):
            already_exists += 1
            continue
        
        # Determine if batter or pitcher based on stats
        has_batting = any(k in yahoo_player['stats'] for k in ['atBats', 'hits', 'homeRuns'])
        has_pitching = any(k in yahoo_player['stats'] for k in ['inningsPitched', 'era', 'wins'])
        
        # Create batter record if has batting stats
        if has_batting:
            record = {
                "upid": upid,
                "player_name": yahoo_player['name'],
                "season": 2025,
                "mlb_team": yahoo_player['mlb_team'],
                "mlb_id": None,  # Yahoo doesn't provide MLB ID easily
                "fbp_name": yahoo_player['name'],
                "fbp_manager": yahoo_player['fbp_manager'],
                "fbp_contract": "",
                "fbp_player_type": "MLB",
                "age": None,
                "position": yahoo_player['position'],
                "stat_type": "batting",
                "level": "MLB",
                "source": "yahoo_api",
                
                # Batting stats from Yahoo
                "games": int(yahoo_player['stats'].get('games', 0)) if yahoo_player['stats'].get('games') else None,
                "atBats": int(yahoo_player['stats'].get('atBats', 0)) if yahoo_player['stats'].get('atBats') else None,
                "runs": int(yahoo_player['stats'].get('runs', 0)) if yahoo_player['stats'].get('runs') else None,
                "hits": int(yahoo_player['stats'].get('hits', 0)) if yahoo_player['stats'].get('hits') else None,
                "doubles": int(yahoo_player['stats'].get('doubles', 0)) if yahoo_player['stats'].get('doubles') else None,
                "triples": int(yahoo_player['stats'].get('triples', 0)) if yahoo_player['stats'].get('triples') else None,
                "homeRuns": int(yahoo_player['stats'].get('homeRuns', 0)) if yahoo_player['stats'].get('homeRuns') else None,
                "rbi": int(yahoo_player['stats'].get('rbi', 0)) if yahoo_player['stats'].get('rbi') else None,
                "stolenBases": int(yahoo_player['stats'].get('stolenBases', 0)) if yahoo_player['stats'].get('stolenBases') else None,
                "baseOnBalls": int(yahoo_player['stats'].get('baseOnBalls', 0)) if yahoo_player['stats'].get('baseOnBalls') else None,
                "strikeOuts": int(yahoo_player['stats'].get('strikeOuts', 0)) if yahoo_player['stats'].get('strikeOuts') else None,
                "avg": float(yahoo_player['stats'].get('avg', 0)) if yahoo_player['stats'].get('avg') else None,
                "obp": float(yahoo_player['stats'].get('obp', 0)) if yahoo_player['stats'].get('obp') else None,
                "slg": float(yahoo_player['stats'].get('slg', 0)) if yahoo_player['stats'].get('slg') else None,
                "ops": float(yahoo_player['stats'].get('ops', 0)) if yahoo_player['stats'].get('ops') else None,
                
                # Nulls for pitching
                "inningsPitched": None,
                "era": None,
                "whip": None
            }
            
            existing_stats.append(record)
            added += 1
            
            if added <= 10:
                print(f"   ✅ {yahoo_player['name']:<30} {yahoo_player['fbp_manager']}")
        
        # Create pitcher record if has pitching stats
        if has_pitching and not has_batting:
            record = {
                "upid": upid,
                "player_name": yahoo_player['name'],
                "season": 2025,
                "mlb_team": yahoo_player['mlb_team'],
                "mlb_id": None,
                "fbp_name": yahoo_player['name'],
                "fbp_manager": yahoo_player['fbp_manager'],
                "fbp_contract": "",
                "fbp_player_type": "MLB",
                "age": None,
                "position": yahoo_player['position'],
                "stat_type": "pitching",
                "level": "MLB",
                "source": "yahoo_api",
                
                # Pitching stats from Yahoo
                "games": int(yahoo_player['stats'].get('games', 0)) if yahoo_player['stats'].get('games') else None,
                "wins": int(yahoo_player['stats'].get('wins', 0)) if yahoo_player['stats'].get('wins') else None,
                "saves": int(yahoo_player['stats'].get('saves', 0)) if yahoo_player['stats'].get('saves') else None,
                "inningsPitched": float(yahoo_player['stats'].get('inningsPitched', 0)) if yahoo_player['stats'].get('inningsPitched') else None,
                "era": float(yahoo_player['stats'].get('era', 0)) if yahoo_player['stats'].get('era') else None,
                "whip": float(yahoo_player['stats'].get('whip', 0)) if yahoo_player['stats'].get('whip') else None,
                "strikeOuts": int(yahoo_player['stats'].get('strikeOuts_pitch', 0)) if yahoo_player['stats'].get('strikeOuts_pitch') else None,
                "baseOnBalls": int(yahoo_player['stats'].get('baseOnBalls_pitch', 0)) if yahoo_player['stats'].get('baseOnBalls_pitch') else None,
                
                # Nulls for batting
                "atBats": None,
                "avg": None,
                "ops": None
            }
            
            existing_stats.append(record)
            added += 1
    
    # Save updated database
    with open(STATS_FILE, 'w') as f:
        json.dump(existing_stats, f, indent=2)
    
    file_size = os.path.getsize(STATS_FILE)
    
    print("\n" + "=" * 70)
    print("✅ Yahoo Stats Added!")
    print("=" * 70)
    print(f"   Added: {added} MLB player-seasons")
    print(f"   Skipped (no UPID): {no_upid}")
    print(f"   Skipped (already exists): {already_exists}")
    print(f"   New total: {len(existing_stats)} records")
    print(f"   File size: {file_size/1024:.1f} KB")
    print("=" * 70)
    
    print(f"\n🎯 Query example:")
    print(f"   stats = json.load(open('data/player_stats.json'))")
    print(f"   trout_2025 = next(s for s in stats if s['upid'] == '1967' and s['season'] == 2025)")
    print(f"   print(f\"Mike Trout: {{trout_2025['homeRuns']}} HR, {{trout_2025['avg']}} AVG\")")

if __name__ == "__main__":
    print("\n🚀 Yahoo Stats Importer")
    print("=" * 70 + "\n")
    append_yahoo_stats()