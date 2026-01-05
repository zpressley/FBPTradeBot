#!/usr/bin/env python3
"""
Build comprehensive player_stats.json - One JSON to Rule Them All
Flat structure: Each record = one player-season
Easy query: stats.filter(s => s.upid === '12345') gets ALL seasons for that player
"""

import json
import os
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Config
BATTER_CSV = "mlb_prospect_batstats_2025.csv"
PITCHER_CSV = "mlb_prospect_pitchstats_2025.csv"
FBP_SHEET_KEY = "13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA"
PLAYER_TAB = "Player Data"
CACHE_FILE = "data/mlb_id_cache.json"
OUTPUT_FILE = "data/player_stats.json"

def load_cache_and_fbp():
    """Load MLB ID cache and FBP data"""
    print("📊 Loading MLB ID cache...")
    with open(CACHE_FILE, 'r') as f:
        cache = json.load(f)
    
    # Create reverse lookup: MLB ID → UPID
    mlb_to_upid = {v['mlb_id']: upid for upid, v in cache.items()}
    print(f"   ✅ {len(cache)} UPIDs → MLB IDs")
    
    # Load FBP data for manager/contract info
    print("📊 Loading FBP data...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(FBP_SHEET_KEY).worksheet(PLAYER_TAB)
    all_data = sheet.get_all_values()
    headers = all_data[0]
    
    upid_data = {}
    for row in all_data[1:]:
        if len(row) > 0:
            upid = str(row[0]).strip()
            if upid:
                upid_data[upid] = {
                    'fbp_name': row[1].strip() if len(row) > 1 else "",
                    'fbp_manager': row[headers.index("Manager")].strip() if "Manager" in headers else "",
                    'fbp_contract': row[headers.index("Contract Type")].strip() if "Contract Type" in headers else "",
                    'fbp_player_type': row[headers.index("Player Type")].strip() if "Player Type" in headers else ""
                }
    
    print(f"   ✅ {len(upid_data)} UPIDs with FBP data\n")
    
    return mlb_to_upid, upid_data

def build_flat_stats():
    """Build flat stats database"""
    print("🚀 Building Comprehensive Player Stats Database")
    print("="*70 + "\n")
    
    mlb_to_upid, upid_data = load_cache_and_fbp()
    
    # This will hold ALL player-seasons (flat list)
    all_stats = []
    
    # ========================================================================
    # PART 1: 2025 Prospect Stats from MLB CSVs
    # ========================================================================
    print("📊 Processing 2025 prospect batters...")
    df_bat = pd.read_csv(BATTER_CSV)
    
    for _, row in df_bat.iterrows():
        mlb_id = int(row['playerId'])
        upid = mlb_to_upid.get(mlb_id)
        
        if not upid:
            continue
        
        fbp = upid_data.get(upid, {})
        
        # One record = one player-season
        record = {
            # Core identifiers (ALWAYS present)
            "upid": upid,
            "player_name": row['full_name'],
            "season": 2025,
            "mlb_team": row['team'],
            "mlb_id": mlb_id,
            
            # FBP metadata
            "fbp_name": fbp.get('fbp_name', row['full_name']),
            "fbp_manager": fbp.get('fbp_manager', ''),
            "fbp_contract": fbp.get('fbp_contract', ''),
            "fbp_player_type": fbp.get('fbp_player_type', 'Farm'),
            
            # Player bio
            "position": row['position'],
            "age": int(row['age']),
            
            # Stat category
            "stat_type": "batting",
            "level": "MiLB",  # These are minor league prospects
            "source": "mlb_prospect_csv",
            
            # All batting stats (standardized names)
            "games": int(row.get('atBats', 0) / 3.8) if row.get('atBats', 0) > 0 else 0,
            "atBats": int(row['atBats']),
            "plateAppearances": int(row['atBats']) + int(row['baseOnBalls']),  # Approximate
            "runs": int(row['runs']),
            "hits": int(row['hits']),
            "doubles": int(row['doubles']),
            "triples": int(row['triples']),
            "homeRuns": int(row['homeRuns']),
            "rbi": int(row['rbi']),
            "stolenBases": int(row['stolenBases']),
            "caughtStealing": int(row['caughtStealing']),
            "baseOnBalls": int(row['baseOnBalls']),
            "strikeOuts": int(row['strikeOuts']),
            "avg": float(row['avg']),
            "obp": float(row['obp']),
            "slg": float(row['slg']),
            "ops": float(row['ops']),
            "totalBases": int(row['totalBases']),
            "leftOnBase": int(row['leftOnBase']),
            
            # Ranking info
            "prospect_rank": int(row['rank']),
            
            # Advanced stats (null for now, will add from Fangraphs later)
            "wOBA": None,
            "wRC+": None,
            "ISO": None,
            "BABIP": None,
            "BB%": None,
            "K%": None
        }
        
        all_stats.append(record)
    
    print(f"   ✅ Added {len([s for s in all_stats if s['stat_type'] == 'batting'])} batter-seasons\n")
    
    # ========================================================================
    # PART 2: 2025 Prospect Pitchers
    # ========================================================================
    print("📊 Processing 2025 prospect pitchers...")
    df_pitch = pd.read_csv(PITCHER_CSV)
    
    for _, row in df_pitch.iterrows():
        mlb_id = int(row['playerId'])
        upid = mlb_to_upid.get(mlb_id)
        
        if not upid:
            continue
        
        # Skip if already added as batter (two-way players)
        if any(s['upid'] == upid and s['season'] == 2025 for s in all_stats):
            continue
        
        fbp = upid_data.get(upid, {})
        
        record = {
            # Core identifiers
            "upid": upid,
            "player_name": row['full_name'],
            "season": 2025,
            "mlb_team": row['team'],
            "mlb_id": mlb_id,
            
            # FBP metadata
            "fbp_name": fbp.get('fbp_name', row['full_name']),
            "fbp_manager": fbp.get('fbp_manager', ''),
            "fbp_contract": fbp.get('fbp_contract', ''),
            "fbp_player_type": fbp.get('fbp_player_type', 'Farm'),
            
            # Player bio
            "position": "P",
            "age": int(row['age']),
            
            # Stat category
            "stat_type": "pitching",
            "level": "MiLB",
            "source": "mlb_prospect_csv",
            
            # All pitching stats (standardized names)
            "games": int(row['gamesPitched']),
            "gamesStarted": int(row['gamesStarted']) if pd.notna(row.get('gamesStarted')) else 0,
            "inningsPitched": float(row['inningsPitched']),
            "hits": int(row['hits']),
            "runs": int(row['runs']),
            "earnedRuns": int(row['earnedRuns']),
            "baseOnBalls": int(row['baseOnBalls']),
            "strikeOuts": int(row['strikeOuts']),
            "homeRuns": int(row['homeRuns']),
            "era": float(row['era']),
            "whip": float(row['whip']),
            "wins": int(row['wins']) if pd.notna(row.get('wins')) else 0,
            "losses": int(row['losses']) if pd.notna(row.get('losses')) else 0,
            "saves": int(row['saves']) if pd.notna(row.get('saves')) else 0,
            "holds": int(row['holds']) if pd.notna(row.get('holds')) else 0,
            "blownSaves": int(row['blownSaves']) if pd.notna(row.get('blownSaves')) else 0,
            "battersFaced": int(row['battersFaced']) if pd.notna(row.get('battersFaced')) else 0,
            "completeGames": int(row['completeGames']) if pd.notna(row.get('completeGames')) else 0,
            "shutouts": int(row['shutouts']) if pd.notna(row.get('shutouts')) else 0,
            
            # Ranking info
            "prospect_rank": int(row['rank']),
            
            # Advanced stats (null for now)
            "FIP": None,
            "xFIP": None,
            "SIERA": None,
            "K%": None,
            "BB%": None,
            "K-BB%": None
        }
        
        all_stats.append(record)
    
    print(f"   ✅ Added {len([s for s in all_stats if s['stat_type'] == 'pitching'])} pitcher-seasons\n")
    
    # Save
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_stats, f, indent=2)
    
    file_size = os.path.getsize(OUTPUT_FILE)
    
    print("="*70)
    print("✅ PLAYER STATS DATABASE CREATED!")
    print("="*70)
    print(f"\n📄 File: {OUTPUT_FILE}")
    print(f"   Size: {file_size/1024:.1f} KB")
    print(f"   Total records: {len(all_stats)}")
    print(f"   Structure: Flat array (one record per player-season)")
    
    batters = len([s for s in all_stats if s['stat_type'] == 'batting'])
    pitchers = len([s for s in all_stats if s['stat_type'] == 'pitching'])
    unique_players = len(set(s['upid'] for s in all_stats))
    
    print(f"\n📊 Breakdown:")
    print(f"   Unique players: {unique_players}")
    print(f"   ├─ Batter-seasons: {batters}")
    print(f"   └─ Pitcher-seasons: {pitchers}")
    print(f"   Seasons covered: 2025 (current)")
    
    print(f"\n🎯 Query Examples:")
    print(f"\n   Get ALL seasons for a player:")
    print(f"   player_seasons = [s for s in stats if s['upid'] == '12345']")
    print(f"\n   Get 2025 batting stats:")
    print(f"   stats_2025 = [s for s in stats if s['season'] == 2025 and s['stat_type'] == 'batting']")
    print(f"\n   Get player's career progression:")
    print(f"   career = sorted([s for s in stats if s['upid'] == '12345'], key=lambda x: x['season'])")
    
    print(f"\n🚀 Next Steps:")
    print(f"   1. Add historical seasons (import Fangraphs CSVs)")
    print(f"   2. Add current MLB stats (Yahoo/MLB API)")
    print(f"   3. Add MiLB level-by-level stats")
    print(f"   4. Update daily for current season")
    
    print(f"\n💡 Future size estimate:")
    print(f"   2,500 players × 10 years = 25,000 records")
    print(f"   At ~2 KB per record = ~50 MB total")
    print(f"   ✅ Well within GitHub Pages limits!")

if __name__ == "__main__":
    build_flat_stats()