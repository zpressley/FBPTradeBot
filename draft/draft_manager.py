"""
Draft Manager - Core draft logic and state management
Handles draft flow, pick tracking, and state persistence
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class DraftManager:
    """
    Manages draft state and flow for FBP drafts.
    Loads custom draft order, tracks picks, handles state persistence.
    """
    
    def __init__(self, draft_type: str = "prospect", season: int = 2026):
        self.draft_type = draft_type
        self.season = season
        
        # File paths
        self.order_file = f"data/draft_order_{season}.json"
        self.state_file = f"data/draft_state_{draft_type}_{season}.json"
        
        # Load draft configuration
        self.draft_order = self.load_draft_order()
        
        # Load or initialize state
        self.state = self.load_or_init_state()
        
        # Initialize per-team slot limits/usage for prospect drafts.
        # For now we derive BC/DC slot limits directly from the draft order:
        # - Rounds 1–2: BC slots (FYPD-only rounds)
        # - Rounds 3+: DC slots (general prospect rounds)
        if self.draft_type == "prospect":
            self._init_team_slots_from_order()
        
        # Current position in draft
        self.current_pick_index = self.state.get("current_pick_index", 0)
        
    def load_draft_order(self) -> List[Dict]:
        """
        Load custom draft order from JSON file.
        
        Format: [
            {"round": 1, "pick": 1, "team": "WIZ", "round_type": "protected"},
            {"round": 1, "pick": 2, "team": "B2J", "round_type": "protected"},
            ...
        ]
        
        Returns:
            List of pick dictionaries in order
        """
        if not os.path.exists(self.order_file):
            raise FileNotFoundError(
                f"Draft order file not found: {self.order_file}\n"
                f"Please create this file with your custom draft order."
            )
        
        with open(self.order_file, 'r') as f:
            data = json.load(f)
        
        # Extract picks array (handles both formats)
        picks = data.get("picks", data.get("rounds", []))
        
        if not picks:
            raise ValueError(f"No picks found in {self.order_file}")
        
        print(f"✅ Loaded draft order: {len(picks)} total picks")
        
        # Validate order
        self._validate_draft_order(picks)
        
        return picks
    
    def _validate_draft_order(self, picks: List[Dict]) -> None:
        """Validate draft order structure and completeness"""
        
        # Check required fields
        required_fields = ["round", "pick", "team", "round_type"]
        for i, pick in enumerate(picks):
            for field in required_fields:
                if field not in pick:
                    raise ValueError(
                        f"Pick {i+1} missing required field: {field}"
                    )
        
        # Check pick numbers are sequential
        for i, pick in enumerate(picks):
            expected_pick = i + 1
            if pick["pick"] != expected_pick:
                raise ValueError(
                    f"Pick numbers not sequential: expected {expected_pick}, "
                    f"got {pick['pick']} at index {i}"
                )
        
        # Count picks by team
        team_counts = {}
        for pick in picks:
            team = pick["team"]
            team_counts[team] = team_counts.get(team, 0) + 1
        
        print(f"📊 Pick distribution:")
        for team, count in sorted(team_counts.items()):
            print(f"   {team}: {count} picks")
    
    def load_or_init_state(self) -> Dict:
        """
        Load existing draft state or initialize new one.
        
        State includes:
        - status: 'not_started', 'active', 'paused', 'completed'
        - current_pick_index: index in draft_order
        - picks_made: list of completed picks
        - timer_started_at: timestamp when current pick timer started
        - paused_at: timestamp when draft was paused
        """
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            print(f"✅ Loaded draft state: {state.get('status', 'unknown')}")
            return state
        
        # Initialize new state
        state = {
            "status": "not_started",
            "draft_type": self.draft_type,
            "season": self.season,
            "current_pick_index": 0,
            "picks_made": [],
            "timer_started_at": None,
            "paused_at": None,
            "started_at": None,
            "completed_at": None,
            # Per-team slot usage for prospect drafts (populated via
            # _init_team_slots_from_order for draft_type == "prospect").
            # Structure:
            # "team_slots": {
            #   "WIZ": {"bc_slots": 2, "dc_slots": 5, "bc_used": 0, "dc_used": 0},
            #   ...
            # }
            "team_slots": {}
        }
        
        self.save_state(state)
        print(f"✅ Initialized new draft state")
        return state
    
    def save_state(self, state: Optional[Dict] = None) -> None:
        """Persist draft state to disk"""
        if state is None:
            state = self.state
        
        # Ensure data directory exists
        os.makedirs("data", exist_ok=True)
        
        # Update timestamp
        state["last_updated"] = datetime.now().isoformat()
        
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def get_current_pick(self) -> Optional[Dict]:
        """
        Get information about the current pick.
        
        Returns:
            Dict with: round, pick, team, round_type, notes
            None if draft is complete
        """
        if self.current_pick_index >= len(self.draft_order):
            return None  # Draft complete
        
        return self.draft_order[self.current_pick_index].copy()
    
    def get_next_pick(self) -> Optional[Dict]:
        """Get information about the next pick (on deck)"""
        next_index = self.current_pick_index + 1
        if next_index >= len(self.draft_order):
            return None
        
        return self.draft_order[next_index].copy()
    
    def get_pick_after_next(self) -> Optional[Dict]:
        """Get information about pick after next (in the hole)"""
        next_index = self.current_pick_index + 2
        if next_index >= len(self.draft_order):
            return None
        
        return self.draft_order[next_index].copy()
    
    def _init_team_slots_from_order(self) -> None:
        """Initialize per-team BC/DC slot limits from draft order if missing.

        We derive effective slot counts from the number of picks each team
        has in rounds 1–2 (BC slots) and rounds 3+ (DC slots). This keeps the
        draft engine self-contained: PAD purchases are reflected in the
        custom draft order, and we mirror that here for validation and UI.
        """
        team_slots = self.state.get("team_slots") or {}

        # Only derive limits if they are not already present (e.g., from a
        # previous run or an external PAD-driven import).
        if not team_slots:
            for pick in self.draft_order:
                team = pick["team"]
                round_num = pick["round"]
                entry = team_slots.setdefault(
                    team,
                    {"bc_slots": 0, "dc_slots": 0, "bc_used": 0, "dc_used": 0},
                )
                if round_num <= 2:
                    entry["bc_slots"] += 1
                else:
                    entry["dc_slots"] += 1

            self.state["team_slots"] = team_slots
            self.save_state()

    def make_pick(self, team: str, player_name: str) -> Dict:
        """
        Record a pick and advance to next pick.
        
        Args:
            team: Team abbreviation (e.g. "WIZ")
            player_name: Full player name
            
        Returns:
            Dict with pick details
            
        Raises:
            ValueError: If not this team's turn or draft not active
        """
        current_pick = self.get_current_pick()
        
        if current_pick is None:
            raise ValueError("Draft is complete, no more picks available")
        
        if self.state["status"] not in ["active", "paused"]:
            raise ValueError(f"Cannot make pick, draft status: {self.state['status']}")
        
        if current_pick["team"] != team:
            raise ValueError(
                f"Not {team}'s turn. Current pick is {current_pick['team']}"
            )
        
        # Record pick
        pick_record = {
            **current_pick,
            "player": player_name,
            "timestamp": datetime.now().isoformat(),
            "pick_index": self.current_pick_index
        }
        
        self.state["picks_made"].append(pick_record)
        
        # Track BC/DC slot usage for prospect drafts
        if self.draft_type == "prospect":
            team_slots = self.state.setdefault("team_slots", {})
            slot_info = team_slots.setdefault(
                team,
                {"bc_slots": 0, "dc_slots": 0, "bc_used": 0, "dc_used": 0},
            )
            round_num = current_pick["round"]
            if round_num <= 2:
                slot_info["bc_used"] = slot_info.get("bc_used", 0) + 1
            else:
                slot_info["dc_used"] = slot_info.get("dc_used", 0) + 1

        # Advance to next pick
        self.current_pick_index += 1
        self.state["current_pick_index"] = self.current_pick_index
        
        # Check if draft complete
        if self.current_pick_index >= len(self.draft_order):
            self.state["status"] = "completed"
            self.state["completed_at"] = datetime.now().isoformat()
            print(f"🏁 Draft complete!")
        
        # Save state
        self.save_state()
        
        print(f"✅ Pick recorded: {team} - {player_name} (Pick {current_pick['pick']})")
        
        return pick_record
    
    def undo_last_pick(self) -> Optional[Dict]:
        """
        Undo the most recent pick.
        
        Returns:
            The pick that was undone, or None if no picks to undo
        """
        if not self.state["picks_made"]:
            return None
        
        # Remove last pick
        undone_pick = self.state["picks_made"].pop()
        
        # Move back one position
        self.current_pick_index -= 1
        self.state["current_pick_index"] = self.current_pick_index
        
        # If draft was complete, set back to active
        if self.state["status"] == "completed":
            self.state["status"] = "active"
            self.state["completed_at"] = None
        
        self.save_state()
        
        print(f"↩️ Undone pick: {undone_pick['team']} - {undone_pick['player']}")
        
        return undone_pick
    
    def start_draft(self) -> None:
        """Start the draft (change status to active).

        Designed to be *idempotent* so that calling it again after a bot
        restart just re-attaches to an already-active draft instead of
        throwing an error.
        """
        status = self.state.get("status")

        # If the draft is already active, treat this as a no-op so that
        # /draft start can be used to reattach to existing state.
        if status == "active":
            print("🔁 Draft already active; reusing existing state")
            return

        # If the draft was completed, require a manual reset (e.g. removing
        # the state file) before starting over to avoid accidental resets.
        if status == "completed":
            raise ValueError(
                "Draft is already complete. Delete the draft_state file "
                "if you really want to restart from scratch."
            )

        self.state["status"] = "active"
        # Only set started_at if it wasn't already recorded.
        if not self.state.get("started_at"):
            self.state["started_at"] = datetime.now().isoformat()
        self.save_state()

        print("🏟️ Draft started!")
    
    def pause_draft(self) -> None:
        """Pause the draft"""
        if self.state["status"] != "active":
            raise ValueError(f"Cannot pause, draft status: {self.state['status']}")
        
        self.state["status"] = "paused"
        self.state["paused_at"] = datetime.now().isoformat()
        self.save_state()
        
        print(f"⏸️ Draft paused")
    
    def resume_draft(self) -> None:
        """Resume paused draft"""
        if self.state["status"] != "paused":
            raise ValueError(f"Cannot resume, draft status: {self.state['status']}")
        
        self.state["status"] = "active"
        self.state["paused_at"] = None
        self.save_state()
        
        print(f"▶️ Draft resumed")
    
    def get_picks_by_team(self, team: str) -> List[Dict]:
        """Get all picks made by a specific team"""
        return [p for p in self.state["picks_made"] if p["team"] == team]
    
    def get_picks_by_round(self, round_num: int) -> List[Dict]:
        """Get all picks from a specific round"""
        return [p for p in self.state["picks_made"] if p["round"] == round_num]
    
    def is_player_drafted(self, player_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a player has been drafted.
        
        Returns:
            (drafted: bool, team: Optional[str])
        """
        for pick in self.state["picks_made"]:
            if pick["player"].lower() == player_name.lower():
                return True, pick["team"]
        
        return False, None
    
    def get_draft_progress(self) -> Dict:
        """
        Get overall draft progress statistics.
        
        Returns:
            Dict with progress metrics
        """
        total_picks = len(self.draft_order)
        picks_made = len(self.state["picks_made"])
        
        # Count by round
        rounds = {}
        for pick in self.draft_order:
            round_num = pick["round"]
            if round_num not in rounds:
                rounds[round_num] = {"total": 0, "made": 0}
            rounds[round_num]["total"] += 1
        
        for pick in self.state["picks_made"]:
            round_num = pick["round"]
            rounds[round_num]["made"] += 1
        
        # Current round info
        current = self.get_current_pick()
        current_round = current["round"] if current else None
        
        return {
            "total_picks": total_picks,
            "picks_made": picks_made,
            "picks_remaining": total_picks - picks_made,
            "percent_complete": round((picks_made / total_picks) * 100, 1),
            "current_round": current_round,
            "rounds": rounds,
            "status": self.state["status"]
        }
    
    def export_results(self, format: str = "json") -> str:
        """
        Export draft results.
        
        Args:
            format: 'json' or 'csv'
            
        Returns:
            Filepath of exported file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        
        if format == "json":
            output_file = f"data/draft_results_{self.draft_type}_{self.season}_{timestamp}.json"
            
            results = {
                "draft_type": self.draft_type,
                "season": self.season,
                "status": self.state["status"],
                "started_at": self.state.get("started_at"),
                "completed_at": self.state.get("completed_at"),
                "picks": self.state["picks_made"]
            }
            
            with open(output_file, 'w') as f:
                json.dump(results, f, indent=2)
        
        elif format == "csv":
            output_file = f"data/draft_results_{self.draft_type}_{self.season}_{timestamp}.csv"
            
            import csv
            with open(output_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'round', 'pick', 'team', 'player', 'round_type', 'timestamp'
                ])
                writer.writeheader()
                
                for pick in self.state["picks_made"]:
                    writer.writerow({
                        'round': pick['round'],
                        'pick': pick['pick'],
                        'team': pick['team'],
                        'player': pick['player'],
                        'round_type': pick['round_type'],
                        'timestamp': pick['timestamp']
                    })
        
        print(f"📄 Results exported to: {output_file}")
        return output_file


# Testing / CLI usage
if __name__ == "__main__":
    print("🧪 Testing DraftManager")
    print("=" * 50)
    
    # Note: Requires data/draft_order_2025.json to exist
    try:
        dm = DraftManager(draft_type="prospect", season=2025)
        
        print(f"\n📊 Draft Progress:")
        progress = dm.get_draft_progress()
        print(f"   Status: {progress['status']}")
        print(f"   Picks: {progress['picks_made']}/{progress['total_picks']}")
        print(f"   Progress: {progress['percent_complete']}%")
        
        print(f"\n⏰ Current Pick:")
        current = dm.get_current_pick()
        if current:
            print(f"   Round {current['round']}, Pick {current['pick']}")
            print(f"   Team: {current['team']}")
            print(f"   Type: {current['round_type']}")
        
        print(f"\n📋 Next 2 Picks:")
        next_pick = dm.get_next_pick()
        if next_pick:
            print(f"   On Deck: {next_pick['team']} (Pick {next_pick['pick']})")
        
        after_next = dm.get_pick_after_next()
        if after_next:
            print(f"   In Hole: {after_next['team']} (Pick {after_next['pick']})")
        
    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        print(f"\n💡 Create data/draft_order_2025.json first")