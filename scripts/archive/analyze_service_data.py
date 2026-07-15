#!/usr/bin/env python3
# Analyze current service days data to understand what we're working with

import json
import os
from datetime import datetime

def analyze_roster_snapshots():
    """Analyze existing roster snapshots"""
    snapshot_dir = "data/roster_snapshots"
    
    if not os.path.exists(snapshot_dir):
        print("❌ No roster_snapshots directory found")
        return
    
    files = sorted([f for f in os.listdir(snapshot_dir) if f.endswith('.json')])
    print(f"📊 Found {len(files)} roster snapshot files")
    
    if not files:
        print("❌ No snapshot files found")
        return
    
    # Analyze latest snapshot
    latest_file = files[-1]
    print(f"\n📅 Latest snapshot: {latest_file}")
    
    with open(os.path.join(snapshot_dir, latest_file), 'r') as f:
        data = json.load(f)
    
    if 'players' in data:
        players = data['players']
        print(f"👥 Players tracked: {len(players)}")
        
        # Show sample player data
        print("\n📋 Sample player data:")
        for i, (name, info) in enumerate(list(players.items())[:3]):
            print(f"  {i+1}. {name}:")
            for key, value in info.items():
                print(f"     {key}: {value}")
            print()
    
    # Check date range
    dates = [f.replace('.json', '') for f in files]
    print(f"📆 Date range: {dates[0]} to {dates[-1]}")
    
    return files, players if 'players' in data else {}

def analyze_roster_events():
    """Analyze roster events log"""
    events_file = "data/roster_events.json"
    
    if not os.path.exists(events_file):
        print("❌ No roster_events.json found")
        return {}
    
    with open(events_file, 'r') as f:
        events = json.load(f)
    
    print(f"\n📋 Roster Events Analysis:")
    print(f"Players with events: {len(events)}")
    
    total_events = sum(len(player_events) for player_events in events.values())
    print(f"Total events logged: {total_events}")
    
    # Show sample events
    print("\n📝 Sample events:")
    for i, (player, player_events) in enumerate(list(events.items())[:3]):
        print(f"  {i+1}. {player}: {len(player_events)} events")
        for event in player_events[-2:]:  # Last 2 events
            print(f"     {event['date']}: {event['event']}")
    
    return events

def analyze_prospects_data():
    """Analyze prospects from combined data"""
    try:
        with open("data/combined_players.json", 'r') as f:
            players = json.load(f)
    except FileNotFoundError:
        print("❌ No combined_players.json found")
        return []
    
    # Find prospects
    prospects = [p for p in players if p.get('player_type') == 'Farm']
    print(f"\n🌱 Prospects Analysis:")
    print(f"Total prospects: {len(prospects)}")
    
    # Group by manager
    by_manager = {}
    for p in prospects:
        manager = p.get('manager', 'Unknown')
        if manager not in by_manager:
            by_manager[manager] = []
        by_manager[manager].append(p['name'])
    
    print("\n📊 Prospects by manager:")
    for manager, prospect_list in sorted(by_manager.items()):
        print(f"  {manager}: {len(prospect_list)} prospects")
        # Show first few names
        for name in prospect_list[:3]:
            print(f"    - {name}")
        if len(prospect_list) > 3:
            print(f"    ... and {len(prospect_list) - 3} more")
    
    return prospects

def check_mlb_id_cache():
    """Check MLB ID mapping"""
    try:
        with open("data/mlb_id_cache.json", 'r') as f:
            cache = json.load(f)
        
        print(f"\n🆔 MLB ID Cache:")
        print(f"Players with MLB IDs: {len(cache)}")
        
        # Sample entries
        print("\n📋 Sample MLB ID mappings:")
        for i, (upid, info) in enumerate(list(cache.items())[:3]):
            print(f"  UPID {upid}: {info.get('name')} → MLB ID {info.get('mlb_id')}")
        
        return cache
    except FileNotFoundError:
        print("❌ No mlb_id_cache.json found")
        return {}

def main():
    print("🔍 FBP Service Days Data Analysis")
    print("=" * 50)
    
    # Analyze existing data
    files, latest_players = analyze_roster_snapshots()
    events = analyze_roster_events() 
    prospects = analyze_prospects_data()
    mlb_cache = check_mlb_id_cache()
    
    print("\n" + "=" * 50)
    print("📋 SUMMARY & RECOMMENDATIONS:")
    
    if not files:
        print("❌ No roster snapshots - need to start daily tracking")
    else:
        print(f"✅ Have {len(files)} days of roster data")
    
    if not events:
        print("❌ No roster events logged - need to build change detection")
    else:
        print(f"✅ Have events for {len(events)} players")
    
    if not prospects:
        print("❌ No prospects found - check data pipeline")
    else:
        print(f"✅ Found {len(prospects)} prospects to track")
    
    if not mlb_cache:
        print("❌ No MLB ID cache - need to build prospect→MLB ID mapping")
    else:
        print(f"✅ Have MLB IDs for {len(mlb_cache)} players")
    
    print("\n🎯 NEXT STEPS:")
    print("1. Build daily roster tracking system")
    print("2. Create MLB stats tracking (AB, IP, appearances)")
    print("3. Build service days calculator")
    print("4. Add Discord commands for reporting")
    print("5. Create Google Sheets progress tracker")

if __name__ == "__main__":
    main()