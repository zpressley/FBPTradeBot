"""
Bot Keeper Data Manager
Tracks keeper rosters during offseason when Yahoo is not authoritative
Handles trades, keeper decisions, contract changes
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

class BotKeeperManager:
    """
    Manages keeper rosters during offseason
    This becomes the source of truth from post-playoffs until Week 1
    """
    
    def __init__(self):
        self.keeper_file = "data/bot_keepers.json"
        self.transaction_log = "data/keeper_transactions.json"
        self.keepers = self.load_keepers()
        self.transactions = self.load_transactions()
    
    def load_keepers(self) -> Dict:
        """Load current keeper data"""
        if os.path.exists(self.keeper_file):
            with open(self.keeper_file, 'r') as f:
                return json.load(f)
        
        # Initialize from last year's Yahoo rosters
        return self.initialize_from_yahoo()
    
    def initialize_from_yahoo(self) -> Dict:
        """
        Initialize keeper tracking from Yahoo rosters
        Called once at start of offseason
        """
        print("🔄 Initializing keeper tracking from Yahoo rosters...")
        
        try:
            with open("data/yahoo_players.json", 'r') as f:
                yahoo_data = json.load(f)
            
            keepers = {}
            
            for manager, players in yahoo_data.items():
                keepers[manager] = []
                
                for player in players:
                    # Convert Yahoo roster to keeper format
                    keeper_entry = {
                        "name": player["name"],
                        "position": player["position"],
                        "team": player["team"],
                        "yahoo_id": player["yahoo_id"],
                        "contract_type": "TC(1)",  # Default for new keepers
                        "years_simple": "3",
                        "salary": 15,  # Base TC salary
                        "acquired_date": datetime.now().isoformat(),
                        "source": "yahoo_offseason_freeze"
                    }
                    
                    keepers[manager].append(keeper_entry)
            
            self.save_keepers(keepers)
            print(f"✅ Initialized keepers for {len(keepers)} teams")
            
            return keepers
            
        except FileNotFoundError:
            print("❌ No Yahoo data found to initialize from")
            return {}
    
    def load_transactions(self) -> List[Dict]:
        """Load transaction history"""
        if os.path.exists(self.transaction_log):
            with open(self.transaction_log, 'r') as f:
                return json.load(f)
        return []
    
    def save_keepers(self, keepers: Dict = None):
        """Save keeper data"""
        if keepers is None:
            keepers = self.keepers
        
        with open(self.keeper_file, 'w') as f:
            json.dump(keepers, f, indent=2)
    
    def save_transactions(self):
        """Save transaction log"""
        with open(self.transaction_log, 'w') as f:
            json.dump(self.transactions, f, indent=2)
    
    def log_transaction(self, transaction_type: str, details: Dict):
        """Log a keeper transaction"""
        transaction = {
            "timestamp": datetime.now().isoformat(),
            "type": transaction_type,
            "details": details
        }
        
        self.transactions.append(transaction)
        self.save_transactions()
    
    def process_trade(self, trade_data: Dict):
        """
        Process a keeper trade during offseason
        
        trade_data format:
        {
            "team1": "WIZ",
            "team1_gives": ["Player Name", "Player Name"],
            "team2": "B2J",
            "team2_gives": ["Player Name"],
            "wizbucks": {"WIZ": 10, "B2J": 0}
        }
        """
        print(f"🔄 Processing trade: {trade_data['team1']} ↔ {trade_data['team2']}")
        
        team1 = trade_data["team1"]
        team2 = trade_data["team2"]
        
        # Remove players from giving teams
        for player_name in trade_data["team1_gives"]:
            self.remove_player(team1, player_name)
            self.add_player(team2, player_name, source="trade")
        
        for player_name in trade_data["team2_gives"]:
            self.remove_player(team2, player_name)
            self.add_player(team1, player_name, source="trade")
        
        # Log the trade
        self.log_transaction("trade", trade_data)
        
        self.save_keepers()
        print("✅ Trade processed")
    
    def remove_player(self, manager: str, player_name: str):
        """Remove a player from a manager's keepers"""
        if manager not in self.keepers:
            return False
        
        self.keepers[manager] = [
            p for p in self.keepers[manager]
            if p["name"].lower() != player_name.lower()
        ]
        
        return True
    
    def add_player(self, manager: str, player_name: str, 
                   source: str = "manual", **kwargs):
        """Add a player to a manager's keepers"""
        if manager not in self.keepers:
            self.keepers[manager] = []
        
        # Check if player already exists
        existing = [p for p in self.keepers[manager] 
                   if p["name"].lower() == player_name.lower()]
        
        if existing:
            print(f"⚠️ {player_name} already on {manager}'s roster")
            return False
        
        player_entry = {
            "name": player_name,
            "acquired_date": datetime.now().isoformat(),
            "source": source,
            **kwargs
        }
        
        self.keepers[manager].append(player_entry)
        return True
    
    def update_contract(self, manager: str, player_name: str, 
                       new_contract: str, new_salary: int):
        """Update a player's contract (keeper deadline decisions)"""
        if manager not in self.keepers:
            return False
        
        for player in self.keepers[manager]:
            if player["name"].lower() == player_name.lower():
                old_contract = player.get("contract_type", "Unknown")
                old_salary = player.get("salary", 0)
                
                player["contract_type"] = new_contract
                player["salary"] = new_salary
                player["years_simple"] = self.calculate_years_remaining(new_contract)
                
                self.log_transaction("contract_update", {
                    "manager": manager,
                    "player": player_name,
                    "old_contract": old_contract,
                    "new_contract": new_contract,
                    "old_salary": old_salary,
                    "new_salary": new_salary
                })
                
                return True
        
        return False
    
    def calculate_years_remaining(self, contract: str) -> str:
        """Calculate years remaining based on contract type"""
        # TC(R) = 4 years, TC(1) = 3 years, etc.
        if "R" in contract:
            return "4"
        elif "TC(1)" in contract:
            return "3"
        elif "TC(2)" in contract:
            return "2"
        elif "TC(3)" in contract:
            return "1"
        elif "VC(1)" in contract:
            return "2"
        elif "VC(2)" in contract:
            return "1"
        elif "FC" in contract:
            return "F"  # Franchise = until not renewed
        else:
            return "?"
    
    def apply_keeper_deadline_decisions(self, manager: str, 
                                       kept_players: List[str],
                                       il_tags: Dict[str, bool]):
        """
        Apply manager's keeper deadline decisions
        
        kept_players: List of player names being kept
        il_tags: Dict mapping player names to IL tag status
        """
        print(f"📋 Processing keeper deadline for {manager}")
        
        if manager not in self.keepers:
            print(f"❌ Manager {manager} not found")
            return
        
        # Mark unkept players
        unkept = []
        for player in self.keepers[manager]:
            player_name = player["name"]
            
            if player_name not in kept_players:
                # Player not kept - goes to draft pool
                unkept.append(player_name)
            else:
                # Player kept - advance contract
                self.advance_contract(manager, player_name)
                
                # Apply IL tag if applicable
                if il_tags.get(player_name, False):
                    player["il_tag"] = True
                    player["salary"] = self.apply_il_discount(
                        player["salary"],
                        player["contract_type"]
                    )
        
        # Remove unkept players
        self.keepers[manager] = [
            p for p in self.keepers[manager]
            if p["name"] not in unkept
        ]
        
        self.log_transaction("keeper_deadline", {
            "manager": manager,
            "kept": len(kept_players),
            "unkept": unkept,
            "il_tags": il_tags
        })
        
        self.save_keepers()
        print(f"✅ {manager}: {len(kept_players)} keepers, {len(unkept)} to draft")
    
    def advance_contract(self, manager: str, player_name: str):
        """Advance a player's contract when kept"""
        for player in self.keepers[manager]:
            if player["name"].lower() == player_name.lower():
                current = player.get("contract_type", "TC(1)")
                
                # Contract advancement logic
                if "TC(R)" in current:
                    new_contract = "TC(1)"
                    new_salary = 15
                elif "TC(1)" in current:
                    new_contract = "TC(2)"
                    new_salary = 15
                elif "TC(2)" in current:
                    new_contract = "TC(3)"
                    new_salary = 15
                elif "TC(3)" in current:
                    new_contract = "VC(1)"
                    new_salary = 35
                elif "VC(1)" in current:
                    new_contract = "VC(2)"
                    new_salary = 55
                elif "VC(2)" in current:
                    new_contract = "FC(1)"
                    new_salary = 85
                elif "FC" in current:
                    # Franchise advances
                    num = int(current.split("(")[1].split(")")[0])
                    new_contract = f"FC({num+1})"
                    new_salary = 125 if num >= 2 else 85
                else:
                    return  # Unknown contract
                
                player["contract_type"] = new_contract
                player["salary"] = new_salary
                player["years_simple"] = self.calculate_years_remaining(new_contract)
                
                return
    
    def apply_il_discount(self, base_salary: int, contract_type: str) -> int:
        """Apply IL tag discount to salary"""
        if "TC" in contract_type:
            return max(5, base_salary - 10)  # Min $5
        elif "VC" in contract_type:
            return max(20, base_salary - 15)
        elif "FC" in contract_type:
            return max(50, base_salary - 35)
        return base_salary
    
    def export_for_combined_players(self) -> Dict:
        """
        Export keeper data in format for combined_players.json
        """
        export_data = {}
        
        for manager, players in self.keepers.items():
            export_data[manager] = []
            
            for player in players:
                export_data[manager].append({
                    "name": player["name"],
                    "position": player.get("position", ""),
                    "team": player.get("team", ""),
                    "manager": manager,
                    "player_type": "MLB",
                    "contract_type": player.get("contract_type", ""),
                    "years_simple": player.get("years_simple", ""),
                    "salary": player.get("salary", 0),
                    "yahoo_id": player.get("yahoo_id", ""),
                    "il_tag": player.get("il_tag", False)
                })
        
        return export_data
    
    def get_team_summary(self, manager: str) -> Dict:
        """Get summary stats for a team"""
        if manager not in self.keepers:
            return {}
        
        players = self.keepers[manager]
        
        total_salary = sum(p.get("salary", 0) for p in players)
        
        # Count by contract tier
        tc_count = len([p for p in players if "TC" in p.get("contract_type", "")])
        vc_count = len([p for p in players if "VC" in p.get("contract_type", "")])
        fc_count = len([p for p in players if "FC" in p.get("contract_type", "")])
        
        return {
            "manager": manager,
            "total_keepers": len(players),
            "total_salary": total_salary,
            "tc_count": tc_count,
            "vc_count": vc_count,
            "fc_count": fc_count,
            "roster_spots_used": len(players),
            "roster_spots_remaining": 26 - len(players)
        }


def main():
    """Demo the bot keeper manager"""
    manager = BotKeeperManager()
    
    print("🤖 BOT KEEPER MANAGER")
    print("=" * 50)
    
    # Show all teams
    for team_abbr in manager.keepers.keys():
        summary = manager.get_team_summary(team_abbr)
        print(f"\n{team_abbr}:")
        print(f"  Keepers: {summary['total_keepers']}/26")
        print(f"  Salary: ${summary['total_salary']}")
        print(f"  Contracts: {summary['tc_count']} TC, {summary['vc_count']} VC, {summary['fc_count']} FC")


if __name__ == "__main__":
    main()
