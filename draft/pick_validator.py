"""
Pick Validator - Draft rule validation for 2026 Constitution

New prospect-draft rules (2026):
- Rounds 1–2: FYPD-only, consume BC slots.
- Rounds 3+: General prospect pool, consume DC slots.
- Eligible players must be current Farm prospects (player_type == "Farm")
  with no existing BC/DC/PC contract (contract_type blank).
- No duplicate picks.

Protected/unprotected rounds and UC/FC-based rules are removed.
"""

from typing import Dict, Tuple, Optional, List
from difflib import get_close_matches


class PickValidator:
    """Validate draft picks against 2026 FBP prospect-draft rules."""
    
    def __init__(self, prospect_database, draft_manager):
        """Wire validator to ProspectDatabase and DraftManager.

        Args:
            prospect_database: ProspectDatabase instance
            draft_manager: DraftManager instance
        """
        self.db = prospect_database
        self.draft = draft_manager
        
        # FYPD-only rounds
        self.FYPD_ROUNDS = {1, 2}
    
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
        
        # 1. Find player in database
        player = self._find_player(player_input)
        if player is None:
            # Try fuzzy match (up to 4 suggestions)
            suggestions = self._suggest_players(player_input)
            if suggestions:
                formatted = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(suggestions))
                msg = (
                    "❌ Player not found.\n"
                    "Did you mean:\n"
                    f"{formatted}\n\n"
                    "If that is who you meant, just reply with the right name."
                )
                return False, msg, None
            return False, (
                f"❌ Player '{player_input}' not found in database. "
                "Reply with the full player name as it appears on the board."
            ), None
        
        # 2. Check if already drafted
        is_drafted, drafted_by = self.draft.is_player_drafted(player["name"])
        if is_drafted:
            return False, f"❌ {player['name']} already drafted by {drafted_by}", None
        
        # 3. Enforce core prospect eligibility
        ok, reason = self._is_prospect_eligible(player)
        if not ok:
            return False, f"❌ {reason}", player
        
        # 4. Enforce FYPD-only constraint in rounds 1–2
        if round_num in self.FYPD_ROUNDS and not bool(player.get("fypd")):
            return False, (
                "❌ Rounds 1–2 are FYPD-only. "
                f"{player['name']} is not in the FYPD pool."
            ), player

        # Note: BC/DC slot availability is now enforced implicitly via the
        # draft order itself. If a team has a pick in draft_order_*.json,
        # they are allowed to exercise it. We no longer block picks based
        # on derived slot counts here.

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
    
    def _suggest_players(self, player_input: str, limit: int = 4) -> List[str]:
        """Suggest close matches using fuzzy matching.

        We prefer higher-confidence matches first, then expand to "slightly close"
        matches if needed, up to `limit` total.
        """
        all_names = list(self.db.prospects.keys())

        # High-confidence matches
        close = get_close_matches(player_input, all_names, n=limit, cutoff=0.80)
        if len(close) >= limit:
            return close[:limit]

        # Slightly looser matches to fill remaining slots.
        loose = get_close_matches(player_input, all_names, n=limit, cutoff=0.65)

        # Preserve order / uniqueness.
        out: List[str] = []
        for name in close + loose:
            if name not in out:
                out.append(name)
            if len(out) >= limit:
                break
        return out

    def _suggest_player(self, player_input: str) -> Optional[str]:
        """Back-compat helper: return the top suggestion (if any)."""
        matches = self._suggest_players(player_input, limit=1)
        return matches[0] if matches else None
    
    def _is_prospect_eligible(self, player: Dict) -> Tuple[bool, str]:
        """Check that a player is an eligible prospect for this draft.

        Rules enforced here:
        - Must be a Farm prospect: player_type == "Farm".
        - Must not already have a BC/DC/PC contract: contract_type blank.
        - (Service-time / graduation limits are assumed to be baked into
          combined_players via the graduation pipeline.)
        """
        if player.get("player_type") != "Farm":
            return False, "Player is not a Farm prospect."

        contract_type = (player.get("contract_type") or "").strip()
        if contract_type:
            return False, "Player already has a prospect contract (BC/DC/PC)."

        return True, "Eligible prospect"

    def _has_available_slot(self, team: str, round_num: int) -> Tuple[bool, str]:
        """Check that the team has an available BC/DC slot for this round.

        DraftManager.state.team_slots structure:
        {
            "TEAM": {"bc_slots": int, "dc_slots": int, "bc_used": int, "dc_used": int},
            ...
        }
        """
        state = getattr(self.draft, "state", {}) or {}
        team_slots = state.get("team_slots") or {}
        info = team_slots.get(team)

        # If no slot info exists (e.g. non-prospect drafts or legacy state),
        # treat as unlimited to avoid hard failures.
        if not info:
            return True, "No slot limits configured for team."

        if round_num in self.FYPD_ROUNDS:
            bc_slots = info.get("bc_slots", 0)
            bc_used = info.get("bc_used", 0)
            if bc_used >= bc_slots:
                return False, f"No BC slots remaining for {team} (used {bc_used}/{bc_slots})."
            return True, "BC slot available"

        # Rounds 3+ use DC slots
        dc_slots = info.get("dc_slots", 0)
        dc_used = info.get("dc_used", 0)
        if dc_used >= dc_slots:
            return False, f"No DC slots remaining for {team} (used {dc_used}/{dc_slots})."
        return True, "DC slot available"
    
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
            suggestions = self._suggest_players(player_input)
            suggestion = suggestions[0] if suggestions else None
            return {
                "valid": False,
                "message": "Player not found",
                # Back-compat: keep the singular suggestion field.
                "suggestion": suggestion,
                # New: expose multiple candidates for richer UIs.
                "suggestions": suggestions,
                "player": None,
            }
        
        # Full validation
        valid, message, player_data = self.validate_pick(team, player_input)
        
        return {
            "valid": valid,
            "message": message,
            "player": player_data,
            "round": current_pick["round"],
            # Round type is now implicit: R1–2 = FYPD/BC, R3+ = prospect/DC.
            "round_type": "fypd" if current_pick["round"] in self.FYPD_ROUNDS else "prospect",
        }


# Testing / CLI usage
if __name__ == "__main__":
    print("🧪 Testing PickValidator (2026 rules)")
    print("=" * 50)
    
    # Mock database for testing
    class MockProspectDB:
        def __init__(self):
            self.prospects = {
                "FYPD Prospect": {
                    "name": "FYPD Prospect",
                    "player_type": "Farm",
                    "contract_type": "",
                    "fypd": True,
                },
                "Existing DC Prospect": {
                    "name": "Existing DC Prospect",
                    "player_type": "Farm",
                    "contract_type": "Development Cont.",
                    "fypd": False,
                },
                "Non-FYPD Prospect": {
                    "name": "Non-FYPD Prospect",
                    "player_type": "Farm",
                    "contract_type": "",
                    "fypd": False,
                },
            }
    
    # Mock draft manager
    class MockDraftManager:
        def __init__(self):
            self.picks_made = []
            self.state = {
                "team_slots": {
                    "WIZ": {"bc_slots": 1, "dc_slots": 1, "bc_used": 0, "dc_used": 0}
                }
            }
        
        def get_current_pick(self):
            # Round will be overridden in individual tests
            return {"round": 1, "pick": 1, "team": "WIZ"}
        
        def is_player_drafted(self, player_name):
            return False, None
    
    mock_db = MockProspectDB()
    mock_draft = MockDraftManager()
    validator = PickValidator(mock_db, mock_draft)
    
    print("\n📋 Test Case 1: FYPD prospect in Round 1 (BC)")
    mock_draft.state["team_slots"]["WIZ"]["bc_used"] = 0
    valid, msg, _ = validator.validate_pick("WIZ", "FYPD Prospect")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
    
    print("\n📋 Test Case 2: Non-FYPD prospect in Round 1 (should fail)")
    valid, msg, _ = validator.validate_pick("WIZ", "Non-FYPD Prospect")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
    
    print("\n📋 Test Case 3: Existing DC prospect (should fail)")
    valid, msg, _ = validator.validate_pick("WIZ", "Existing DC Prospect")
    print(f"   Result: {valid}")
    print(f"   Message: {msg}")
