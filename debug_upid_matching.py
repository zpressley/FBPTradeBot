#!/usr/bin/env python3
"""
Debug UPID matching between CSV and JSON
"""

import csv
import json

csv_path = "data/Player Database.csv"
json_path = "data/combined_players.json"

print("🔍 UPID Format Comparison")
print("=" * 70)
print()

# Load CSV UPIDs
print("📊 Loading UPIDs from CSV...")
csv_upids = {}

with open(csv_path, 'r', encoding='utf-8') as f:
    next(f)  # Skip doc row
    reader = csv.DictReader(f)
    
    for row in reader:
        upid = row.get('UPID', '').strip()
        rank = row.get('Rank/ADP', '').strip()
        name = row.get('Player Name', '').strip()
        
        if upid and rank:
            csv_upids[upid] = {
                'rank': rank,
                'name': name
            }

print(f"✅ Found {len(csv_upids)} UPIDs with ranks in CSV")
print()

# Show sample CSV UPIDs
print("Sample CSV UPIDs (first 5 with ranks):")
for i, (upid, data) in enumerate(list(csv_upids.items())[:5], 1):
    print(f"  {i}. UPID: '{upid}' (type: {type(upid).__name__}) → Rank: {data['rank']} → {data['name']}")
print()

# Load JSON UPIDs
print("📊 Loading UPIDs from JSON...")
with open(json_path, 'r') as f:
    players = json.load(f)

json_upids = {}
for player in players:
    upid = player.get('upid', '')
    if upid:
        json_upids[upid] = player.get('name', '')

print(f"✅ Found {len(json_upids)} UPIDs in combined_players.json")
print()

# Show sample JSON UPIDs
print("Sample JSON UPIDs (first 5):")
for i, (upid, name) in enumerate(list(json_upids.items())[:5], 1):
    print(f"  {i}. UPID: '{upid}' (type: {type(upid).__name__}) → {name}")
print()

# Try matching
print("🔍 Testing Matches...")
print("-" * 70)

# Test exact match
exact_matches = 0
for json_upid in list(json_upids.keys())[:10]:
    if json_upid in csv_upids:
        exact_matches += 1
        print(f"✅ MATCH: '{json_upid}' → {json_upids[json_upid]}")
    else:
        print(f"❌ NO MATCH: '{json_upid}' → {json_upids[json_upid]}")

print()
print(f"Exact matches in first 10: {exact_matches}/10")
print()

# Look for a known prospect
print("🔍 Looking for known prospects...")
known_prospects = ["Jordan Lawlar", "Charlie Condon", "Max Clark", "Brandon Sproat"]

for name in known_prospects:
    # Find in JSON
    json_player = next((p for p in players if p.get('name') == name), None)
    
    if json_player:
        json_upid = json_player.get('upid', '')
        print(f"\n{name}:")
        print(f"  JSON UPID: '{json_upid}' (type: {type(json_upid).__name__})")
        
        # Try to find in CSV
        found_in_csv = False
        for csv_upid, csv_data in csv_upids.items():
            if csv_data['name'] == name:
                print(f"  CSV UPID:  '{csv_upid}' (type: {type(csv_upid).__name__})")
                print(f"  CSV Rank:  {csv_data['rank']}")
                found_in_csv = True
                
                # Check if they match
                if str(json_upid) == str(csv_upid):
                    print(f"  ✅ UPIDs MATCH (as strings)")
                elif json_upid == csv_upid:
                    print(f"  ✅ UPIDs MATCH (exact)")
                else:
                    print(f"  ❌ UPIDs DON'T MATCH")
                    print(f"     JSON: '{json_upid}' vs CSV: '{csv_upid}'")
                break
        
        if not found_in_csv:
            print(f"  ❌ Not found in CSV by name")
            # Try UPID lookup
            if json_upid in csv_upids:
                print(f"     But UPID '{json_upid}' exists in CSV as: {csv_upids[json_upid]['name']}")
            elif str(json_upid) in csv_upids:
                print(f"     But str(UPID) '{str(json_upid)}' exists in CSV")

print()
print("=" * 70)
print("💡 This will show us the UPID format difference")