# Debug script to understand your data structure

import json
from difflib import get_close_matches

# Load the data
with open("data/combined_players.json", "r") as f:
    players = json.load(f)

with open("data/wizbucks.json", "r") as f:
    wizbucks = json.load(f)

print("=== SAMPLE PLAYER RECORDS ===")
for i, player in enumerate(players[:5]):
    print(f"Player {i+1}:")
    for key, value in player.items():
        print(f"  {key}: {value}")
    print()

print("=== WIZBUCKS BALANCES ===")
for team, balance in wizbucks.items():
    print(f"{team}: ${balance}")

print("\n=== WAR TEAM ROSTER ===")
war_players = [p for p in players if p.get("manager") == "WAR"]
print(f"WAR has {len(war_players)} players:")
for player in war_players[:10]:  # First 10 players
    name = player.get("name", "Unknown")
    pos = player.get("position", "?")
    contract = player.get("years_simple", "?")
    print(f"  {pos} {name} [{contract}]")

print("\n=== WIZ TEAM ROSTER ===")
wiz_players = [p for p in players if p.get("manager") == "WIZ"]
print(f"WIZ has {len(wiz_players)} players:")
for player in wiz_players[:10]:  # First 10 players
    name = player.get("name", "Unknown")
    pos = player.get("position", "?")
    contract = player.get("years_simple", "?")
    print(f"  {pos} {name} [{contract}]")

print("\n=== FUZZY MATCHING TEST ===")
test_names = ["Masyn Winn", "Lars Nootbaar", "masyn winn", "Lars Nootbar"]
all_names = [p["name"] for p in players]

for test_name in test_names:
    matches = get_close_matches(test_name, all_names, n=3, cutoff=0.6)
    print(f"'{test_name}' matches: {matches}")

print("\n=== CONTRACT TYPES FOUND ===")
contract_types = set()
for player in players:
    contract = player.get("years_simple", "")
    if contract:
        contract_types.add(contract)

print("Unique contract types:")
for contract in sorted(contract_types):
    print(f"  '{contract}'")