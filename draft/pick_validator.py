"""
Pick Validator - Draft rule validation
Validates picks against FBP rules for protected/unprotected rounds,
ownership status (PC/FC/UC), and availability
"""

from typing import Dict, Tuple, Optional
from difflib import get_close_matches


class PickValidator:
    """
    Validates draft picks against FBP rules.
    
    Rules:
    - Protected rounds (1-6): Can only pick UC or own PC/FC
    - Unprotected rounds (7+): Can pick any available player
    - No duplicate picks
    """
    
    def __init__(self, prospect_database, draft_manager):
        """
        Args:
            prospect_database: ProspectDatabase instance
            draft_manager: DraftManager instance
        """
        self.db = prospect_database
        self.draft = draft_manager
        
        # Round type rules
        self.PROTECTED_ROUNDS = [1, 2, 3, 4, 5, 6]
        self.UNPROTECTED_ROUNDS_START = 7
    
    def validate_pick(
        self, 
        team: str, 
        player_input: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Validate a pick against all rules.
        
        Args:
            team: Team making the pick (e.g. "WIZ")
            player_input: Player name as typed by manager
            
        Returns:
            (valid: bool, message: str, player_data: Optional[Dict])
            
        Examples:
            (True, "Valid pick", {...player data...})
            (False, "Player already drafted by HAM", None)
            (False, "Protected player owned by another team", None)
        """
        current_pick = self.draft.get_current_pick()
        
        if current_pick is None:
            return False, "Draft is complete", None
        
        round_num = current_pick["round"]
        round_type = current_pick["round_type"]
        
        # 1. Find player in database
        player = self._find_player(player_input)
        if player is None:
            # Try fuzzy match
            suggestion = self._suggest_player(player_input)
            if suggestion:
                return False, f"Player not found. Did you mean: {suggestion}?", None
            return False, f"Player '{player_input}' not found in database", None
        
        # 2. Check if already drafted
        is_drafted, drafted_by = self.draft.is_player_drafted(player["name"])
        if is_drafted:
            return False, f"❌ {player['name']} already drafted by {drafted_by}", None
        
        # 3. Check protected/unprotected rules
        if self.is_protected_round(round_num):
            valid, msg = self._validate_protected_pick(team, player)
            if not valid:
                return False, msg, player
        
        # If we get here, pick is valid
        return True, f"✅ Valid pick: {player['name']}", player
    
    def _find_player(self, player_input: str) -> Optional[Dict]:
        """
        Find player in database by name (case-insensitive exact match).
        
        Args:
            player_input: Player name as typed
            
        Returns:
            Player dict or None if not found
        """
        # Exact match (case insensitive)
        for name, player in self.db.prospects.items():
            if name.lower() == player_input.lower():
                return player
        
        return None
    
    def _suggest_player(self, player_input: str) -> Optional[str]:
        """
        Suggest closest match using fuzzy matching.
        
        Returns:
            Suggested player name or None
        """
        all_names = list(self.db.prospects.keys())
        matches = get_close_matches(player_input, all_names, n=1, cutoff=0.7)
        
        return matches[0] if matches else None
    
    def is_protected_round(self, round_num: int) -> bool:
        """Check if round is protected (1-6) or unprotected (7+)"""
        return round_num in self.PROTECTED_ROUNDS
    
    def _validate_protected_pick(
        self, 
        team: str, 
        player: Dict
    ) -> Tuple[bool, str]:
        """
        Validate pick in a protected round.
        
        Protected round rules:
        - Can pick UC (uncontracted) players
        - Can pick own PC/FC players
        - Cannot pick another team's PC/FC players
        
        Returns:
            (valid: bool, message: str)
        """
        ownership_status = player.get("ownership", "UC")
        owner = player.get("owner")
        
        # UC players are always available
        if ownership_status == "UC":
            return True, "Available (UC)"
        
        # Own PC/FC players are available
        if owner == team:
            return True, f"Your own {ownership_status} player"
        
        # Other team's PC/FC players are NOT available in protected rounds
        return False, (
            f"❌ Protected player owned by {owner}\n"
            f"{player['name']} has {ownership_status} contract with {owner}\n"
            f"Available in unprotected rounds only (Round 7+)"
        )
    
    def validate_multiple_matches(
        self, 
        player_input: str
    ) -> Tuple[bool, str, Optional[list]]:
        """
        Check if input matches multiple players (ambiguous).
        
        Returns:
            (is_ambiguous: bool, message: str, matches: Optional[List[Dict]])
        """
        matches = []
        input_lower = player_input.lower()
        
        # Check for partial matches
        for name, player in self.db.prospects.items():
            if input_lower in name.lower():
                matches.append(player)
        
        if len(matches) == 0:
            return False, "No matches", None
        
        if len(matches) == 1:
            return False, "Single match", matches
        
        # Multiple matches - ambiguous
        match_names = [p["name"] for p in matches[:5]]  # Top 5
        msg = (
            f"🔍 Multiple players match '{player_input}':\n" +
            "\n".join(f"  • {name}" for name in match_names)
        )
        
        if len(matches) > 5:
            msg += f"\n  ... and {len(matches) - 5} more"
        
        return True, msg, matches
    
    def can_poach_in_unprotected(
        self, 
        team: str, 
        player: Dict
    ) -> Tuple[bool, str]:
        """
        Check if team can poach a protected player in unprotected round.
        
        In unprotected rounds, ANY player can be picked (poaching allowed).
        
        Returns:
            (can_poach: bool, message: str)
        """
        ownership_status = player.get("ownership", "UC")
        owner = player.get("owner")
        
        if ownership_status == "UC":
            return True, "Available (UC)"
        
        if owner == team:
            return True, f"Your own {ownership_status} player"
        
        # Poaching another team's PC/FC
        return True, (
            f"🏴‍☠️ POACH from {owner}\n"
            f"{player['name']} is {ownership_status} with {owner}\n"
            f"You can poach in unprotected rounds"
        )
    
    def get_validation_summary(self, team: str, player_input: str) -> Dict:
        """
        Get detailed validation info for UI display.
        
        Returns:
            Dict with all validation details
        """
        current_pick = self.draft.get_current_pick()
        
        if current_pick is None:
            return {
                "valid": False,
                "message": "Draft complete",
                "player": None
            }
        
        # Find player
        player = self._find_player(player_input)
        
        # Check for ambiguous matches
        is_ambiguous, ambig_msg, matches = self.validate_multiple_matches(player_input)
        
        if is_ambiguous:
            return {
                "valid": False,
                "ambiguous": True,
                "message": ambig_msg,
                "matches": matches,
                "player": None
            }
        
        if player is None:
            suggestion = self._suggest_player(player_input)
            return {
                "valid": False,
                "message": f"Player not found",
                "suggestion": suggestion,
                "player": None
            }
        
        # Full validation
        valid, message, player_data = self.validate_pick(team, player_input)
        
        return {
            "valid": valid,
            "message": message,
            "player": player_data,
            "round": current_pick["round"],
            "round_type": current_pick["round_type"],
            "is_protected_round": self.is_protected_round(current_pick["round"])
        }


# Testing / CLI usage
if __name__ == "__main__":
    print("🧪 Testing PickValidator")
    print("=" * 50)
    
    # Mock database for testing
    class MockProspectDB:
        def __init__(self):
            self.prospects = {
                "Jackson Chourio": {
                    "name": "Jackson Chourio",
                    "position": "CF",
                    "team": "MIL",
                    "rank": 3,
                    "ownership": "UC",
                    "owner": None
                },
                "Paul Skenes": {
                    "name": "Paul Skenes",
                    "position": "SP",
                    "team": "PIT",
                    "rank": 1,
                    "ownership": "PC",
                    "owner": "HAM"
                },
                "Kyle Teel": {
                    "name": "Kyle Teel",
                    "position": "C",
                    "team": "BOS",
                    "rank": 47,
                    "ownership": "FC",
                    "owner": "WIZ"
                }
            }
    
    # Mock draft manager
    class MockDraftManager:
        def __init__(self):
            self.picks_made = []
        
        def get_current_pick(self):
            return {
                "round": 1,
                "pick": 1,
                "team": "WIZ",
                "round_type": "protected"
            }
        
        def is_player_drafted(self, player_name):
            return False, None
    
    # Initialize
    mock_db = MockProspectDB()
    mock_draft = MockDraftManager()
    validator = PickValidator(mock_db, mock_draft)
    
    # Test cases
    print("\n📋 Test Case 1: Valid UC pick in protected round")
    valid, msg, player = validator.validate_pick("WIZ", "Jackson Chourio")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
    
    print("\n📋 Test Case 2: Own FC player in protected round")
    valid, msg, player = validator.validate_pick("WIZ", "Kyle Teel")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
    
    print("\n📋 Test Case 3: Another team's PC in protected round")
    valid, msg, player = validator.validate_pick("WIZ", "Paul Skenes")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
    
    print("\n📋 Test Case 4: Player not found")
    valid, msg, player = validator.validate_pick("WIZ", "Fake Player")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
    
    print("\n📋 Test Case 5: Fuzzy match suggestion")
    valid, msg, player = validator.validate_pick("WIZ", "Chourio")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")