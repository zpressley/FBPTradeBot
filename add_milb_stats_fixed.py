#!/usr/bin/env python3
"""
Add MiLB Stats Using Proven MLB API Patterns
Based on working patterns from track_roster_status.py and flagged_service_tracker.py
Fetches year-by-year stats at all levels for 2024 (last completed season)
"""

import json
import os
import time
import requests
import argparse

# Config
STATS_FILE = "data/player_stats.json"
CACHE_FILE = "data/mlb_id_cache.json"

# Sport ID to level mapping (from MLB API docs)
SPORT_ID_TO_LEVEL = {
    1: "MLB",
    11: "AAA",
    12: "AA",
    13: "A+",
    14: "A",
    15: "Low-A",
    16: "Rookie",
    17: "Winter",
    504: "DSL",
    508: "Complex"
}

# MLB API field mapping (from your working scripts)
MLB_API_BATTING_FIELDS = [
    'gamesPlayed', 'atBats', 'runs', 'hits', 'doubles', 'triples', 'homeRuns',
    'rbi', 'stolenBases', 'caughtStealing', 'baseOnBalls', 'strikeOuts',
    'avg', 'obp', 'slg', 'ops', 'plateAppearances', 'totalBases',
    'hitByPitch', 'sacFlies', 'sacBunts', 'groundIntoDoublePlay',
    'leftOnBase', 'intentionalWalks', 'babip'
]

MLB_API_PITCHING_FIELDS = [
    'gamesPlayed', 'gamesStarted', 'wins', 'losses', 'saves', 'holds',
    'blownSaves', 'saveOpportunities', 'inningsPitched', 'hits', 'runs',
    'earnedRuns', 'homeRuns', 'baseOnBalls', 'strikeOuts', 'battersFaced',
    'outs', 'completeGames', 'shutouts', 'era', 'whip', 'numberOfPitches',
    'strikes', 'strikePercentage', 'hitBatsmen', 'balks', 'wildPitches',
    'pickoffs', 'inheritedRunners', 'inheritedRunnersScored', 'gamesFinished'
]

def load_existing_data():
    """Load existing stats and cache"""
    print("📊 Loading existing data...")
    
    existing_stats = []
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            existing_stats = json.load(f)
    
    with open(CACHE_FILE, 'r') as f:
        cache = json.load(f)
    
    print(f"   ✅ Current stats: {len(existing_stats)} records")
    print(f"   ✅ MLB ID cache: {len(cache)} entries\n")
    
    return existing_stats, cache

def fetch_player_year_by_year_stats(mlb_id, season=2024):
    """
    Fetch year-by-year stats for a player (working pattern from flagged_service_tracker.py)
    Returns stats broken down by level (MLB, AAA, AA, etc.)
    """
    url = f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats"
    params = {
        'stats': 'yearByYear',
        'group': 'hitting,pitching'
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            return []
        
        data = response.json()
        level_stats = []
        
        for stat_group in data.get('stats', []):
            group_type = stat_group.get('group', {}).get('displayName', '')
            
            for split in stat_group.get('splits', []):
                split_season = split.get('season')
                
                # Only get requested season
                if split_season != str(season):
                    continue
                
                stat = split.get('stat', {})
                sport = split.get('sport', {})
                league = split.get('league', {})
                team = split.get('team', {})
                
                sport_id = sport.get('id')
                level = SPORT_ID_TO_LEVEL.get(sport_id, f"Sport_{sport_id}")
                
                # Extract stats based on type
                if group_type == 'hitting':
                    stats_dict = {field: stat.get(field) for field in MLB_API_BATTING_FIELDS if field in stat}
                    
                    if stats_dict and any(v for v in stats_dict.values()):  # Has some stats
                        level_stats.append({
                            'season': season,
                            'level': level,
                            'stat_type': 'batting',
                            'team': team.get('name', ''),
                            'league': league.get('name', ''),
                            'stats': stats_dict
                        })
                
                elif group_type == 'pitching':
                    stats_dict = {field: stat.get(field) for field in MLB_API_PITCHING_FIELDS if field in stat}
                    
                    if stats_dict and any(v for v in stats_dict.values()):
                        level_stats.append({
                            'season': season,
                            'level': level,
                            'stat_type': 'pitching',
                            'team': team.get('name', ''),
                            'league': league.get('name', ''),
                            'stats': stats_dict
                        })
        
        return level_stats
        
    except Exception as e:
        return []

def import_milb_stats(season=2024, limit=None):
    """Import MiLB stats for specified season"""
    print(f"🚀 Adding MiLB Stats from MLB API (Season: {season})")
    print("=" * 70 + "\n")
    
    existing_stats, cache = load_existing_data()
    
    # Get prospects that need stats for this season
    prospects_to_fetch = []
    for upid, cache_entry in cache.items():
        # Check if already have stats for this season
        has_season_stats = any(
            s['upid'] == upid and s['season'] == season
            for s in existing_stats
        )
        
        # Skip if already have this season (unless it's only CSV aggregate)
        csv_only = all(
            s['source'] == 'mlb_prospect_csv' 
            for s in existing_stats 
            if s['upid'] == upid and s['season'] == season
        )
        
        if not has_season_stats or csv_only:
            prospects_to_fetch.append({
                'upid': upid,
                'mlb_id': cache_entry['mlb_id'],
                'name': cache_entry['name']
            })
    
    if limit:
        prospects_to_fetch = prospects_to_fetch[:limit]
    
    print(f"📋 Fetching stats for: {len(prospects_to_fetch)} prospects")
    print(f"🗓️  Season: {season}")
    print(f"⏱️  Estimated time: {len(prospects_to_fetch) * 0.5 / 60:.0f} minutes\n")
    
    if len(prospects_to_fetch) > 100:
        user_input = input(f"⚠️  Fetch {len(prospects_to_fetch)} prospects? (y/n): ")
        if user_input.lower() != 'y':
            print("❌ Cancelled")
            return
    
    print("\n🌐 Fetching from MLB Stats API...")
    
    added_records = 0
    players_with_stats = 0
    players_no_stats = 0
    
    for i, prospect in enumerate(prospects_to_fetch, 1):
        # Rate limiting
        if i > 1:
            time.sleep(0.5)
        
        level_stats = fetch_player_year_by_year_stats(prospect['mlb_id'], season)
        
        if level_stats:
            players_with_stats += 1
            
            for level_stat in level_stats:
                record = {
                    "upid": prospect['upid'],
                    "player_name": prospect['name'],
                    "season": season,
                    "mlb_team": level_stat['team'].lower() if level_stat['team'] else "",
                    "mlb_id": prospect['mlb_id'],
                    "fbp_name": prospect['name'],
                    "fbp_manager": "",
                    "fbp_contract": "",
                    "fbp_player_type": "Farm",
                    "age": None,
                    "position": None,
                    "stat_type": level_stat['stat_type'],
                    "level": level_stat['level'],
                    "source": f"mlb_api_{season}",
                    
                    # Add all stats as top-level fields
                    **level_stat['stats'],
                    
                    # Add nulls for opposite stat type
                    **({"inningsPitched": None, "era": None, "whip": None} 
                       if level_stat['stat_type'] == 'batting' 
                       else {"atBats": None, "avg": None, "ops": None})
                }
                
                existing_stats.append(record)
                added_records += 1
        else:
            players_no_stats += 1
        
        # Progress updates
        if i % 100 == 0:
            print(f"   📊 Progress: {i}/{len(prospects_to_fetch)} | {players_with_stats} with stats, {players_no_stats} without, {added_records} records")
    
    # Save
    with open(STATS_FILE, 'w') as f:
        json.dump(existing_stats, f, indent=2)
    
    file_size = os.path.getsize(STATS_FILE)
    
    print("\n" + "=" * 70)
    print(f"✅ MiLB Stats Import Complete!")
    print("=" * 70)
    print(f"   Season: {season}")
    print(f"   Players checked: {len(prospects_to_fetch)}")
    print(f"   Players with stats: {players_with_stats}")
    print(f"   Players without stats: {players_no_stats}")
    print(f"   Records added: {added_records}")
    print(f"   New total: {len(existing_stats)} records")
    print(f"   File size: {file_size/1024:.1f} KB ({file_size/1024/1024:.2f} MB)")
    print("=" * 70)
    
    # Show level breakdown
    levels = {}
    for record in existing_stats:
        if record.get('season') == season and record.get('source', '').startswith('mlb_api'):
            level = record.get('level', 'Unknown')
            levels[level] = levels.get(level, 0) + 1
    
    if levels:
        print(f"\n📊 {season} MiLB Level Breakdown:")
        for level in sorted(levels.keys()):
            print(f"   {level}: {levels[level]} records")

def main():
    parser = argparse.ArgumentParser(description='Import MiLB stats from MLB API')
    parser.add_argument('--season', type=int, default=2024, 
                       help='Season to fetch (default: 2024)')
    parser.add_argument('--limit', type=int, default=None,
                       help='Limit number of prospects to fetch (for testing)')
    args = parser.parse_args()
    
    import_milb_stats(season=args.season, limit=args.limit)
    
    print(f"\n🎯 Next steps:")
    print(f"   • Add more seasons: python3 add_milb_level_stats.py --season 2023")
    print(f"   • Add Fangraphs: python3 import_fangraphs_historical.py")
    print(f"   • Add pybaseball: python3 add_pybaseball_stats.py")

if __name__ == "__main__":
    main()