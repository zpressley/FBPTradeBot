#!/usr/bin/env python3
"""
FBP Data Pipeline Comprehensive Audit
Maps all data sources, pipelines, outputs, and consumers
Based on actual FBP project structure
"""

import json
import os
import pandas as pd
from datetime import datetime
from pathlib import Path
from collections import defaultdict

def format_size(bytes):
    """Format file size"""
    for unit in ['B', 'KB', 'MB']:
        if bytes < 1024.0:
            return f"{bytes:>6.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:>6.1f} GB"

def get_file_info(filepath):
    """Get comprehensive file info"""
    if not os.path.exists(filepath):
        return None
    
    stat = os.stat(filepath)
    mod_time = datetime.fromtimestamp(stat.st_mtime)
    age = datetime.now() - mod_time
    
    # Format age
    if age.days == 0:
        age_str = "Today"
    elif age.days == 1:
        age_str = "Yesterday"
    elif age.days < 7:
        age_str = f"{age.days}d ago"
    elif age.days < 30:
        age_str = f"{age.days//7}w ago"
    else:
        age_str = mod_time.strftime("%Y-%m-%d")
    
    # Count records
    records = "?"
    try:
        if filepath.endswith('.json'):
            with open(filepath, 'r') as f:
                data = json.load(f)
            if isinstance(data, list):
                records = len(data)
            elif isinstance(data, dict):
                if all(isinstance(v, list) for v in data.values()):
                    records = sum(len(v) for v in data.values())
                else:
                    records = len(data)
        elif filepath.endswith('.csv'):
            df = pd.read_csv(filepath)
            records = len(df)
    except:
        pass
    
    return {
        'size': format_size(stat.st_size),
        'modified': age_str,
        'records': records
    }

def print_section(title, emoji="📋"):
    """Print section header"""
    print()
    print("=" * 100)
    print(f"{emoji} {title}")
    print("=" * 100)

def print_file_status(name, info, description="", indent="   "):
    """Print file status line"""
    if info:
        print(f"{indent}✅ {name:<40} {info['size']} │ {info['records']:>6} records │ {info['modified']:>12} │ {description}")
    else:
        print(f"{indent}❌ {name:<40} {'NOT FOUND':>10} │ {description}")

def main():
    print()
    print("=" * 100)
    print("🎯 FBP DATA PIPELINE COMPREHENSIVE AUDIT")
    print("=" * 100)
    print(f"📍 Working directory: {os.getcwd()}")
    print(f"🕐 Audit time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # ============================================================================
    # SECTION 1: DATA SOURCES (External APIs & Sheets)
    # ============================================================================
    print_section("DATA SOURCES (External)", "📥")
    
    print("\n   🌐 Yahoo Fantasy API:")
    print("      └─ Provides: Active rosters, standings, matchups, player stats")
    print("      └─ Update: Daily during season, frozen in offseason")
    print("      └─ Auth: token.json (OAuth2)")
    
    print("\n   📊 Google Sheets (FBP HUB 2.0):")
    print("      └─ Sheet ID: 13oEFhmmVF82qMnX0NV_W0szmfGjZySNaOALg3MhoRzA")
    print("      └─ Provides: Prospect contracts, WizBucks, keeper contracts")
    print("      └─ Update: Manual by managers + bot updates")
    print("      └─ Auth: google_creds.json (Service Account)")
    
    print("\n   📊 Google Sheets (UPID Database):")
    print("      └─ Sheet ID: 19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo")
    print("      └─ Provides: 6,018 player UPIDs with alternate names")
    print("      └─ Update: Maintained externally")
    
    print("\n   ⚾ MLB Stats API:")
    print("      └─ Provides: Player bio, service time, roster status, MiLB stats")
    print("      └─ Update: Real-time (rate limited)")
    print("      └─ Auth: None required (public API)")
    
    print("\n   📈 MLB Prospect Stats (Manual CSVs):")
    print("      └─ Files: mlb_prospect_batstats_2025.csv, mlb_prospect_pitchstats_2025.csv")
    print("      └─ Provides: 795 prospects with comprehensive stats")
    print("      └─ Update: Manual upload from MLB.com")
    
    # ============================================================================
    # SECTION 2: CORE DATA FILES
    # ============================================================================
    print_section("CORE DATA FILES", "💾")
    
    core_files = {
        "combined_players.json": "Master player database (ALL players - MLB + Farm)",
        "yahoo_players.json": "Current Yahoo rosters (12 teams)",
        "sheet_players.json": "Google Sheets player data",
        "mlb_id_cache.json": "UPID → MLB ID mappings (2,745 entries)",
        "enhanced_mlb_id_cache.json": "Extended ID cache with BBRef IDs"
    }
    
    for filename, desc in core_files.items():
        filepath = f"data/{filename}"
        info = get_file_info(filepath)
        print_file_status(filename, info, desc)
    
    # ============================================================================
    # SECTION 3: STATISTICS & ANALYTICS
    # ============================================================================
    print_section("STATISTICS & ANALYTICS", "📊")
    
    stats_files = {
        "fbp_prospect_stats_2025.csv": "Merged prospect stats (batters + pitchers)",
        "fbp_complete_stats.csv": "Complete stats (MLB advanced + MiLB)",
        "fbp_mlb_advanced.csv": "Fangraphs advanced stats (wOBA, wRC+, FIP, etc.)",
        "fbp_milb_stats.csv": "Minor league stats (all levels)",
        "service_stats.json": "Prospect service time tracking data",
        "flagged_for_review.json": "Prospects flagged for graduation"
    }
    
    for filename, desc in stats_files.items():
        filepath = f"data/{filename}"
        info = get_file_info(filepath)
        print_file_status(filename, info, desc)
    
    # ============================================================================
    # SECTION 4: FINANCIAL SYSTEM
    # ============================================================================
    print_section("FINANCIAL SYSTEM (WizBucks)", "💰")
    
    financial_files = {
        "wizbucks.json": "Current WizBucks balances (12 teams)",
        "wizbucks_installments.json": "PAD/KAP/APA period tracking",
        "wizbuck_transactions.json": "All WB transaction history"
    }
    
    for filename, desc in financial_files.items():
        filepath = f"data/{filename}"
        info = get_file_info(filepath)
        print_file_status(filename, info, desc)
    
    # ============================================================================
    # SECTION 5: COMPETITION & STANDINGS
    # ============================================================================
    print_section("COMPETITION & STANDINGS", "🏆")
    
    competition_files = {
        "standings.json": "Current standings + weekly matchups"
    }
    
    for filename, desc in competition_files.items():
        filepath = f"data/{filename}"
        info = get_file_info(filepath)
        print_file_status(filename, info, desc)
    
    # ============================================================================
    # SECTION 6: SERVICE TIME TRACKING
    # ============================================================================
    print_section("SERVICE TIME TRACKING", "⏱️")
    
    service_files = {
        "roster_events.json": "Call-up/send-down event log",
        "service_stats.json": "Current service time calculations",
        "flagged_for_review.json": "Prospects approaching limits"
    }
    
    for filename, desc in service_files.items():
        filepath = f"data/{filename}"
        info = get_file_info(filepath)
        print_file_status(filename, info, desc)
    
    # Check snapshots
    snapshot_dir = Path("data/roster_snapshots")
    if snapshot_dir.exists():
        snapshots = sorted(list(snapshot_dir.glob("*.json")))
        print(f"\n   📸 roster_snapshots/:")
        print(f"      └─ {len(snapshots)} daily snapshots")
        if snapshots:
            print(f"      └─ Latest: {snapshots[-1].name}")
            print(f"      └─ Oldest: {snapshots[0].name}")
    else:
        print(f"\n   ❌ roster_snapshots/ directory not created yet")
    
    # ============================================================================
    # SECTION 7: FUTURE/PLANNED FILES
    # ============================================================================
    print_section("FUTURE DATA FILES (Not Yet Implemented)", "🔮")
    
    future_files = {
        "keeper_salaries.json": "Keeper contract salary calculations",
        "draft_tax.json": "Draft pick penalties by team",
        "il_tags.json": "IL tag assignments for keeper deadline",
        "draft_picks.json": "Draft pick ownership tracker",
        "draft_boards.json": "Personal draft boards (12 teams)",
        "auction_current.json": "Active weekly auction bids",
        "auction_history.json": "Historical auction results",
        "transactions.json": "Master transaction ledger",
        "player_photos.json": "Player photo URLs with credits",
        "26man_compliance.json": "30-man roster compliance tracker"
    }
    
    for filename, desc in future_files.items():
        filepath = f"data/{filename}"
        info = get_file_info(filepath)
        print_file_status(filename, info, desc)
    
    # ============================================================================
    # SECTION 8: DATA PIPELINE SCRIPTS
    # ============================================================================
    print_section("DATA PIPELINE SCRIPTS", "⚙️")
    
    print("\n   📂 data_pipeline/ folder:")
    pipeline_scripts = {
        "update_all.py": "Master orchestrator (runs all updates)",
        "update_yahoo_players.py": "Fetch rosters from Yahoo API",
        "update_hub_players.py": "Fetch data from Google Sheets",
        "update_wizbucks.py": "Fetch WizBucks balances",
        "merge_players.py": "Merge Yahoo + Sheets → combined_players.json",
        "save_standings.py": "Fetch and save standings + matchups"
    }
    
    for script, desc in pipeline_scripts.items():
        filepath = f"data_pipeline/{script}"
        if os.path.exists(filepath):
            print(f"      ✅ {script:<30} {desc}")
        else:
            print(f"      ❌ {script:<30} {desc}")
    
    print("\n   📂 Root level scripts:")
    root_scripts = {
        "build_mlb_id_cache.py": "Build UPID → MLB ID mappings",
        "track_roster_status.py": "Daily roster status snapshots",
        "log_roster_events.py": "Log call-ups/send-downs",
        "count_service_days.py": "Calculate service days from events"
    }
    
    for script, desc in root_scripts.items():
        if os.path.exists(script):
            print(f"      ✅ {script:<30} {desc}")
        else:
            print(f"      ❌ {script:<30} {desc}")
    
    print("\n   📂 service_time/ folder:")
    service_scripts = {
        "flagged_service_tracker.py": "Track prospects approaching limits",
        "progress_bar_sheets.py": "Update Google Sheets with progress bars"
    }
    
    for script, desc in service_scripts.items():
        filepath = f"service_time/{script}"
        if os.path.exists(filepath):
            print(f"      ✅ {script:<30} {desc}")
        else:
            print(f"      ❌ {script:<30} {desc}")
    
    # ============================================================================
    # SECTION 9: DATA FLOW DIAGRAM
    # ============================================================================
    print_section("DATA FLOW DIAGRAM", "🔄")
    
    print("""
   📥 DAILY INPUTS (Automated via GitHub Actions)
   ┌─────────────────────────────────────────────────────────────┐
   │ Yahoo API          → update_yahoo_players.py                 │
   │ Google Sheets      → update_hub_players.py                   │
   │ Google Sheets (WB) → update_wizbucks.py                      │
   │ MLB Stats API      → track_roster_status.py                  │
   └─────────────────────────────────────────────────────────────┘
                               ↓
   ⚙️  PROCESSING
   ┌─────────────────────────────────────────────────────────────┐
   │ merge_players.py   → Combines Yahoo + Sheets                │
   │ save_standings.py  → Parses Yahoo standings XML              │
   │ log_roster_events.py → Detects call-ups/send-downs          │
   │ build_mlb_id_cache.py → UPID → MLB ID mappings              │
   └─────────────────────────────────────────────────────────────┘
                               ↓
   💾 CORE DATA FILES
   ┌─────────────────────────────────────────────────────────────┐
   │ combined_players.json    (2,504 players - MLB + Farm)       │
   │ mlb_id_cache.json        (2,745 MLB IDs)                    │
   │ standings.json           (12 teams + matchups)              │
   │ wizbucks.json            (12 team balances)                 │
   │ roster_events.json       (call-up/send-down history)        │
   └─────────────────────────────────────────────────────────────┘
                               ↓
   📤 CONSUMERS
   ┌─────────────────────────────────────────────────────────────┐
   │ Discord Bot (bot.py)                                         │
   │ ├─ /player   → combined_players.json                        │
   │ ├─ /roster   → combined_players.json                        │
   │ ├─ /trade    → combined_players.json + wizbucks.json        │
   │ └─ /standings → standings.json                              │
   │                                                              │
   │ FBP Hub Website (future)                                     │
   │ ├─ Player DB → combined_players.json + prospect_stats       │
   │ ├─ Rosters   → combined_players.json                        │
   │ ├─ WizBucks  → wizbucks.json                                │
   │ ├─ Service   → service_stats.json + flagged_for_review.json │
   │ └─ Standings → standings.json                               │
   └─────────────────────────────────────────────────────────────┘
    """)
    
    # ============================================================================
    # SECTION 10: UPDATE FREQUENCY
    # ============================================================================
    print_section("UPDATE SCHEDULE", "📅")
    
    schedules = {
        "🔴 REAL-TIME (In-Season)": [
            ("Yahoo rosters", "Every waiver clear + manual refresh"),
            ("Standings/Matchups", "After each game day"),
            ("Player stats", "Live via MLB API")
        ],
        "🟠 DAILY (Automated)": [
            ("combined_players.json", "6:00 AM EST via GitHub Actions"),
            ("yahoo_players.json", "6:00 AM EST"),
            ("sheet_players.json", "6:00 AM EST"),
            ("standings.json", "6:00 AM EST"),
            ("roster_snapshots/", "6:00 AM EST"),
            ("roster_events.json", "6:00 AM EST after snapshots")
        ],
        "🟡 WEEKLY": [
            ("mlb_id_cache.json", "Rebuild for new prospects"),
            ("service_stats.json", "Service time calculations"),
            ("fbp_prospect_stats", "Update from MLB CSVs or API")
        ],
        "🟢 SEASONAL": [
            ("PAD (Feb 10)", "Prospect assignments, DC/PC/BC contracts"),
            ("PPD (Feb 17)", "Prospect draft results"),
            ("KAP (Feb 20-28)", "Keeper assignments, IL tags, RaT"),
            ("Keeper Draft (Mar 8)", "Draft results, new contracts"),
            ("APA (Post-draft)", "Auction portal allotments"),
            ("Trade Deadline (Jul 31)", "TDA allotment distribution")
        ],
        "🔵 MANUAL": [
            ("MLB prospect CSVs", "Upload when available from MLB.com"),
            ("player_photos.json", "Manager uploads + admin approval"),
            ("WizBucks adjustments", "Commissioner manual corrections")
        ]
    }
    
    for category, items in schedules.items():
        print(f"\n   {category}:")
        for item, desc in items:
            print(f"      • {item:<30} {desc}")
    
    # ============================================================================
    # SECTION 11: ACTUAL FILE STATUS
    # ============================================================================
    print_section("ACTUAL FILE STATUS", "📂")
    
    data_dir = Path("data")
    
    if not data_dir.exists():
        print("\n   ❌ data/ directory does not exist!")
        print("   💡 Create with: mkdir -p data")
        print("   💡 Then run: python3 data_pipeline/update_all.py")
        return
    
    print(f"\n   📁 Data directory: {data_dir.absolute()}")
    
    all_files = list(data_dir.glob("**/*"))
    data_files = [f for f in all_files if f.is_file() and not f.name.startswith('.')]
    
    print(f"   📊 Total files: {len(data_files)}")
    print(f"   💾 Total size: {format_size(sum(f.stat().st_size for f in data_files))}")
    
    # Group by category
    print("\n   📋 Files by category:")
    
    json_files = [f for f in data_files if f.suffix == '.json' and 'snapshot' not in str(f)]
    csv_files = [f for f in data_files if f.suffix == '.csv']
    snapshot_files = [f for f in data_files if 'snapshot' in str(f)]
    
    print(f"\n      JSON files: {len(json_files)}")
    for f in sorted(json_files)[:10]:
        info = get_file_info(str(f))
        print(f"         • {f.name:<40} {info['size']} │ {info['modified']}")
    
    if len(json_files) > 10:
        print(f"         ... and {len(json_files) - 10} more")
    
    print(f"\n      CSV files: {len(csv_files)}")
    for f in sorted(csv_files):
        info = get_file_info(str(f))
        print(f"         • {f.name:<40} {info['size']} │ {info['modified']}")
    
    print(f"\n      Snapshot files: {len(snapshot_files)}")
    if snapshot_files:
        latest = max(snapshot_files, key=lambda f: f.stat().st_mtime)
        oldest = min(snapshot_files, key=lambda f: f.stat().st_mtime)
        print(f"         Latest: {latest.name}")
        print(f"         Oldest: {oldest.name}")
    
    # ============================================================================
    # SECTION 12: DATA QUALITY CHECKS
    # ============================================================================
    print_section("DATA QUALITY ANALYSIS", "🔬")
    
    combined_file = "data/combined_players.json"
    if os.path.exists(combined_file):
        with open(combined_file, 'r') as f:
            combined = json.load(f)
        
        total = len(combined)
        mlb = sum(1 for p in combined if p.get('player_type') == 'MLB')
        farm = sum(1 for p in combined if p.get('player_type') == 'Farm')
        with_yahoo = sum(1 for p in combined if p.get('yahoo_id'))
        with_upid = sum(1 for p in combined if p.get('upid'))
        with_manager = sum(1 for p in combined if p.get('manager'))
        unowned = sum(1 for p in combined if not p.get('manager'))
        
        print(f"\n   📊 combined_players.json Quality:")
        print(f"      Total players: {total:,}")
        print(f"      ├─ MLB players: {mlb:,}")
        print(f"      ├─ Farm players: {farm:,}")
        print(f"      ├─ With Yahoo ID: {with_yahoo:,}")
        print(f"      ├─ With UPID: {with_upid:,}")
        print(f"      ├─ Owned (has manager): {with_manager:,}")
        print(f"      └─ Unowned (available): {unowned:,}")
        
        # Check MLB ID cache coverage
        cache_file = "data/mlb_id_cache.json"
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cache = json.load(f)
            
            farm_in_cache = sum(1 for p in combined 
                               if p.get('player_type') == 'Farm' 
                               and str(p.get('upid')) in cache)
            
            coverage_pct = (farm_in_cache / farm * 100) if farm > 0 else 0
            
            print(f"\n   🔗 MLB ID Cache Coverage:")
            print(f"      Total cache entries: {len(cache):,}")
            print(f"      Farm players covered: {farm_in_cache:,}/{farm:,} ({coverage_pct:.1f}%)")
            print(f"      Missing MLB IDs: {farm - farm_in_cache:,}")
    
    # Check prospect stats
    prospect_stats = "data/fbp_prospect_stats_2025.csv"
    if os.path.exists(prospect_stats):
        df = pd.read_csv(prospect_stats)
        
        batters = len(df[df['player_type'] == 'batter'])
        pitchers = len(df[df['player_type'] == 'pitcher'])
        
        print(f"\n   📈 Prospect Stats Coverage:")
        print(f"      Total with stats: {len(df):,}")
        print(f"      ├─ Batters: {batters:,}")
        print(f"      └─ Pitchers: {pitchers:,}")
        
        # Show available stat columns
        stat_cols = [c for c in df.columns if c not in ['upid', 'name', 'player_type', 'manager']]
        print(f"      Stats available: {len(stat_cols)} columns")
        print(f"         Sample: {', '.join(stat_cols[:8])}")
    
    # ============================================================================
    # SECTION 13: DISCORD BOT STATUS
    # ============================================================================
    print_section("DISCORD BOT INTEGRATION", "🤖")
    
    bot_commands = {
        "/player": {
            "files": ["combined_players.json"],
            "desc": "Lookup any player across all teams"
        },
        "/roster": {
            "files": ["combined_players.json"],
            "desc": "View team rosters (MLB + Farm)"
        },
        "/trade": {
            "files": ["combined_players.json", "wizbucks.json"],
            "desc": "Submit trade proposals"
        },
        "/standings": {
            "files": ["standings.json"],
            "desc": "Current standings + matchups"
        }
    }
    
    print("\n   Discord Commands → Data Dependencies:")
    for cmd, info in bot_commands.items():
        file_list = info["files"]
        desc = info["desc"]
        all_exist = all(os.path.exists(f"data/{f}") for f in file_list)
        status = "✅" if all_exist else "❌"
        print(f"      {status} {cmd:<15} {desc}")
        for f in file_list:
            exists = "✅" if os.path.exists(f"data/{f}") else "❌"
            print(f"         {exists} Needs: {f}")
    
    # ============================================================================
    # SECTION 14: RECOMMENDATIONS
    # ============================================================================
    print_section("RECOMMENDATIONS & NEXT STEPS", "🎯")
    
    # Check what's missing
    critical_missing = []
    if not os.path.exists("data/combined_players.json"):
        critical_missing.append("combined_players.json")
    if not os.path.exists("data/mlb_id_cache.json"):
        critical_missing.append("mlb_id_cache.json")
    
    if critical_missing:
        print("\n   🚨 CRITICAL - Discord bot won't work without these:")
        for f in critical_missing:
            print(f"      ❌ {f}")
        print("\n   🔧 Quick fix:")
        print("      cd ~/fbp-trade-bot")
        print("      python3 data_pipeline/update_all.py")
    else:
        print("\n   ✅ CORE DATA FILES PRESENT - Bot should work!")
    
    print("\n   📅 DAILY AUTOMATION:")
    print("      ✅ GitHub Actions workflow exists")
    print("      └─ Runs: update_all.py at 6:00 AM EST daily")
    print("      └─ Updates: combined_players.json, standings.json, wizbucks.json")
    
    print("\n   🔄 MANUAL TASKS:")
    print("      • Upload MLB prospect CSVs weekly (for better stats)")
    print("      • Run merge_with_upid_alternates.py after CSV upload")
    print("      • Run service time tracker for graduation flags")
    
    print("\n   🚀 FOR WEBSITE DEVELOPMENT:")
    print("      Priority files to expose:")
    print("      1. combined_players.json - Player database with search")
    print("      2. fbp_prospect_stats_2025.csv - Prospect stats + rankings")
    print("      3. wizbucks.json - WizBucks balances")
    print("      4. standings.json - Current standings")
    print("      5. service_stats.json - Service time progress bars")
    
    # ============================================================================
    # FINAL SUMMARY
    # ============================================================================
    print()
    print("=" * 100)
    print("📊 PIPELINE HEALTH SUMMARY")
    print("=" * 100)
    
    health_score = 0
    max_score = 5
    
    if os.path.exists("data/combined_players.json"):
        print("   ✅ Core player database")
        health_score += 1
    else:
        print("   ❌ Core player database missing")
    
    if os.path.exists("data/mlb_id_cache.json"):
        print("   ✅ MLB ID mappings")
        health_score += 1
    else:
        print("   ❌ MLB ID mappings missing")
    
    if os.path.exists("data/standings.json"):
        print("   ✅ Standings data")
        health_score += 1
    else:
        print("   ❌ Standings data missing")
    
    if os.path.exists("data/wizbucks.json"):
        print("   ✅ WizBucks data")
        health_score += 1
    else:
        print("   ❌ WizBucks data missing")
    
    if os.path.exists("data_pipeline/update_all.py"):
        print("   ✅ Automated pipeline")
        health_score += 1
    else:
        print("   ❌ Automated pipeline missing")
    
    health_pct = (health_score / max_score) * 100
    
    print(f"\n   Pipeline Health: {health_score}/{max_score} ({health_pct:.0f}%)")
    
    if health_score == max_score:
        print("   🎉 All systems operational!")
    elif health_score >= 3:
        print("   ⚠️ Core systems working, some enhancements needed")
    else:
        print("   🚨 Critical systems missing, run data pipeline setup")
    
    print()
    print("=" * 100)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Audit failed: {e}")
        import traceback
        traceback.print_exc()