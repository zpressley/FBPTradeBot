#!/usr/bin/env python3
# diagnosis_script.py - Find missing prospect data

import json
import os

def diagnose_missing_prospects():
    print("🔍 Diagnosing Missing Prospect Data")
    print("=" * 50)
    
    # Load all data sources
    try:
        with open("data/combined_players.json", 'r') as f:
            combined_players = json.load(f)
        print(f"✅ Loaded combined_players.json: {len(combined_players)} total players")
    except FileNotFoundError:
        print("❌ combined_players.json not found")
        return
    
    try:
        with open("data/mlb_id_cache.json", 'r') as f:
            mlb_cache = json.load(f)
        print(f"✅ Loaded mlb_id_cache.json: {len(mlb_cache)} mappings")
    except FileNotFoundError:
        print("❌ mlb_id_cache.json not found")
        mlb_cache = {}
    
    try:
        with open("data/service_stats.json", 'r') as f:
            service_stats = json.load(f)
        print(f"✅ Loaded service_stats.json: {len(service_stats)} prospects with service data")
    except FileNotFoundError:
        print("❌ service_stats.json not found")
        service_stats = {}
    
    # Filter prospects from combined players
    prospects = [p for p in combined_players if p.get('player_type') == 'Farm']
    print(f"📊 Total prospects in combined data: {len(prospects)}")
    
    # Analyze the gaps
    prospects_with_upid = [p for p in prospects if p.get('upid')]
    print(f"📊 Prospects with UPID: {len(prospects_with_upid)}")
    
    prospects_with_mlb_id = []
    prospects_without_mlb_id = []
    
    for prospect in prospects_with_upid:
        upid = str(prospect.get('upid', ''))
        if upid in mlb_cache and mlb_cache[upid].get('mlb_id'):
            prospects_with_mlb_id.append(prospect)
        else:
            prospects_without_mlb_id.append(prospect)
    
    print(f"📊 Prospects with MLB ID mapping: {len(prospects_with_mlb_id)}")
    print(f"📊 Prospects WITHOUT MLB ID mapping: {len(prospects_without_mlb_id)}")
    
    # Show service data coverage
    prospects_with_service_data = []
    prospects_with_mlb_id_no_service = []
    
    for prospect in prospects_with_mlb_id:
        name = prospect.get('name', '')
        if name in service_stats:
            prospects_with_service_data.append(prospect)
        else:
            prospects_with_mlb_id_no_service.append(prospect)
    
    print(f"📊 Prospects with service data: {len(prospects_with_service_data)}")
    print(f"📊 Prospects with MLB ID but NO service data: {len(prospects_with_mlb_id_no_service)}")
    
    print(f"\n🔍 Data Flow Analysis:")
    print(f"   Total Prospects: {len(prospects)}")
    print(f"   ↳ Have UPID: {len(prospects_with_upid)} ({len(prospects_with_upid)/len(prospects)*100:.1f}%)")
    print(f"   ↳ Have MLB ID: {len(prospects_with_mlb_id)} ({len(prospects_with_mlb_id)/len(prospects)*100:.1f}%)")
    print(f"   ↳ Have Service Data: {len(prospects_with_service_data)} ({len(prospects_with_service_data)/len(prospects)*100:.1f}%)")
    
    # Show sample of missing data
    print(f"\n🔍 Sample prospects WITHOUT MLB ID mapping:")
    for i, prospect in enumerate(prospects_without_mlb_id[:10]):
        upid = prospect.get('upid', 'No UPID')
        name = prospect.get('name', 'No Name')
        manager = prospect.get('manager', 'No Manager')
        print(f"   {i+1}. {name} (UPID: {upid}) - {manager}")
    
    if len(prospects_without_mlb_id) > 10:
        print(f"   ... and {len(prospects_without_mlb_id) - 10} more")
    
    print(f"\n🔍 Sample prospects with MLB ID but NO service data:")
    for i, prospect in enumerate(prospects_with_mlb_id_no_service[:10]):
        upid = prospect.get('upid', 'No UPID')
        name = prospect.get('name', 'No Name')
        manager = prospect.get('manager', 'No Manager')
        mlb_id = mlb_cache.get(str(upid), {}).get('mlb_id', 'Unknown')
        print(f"   {i+1}. {name} (UPID: {upid}, MLB ID: {mlb_id}) - {manager}")
    
    if len(prospects_with_mlb_id_no_service) > 10:
        print(f"   ... and {len(prospects_with_mlb_id_no_service) - 10} more")
    
    # Contract type breakdown
    print(f"\n📊 Contract Type Breakdown:")
    contract_counts = {}
    for prospect in prospects:
        contract = prospect.get('years_simple', 'Unknown')
        contract_counts[contract] = contract_counts.get(contract, 0) + 1
    
    for contract, count in sorted(contract_counts.items()):
        print(f"   {contract}: {count}")
    
    # Manager breakdown
    print(f"\n📊 Manager Breakdown (Top 10):")
    manager_counts = {}
    for prospect in prospects:
        manager = prospect.get('manager', 'Unknown')
        manager_counts[manager] = manager_counts.get(manager, 0) + 1
    
    for manager, count in sorted(manager_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"   {manager}: {count}")
    
    print(f"\n💡 Recommendations:")
    
    if len(prospects_without_mlb_id) > 100:
        print(f"   1. Run build_mlb_id_cache.py to map more UPIDs to MLB IDs")
        print(f"      - {len(prospects_without_mlb_id)} prospects need MLB ID mapping")
    
    if len(prospects_with_mlb_id_no_service) > 50:
        print(f"   2. Run flagged_service_tracker.py to get service data")
        print(f"      - {len(prospects_with_mlb_id_no_service)} prospects have MLB IDs but no service data")
        print(f"      - This might be because they haven't appeared in MLB yet")
    
    print(f"   3. Consider showing prospects without service data in the sheet")
    print(f"      - They could display as 'No MLB Activity' or 'Minors Only'")
    
    return {
        'total_prospects': len(prospects),
        'with_upid': len(prospects_with_upid),
        'with_mlb_id': len(prospects_with_mlb_id),
        'with_service_data': len(prospects_with_service_data),
        'missing_mlb_id': prospects_without_mlb_id,
        'missing_service_data': prospects_with_mlb_id_no_service
    }

if __name__ == "__main__":
    results = diagnose_missing_prospects()