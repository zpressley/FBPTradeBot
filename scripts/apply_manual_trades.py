#!/usr/bin/env python3
"""
Apply three manually approved trades that failed to commit due to git errors.

Trade 1: WIZ ↔ DRO (Players + Picks + WB)
Trade 2: JEP ↔ DMN (Players only)  
Trade 3: DRO ↔ DMN (Players only)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# File paths
COMBINED_PLAYERS = Path("data/combined_players.json")
DRAFT_ORDER = Path("data/draft_order_2026.json")
WIZBUCKS = Path("data/wizbucks.json")
PLAYER_LOG = Path("data/player_log.json")

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def find_player_by_name(players, name):
    """Find player by exact name match."""
    for p in players:
        if p.get('name') == name:
            return p
    return None

def find_pick(picks, draft_type, round_num, pick_num, team):
    """Find a specific draft pick."""
    for p in picks:
        if (p.get('draft') == draft_type and
            p.get('round') == round_num and
            p.get('pick') == pick_num and
            p.get('team') == team):
            return p
    return None

def log_player_change(player_name, upid, from_team, to_team, trade_id):
    """Create a player log entry."""
    return {
        "log_id": f"player_{int(datetime.now(timezone.utc).timestamp())}_{upid}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "season": 2026,
        "source": "manual_trade_application",
        "admin": "MANUAL_FIX",
        "upid": str(upid),
        "player_name": player_name,
        "owner": to_team,
        "update_type": "Trade",
        "event": f"Traded from {from_team} to {to_team} ({trade_id})",
        "changes": {
            "manager": {"from": from_team, "to": to_team},
            "FBP_Team": {"from": from_team, "to": to_team}
        }
    }

def apply_trades():
    # Load data
    print("Loading data files...")
    players = load_json(COMBINED_PLAYERS)
    picks = load_json(DRAFT_ORDER)
    wizbucks = load_json(WIZBUCKS)
    player_log = load_json(PLAYER_LOG)
    
    # ========== TRADE 1: WIZ ↔ DRO ==========
    print("\n=== Applying Trade 1: WIZ ↔ DRO ===")
    trade_id_1 = "WEB-TRADE-20260218-214217"
    
    # WIZ receives from DRO
    maikel = find_player_by_name(players, "Maikel Garcia")
    erik = find_player_by_name(players, "Erik Sabrowski")
    
    if maikel:
        print(f"  ✓ Maikel Garcia: DRO → WIZ")
        maikel['manager'] = "Whiz Kids"
        maikel['FBP_Team'] = "WIZ"
        player_log.append(log_player_change("Maikel Garcia", maikel['upid'], "DRO", "WIZ", trade_id_1))
    else:
        print("  ✗ Maikel Garcia not found!")
    
    if erik:
        print(f"  ✓ Erik Sabrowski: DRO → WIZ")
        erik['manager'] = "Whiz Kids"
        erik['FBP_Team'] = "WIZ"
        player_log.append(log_player_change("Erik Sabrowski", erik['upid'], "DRO", "WIZ", trade_id_1))
    else:
        print("  ✗ Erik Sabrowski not found!")
    
    # DRO receives from WIZ
    leodalis = find_player_by_name(players, "Leodalis De Vries")
    
    if leodalis:
        print(f"  ✓ Leodalis De Vries: WIZ → DRO")
        leodalis['manager'] = "Andromedans"
        leodalis['FBP_Team'] = "DRO"
        player_log.append(log_player_change("Leodalis De Vries", leodalis['upid'], "WIZ", "DRO", trade_id_1))
    else:
        print("  ✗ Leodalis De Vries not found!")
    
    # Swap keeper picks: DRO R4 P46 → WIZ, WIZ R10 P120 → DRO
    dro_pick = find_pick(picks, 'keeper', 4, 46, 'DRO')
    wiz_pick = find_pick(picks, 'keeper', 10, 120, 'WIZ')
    
    if dro_pick:
        print(f"  ✓ Keeper R4 P46: DRO → WIZ")
        dro_pick['team'] = 'WIZ'
    else:
        print("  ✗ DRO R4 P46 not found!")
    
    if wiz_pick:
        print(f"  ✓ Keeper R10 P120: WIZ → DRO")
        wiz_pick['team'] = 'DRO'
    else:
        print("  ✗ WIZ R10 P120 not found!")
    
    # WizBucks: DRO sends $15 to WIZ
    print(f"  ✓ WizBucks: DRO -$15, WIZ +$15")
    wizbucks["Andromedans"] = wizbucks.get("Andromedans", 0) - 15
    wizbucks["Whiz Kids"] = wizbucks.get("Whiz Kids", 0) + 15
    
    # ========== TRADE 2: JEP ↔ DMN ==========
    print("\n=== Applying Trade 2: JEP ↔ DMN ===")
    trade_id_2 = "WEB-TRADE-20260218-215218"
    
    # JEP receives from DMN
    theo = find_player_by_name(players, "Theo Gillen")
    enrique = find_player_by_name(players, "Enrique Bradfield Jr.")
    michael = find_player_by_name(players, "Michael Arroyo")
    
    if theo:
        print(f"  ✓ Theo Gillen: DMN → JEP")
        theo['manager'] = "Jeppie Torrs"
        theo['FBP_Team'] = "JEP"
        player_log.append(log_player_change("Theo Gillen", theo['upid'], "DMN", "JEP", trade_id_2))
    else:
        print("  ✗ Theo Gillen not found!")
    
    if enrique:
        print(f"  ✓ Enrique Bradfield Jr.: DMN → JEP")
        enrique['manager'] = "Jeppie Torrs"
        enrique['FBP_Team'] = "JEP"
        player_log.append(log_player_change("Enrique Bradfield Jr.", enrique['upid'], "DMN", "JEP", trade_id_2))
    else:
        print("  ✗ Enrique Bradfield Jr. not found!")
    
    if michael:
        print(f"  ✓ Michael Arroyo: DMN → JEP")
        michael['manager'] = "Jeppie Torrs"
        michael['FBP_Team'] = "JEP"
        player_log.append(log_player_change("Michael Arroyo", michael['upid'], "DMN", "JEP", trade_id_2))
    else:
        print("  ✗ Michael Arroyo not found!")
    
    # DMN receives from JEP
    luis_gil = find_player_by_name(players, "Luis Gil")
    
    if luis_gil:
        print(f"  ✓ Luis Gil: JEP → DMN")
        luis_gil['manager'] = "Damn Yanks"
        luis_gil['FBP_Team'] = "DMN"
        player_log.append(log_player_change("Luis Gil", luis_gil['upid'], "JEP", "DMN", trade_id_2))
    else:
        print("  ✗ Luis Gil not found!")
    
    # ========== TRADE 3: DRO ↔ DMN ==========
    print("\n=== Applying Trade 3: DRO ↔ DMN ===")
    trade_id_3 = "WEB-TRADE-20260218-215718"
    
    # DRO receives from DMN
    jac = find_player_by_name(players, "Jac Caglianone")
    roman = find_player_by_name(players, "Roman Anthony")
    blake = find_player_by_name(players, "Blake Snell")
    
    if jac:
        print(f"  ✓ Jac Caglianone: DMN → DRO")
        jac['manager'] = "Andromedans"
        jac['FBP_Team'] = "DRO"
        player_log.append(log_player_change("Jac Caglianone", jac['upid'], "DMN", "DRO", trade_id_3))
    else:
        print("  ✗ Jac Caglianone not found!")
    
    if roman:
        print(f"  ✓ Roman Anthony: DMN → DRO")
        roman['manager'] = "Andromedans"
        roman['FBP_Team'] = "DRO"
        player_log.append(log_player_change("Roman Anthony", roman['upid'], "DMN", "DRO", trade_id_3))
    else:
        print("  ✗ Roman Anthony not found!")
    
    if blake:
        print(f"  ✓ Blake Snell: DMN → DRO")
        blake['manager'] = "Andromedans"
        blake['FBP_Team'] = "DRO"
        player_log.append(log_player_change("Blake Snell", blake['upid'], "DMN", "DRO", trade_id_3))
    else:
        print("  ✗ Blake Snell not found!")
    
    # DMN receives from DRO
    aaron = find_player_by_name(players, "Aaron Judge")
    daulton = find_player_by_name(players, "Daulton Varsho")
    
    if aaron:
        print(f"  ✓ Aaron Judge: DRO → DMN")
        aaron['manager'] = "Damn Yanks"
        aaron['FBP_Team'] = "DMN"
        player_log.append(log_player_change("Aaron Judge", aaron['upid'], "DRO", "DMN", trade_id_3))
    else:
        print("  ✗ Aaron Judge not found!")
    
    if daulton:
        print(f"  ✓ Daulton Varsho: DRO → DMN")
        daulton['manager'] = "Damn Yanks"
        daulton['FBP_Team'] = "DMN"
        player_log.append(log_player_change("Daulton Varsho", daulton['upid'], "DRO", "DMN", trade_id_3))
    else:
        print("  ✗ Daulton Varsho not found!")
    
    # Save all data
    print("\n=== Saving changes ===")
    save_json(COMBINED_PLAYERS, players)
    print(f"  ✓ Saved {COMBINED_PLAYERS}")
    
    save_json(DRAFT_ORDER, picks)
    print(f"  ✓ Saved {DRAFT_ORDER}")
    
    save_json(WIZBUCKS, wizbucks)
    print(f"  ✓ Saved {WIZBUCKS}")
    
    save_json(PLAYER_LOG, player_log)
    print(f"  ✓ Saved {PLAYER_LOG}")
    
    print("\n✅ All trades applied successfully!")
    print("\nNext steps:")
    print("  1. Review changes with: git diff")
    print("  2. Commit: git add data/ && git commit -m 'Manual trade application: 3 trades'")
    print("  3. Push: git push")

if __name__ == "__main__":
    apply_trades()
