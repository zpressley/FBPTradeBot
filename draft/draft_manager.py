"""
Draft Manager - Core draft logic and state management
Handles draft flow, pick tracking, and state persistence
"""

import json
import os
import subprocess
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pad.pad_processor import (
    _append_player_log_entry,
    _load_json,
    _save_json,
    load_managers_config,
)


def _resolve_team_name(team_abbr: str) -> str:
    """Resolve an FBP team abbreviation (WIZ) to franchise name (Whiz Kids)."""
    try:
        cfg = load_managers_config() or {}
        teams = cfg.get("teams") or {}
        meta = teams.get(team_abbr)
        if isinstance(meta, dict):
            name = (meta.get("name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return team_abbr


class DraftManager:
    _git_lock = threading.Lock()
    """
    Manages draft state and flow for FBP drafts.
    Loads custom draft order, tracks picks, handles state persistence.

    test_mode:
      - Intended for draft-flow validation.
      - Persists draft_state to GitHub, but does NOT mutate combined_players
        or player_log, and does NOT write pick results into draft_order.
    """

    def __init__(self, draft_type: str = "prospect", season: int = 2026, test_mode: bool | None = None):
        self.draft_type = draft_type
        self.season = season

        if test_mode is None:
            test_mode = os.getenv("DRAFT_TEST_MODE", "false").lower() == "true"
        self.test_mode = bool(test_mode)
        
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
        
        # Extract picks array (handles both formats):
        # - New format: file is a bare list of pick dicts.
        # - Legacy format: object with `picks` or `rounds` keys.
        if isinstance(data, list):
            picks = data
        else:
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
        
        # Note: older formats used a single global pick number sequence
        # (1..N). Newer formats may reset `pick` each round (1..X per
        # round). To stay flexible we no longer enforce global sequential
        # numbering here; we just trust the order of the list.
        
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

    def _apply_pick_to_rosters(self, pick_record: Dict, player_data: Optional[Dict]) -> List[str]:
        """Apply pick side effects to combined_players + player_log.

        Returns a list of file paths that were mutated.
        """
        mutated: List[str] = []

        # Only prospect draft currently mutates combined_players roster ownership.
        if self.draft_type != "prospect":
            return mutated

        combined_path = "data/combined_players.json"
        player_log_path = "data/player_log.json"

        combined_players = _load_json(combined_path) or []
        if not isinstance(combined_players, list):
            print(f"⚠️ combined_players is not a list: {combined_path}")
            return mutated

        upid = None
        if player_data:
            upid = str(player_data.get("upid") or "").strip() or None

        # Best-effort locate record by UPID, fall back to name match.
        player_rec = None
        if upid:
            player_rec = next(
                (p for p in combined_players if str(p.get("upid") or "").strip() == upid),
                None,
            )
        if player_rec is None:
            target = (pick_record.get("player") or "").strip().lower()
            if target:
                player_rec = next(
                    (p for p in combined_players if str(p.get("name") or "").strip().lower() == target),
                    None,
                )

        if player_rec is None:
            print(f"⚠️ Draft pick could not be applied to combined_players (missing record): {pick_record.get('player')}")
            return mutated

        franchise_name = _resolve_team_name(pick_record.get("team", ""))

        # Draft contract type is derived by round.
        round_num = int(pick_record.get("round") or 0)
        if round_num <= 2:
            contract_type = "Blue Chip Contract"
        else:
            contract_type = "Development Cont."

        # Mutate combined_players ownership.
        player_rec["manager"] = franchise_name
        player_rec["contract_type"] = contract_type

        _save_json(combined_path, combined_players)
        mutated.append(combined_path)

        # Append snapshot entry to player_log for audit + website timeline.
        player_log = _load_json(player_log_path) or []
        if not isinstance(player_log, list):
            player_log = []

        event = f"Draft pick: {pick_record.get('team')} selected {pick_record.get('player')} (R{pick_record.get('round')} P{pick_record.get('pick')})"
        _append_player_log_entry(
            player_log,
            player_rec,
            season=self.season,
            source="draft",
            update_type="draft_pick",
            event=event,
            admin=str(pick_record.get("team") or "draft"),
        )
        _save_json(player_log_path, player_log)
        mutated.append(player_log_path)

        return mutated

    def _clear_pick_from_rosters(self, pick_record: Dict) -> List[str]:
        """Best-effort rollback of roster side effects for an undone pick."""
        mutated: List[str] = []
        if self.draft_type != "prospect":
            return mutated

        combined_path = "data/combined_players.json"
        player_log_path = "data/player_log.json"

        combined_players = _load_json(combined_path) or []
        if not isinstance(combined_players, list):
            return mutated

        upid = str(pick_record.get("upid") or "").strip()
        player_rec = None
        if upid:
            player_rec = next(
                (p for p in combined_players if str(p.get("upid") or "").strip() == upid),
                None,
            )
        if player_rec is None:
            target = (pick_record.get("player") or "").strip().lower()
            if target:
                player_rec = next(
                    (p for p in combined_players if str(p.get("name") or "").strip().lower() == target),
                    None,
                )

        if player_rec is not None:
            # Revert to undrafted state (eligible prospects should have been unowned).
            player_rec["manager"] = ""
            player_rec["contract_type"] = ""
            _save_json(combined_path, combined_players)
            mutated.append(combined_path)

        # Remove the most recent matching draft_pick log entry for this player.
        player_log = _load_json(player_log_path) or []
        if isinstance(player_log, list):
            target_upid = upid
            for i in range(len(player_log) - 1, -1, -1):
                entry = player_log[i]
                if entry.get("source") == "draft" and entry.get("update_type") == "draft_pick":
                    if target_upid and str(entry.get("upid") or "").strip() == target_upid:
                        player_log.pop(i)
                        break
                    if not target_upid and (entry.get("player_name") or "").strip().lower() == (pick_record.get("player") or "").strip().lower():
                        player_log.pop(i)
                        break
            _save_json(player_log_path, player_log)
            mutated.append(player_log_path)

        return mutated

    def reset_to_pick_one(self) -> List[str]:
        """Reset the draft back to pick 1 without deleting files.

        This always clears draft_state (picks + clock).

        Live mode additionally clears:
        - order-file `result` payloads
        - roster side effects in combined_players + matching player_log draft entries

        Test mode clears ONLY draft_state so you can re-run dry tests.

        Returns a list of file paths that were mutated.
        """
        mutated: List[str] = []

        # Ensure state exists.
        state = self.state or {}
        picks = list(state.get("picks_made") or [])

        # Roll back roster effects for all picks (live mode only).
        if (not self.test_mode) and self.draft_type == "prospect" and picks:
            combined_path = "data/combined_players.json"
            combined_players = _load_json(combined_path) or []
            if isinstance(combined_players, list):
                drafted_upids = {str(p.get("upid") or "").strip() for p in picks if str(p.get("upid") or "").strip()}
                drafted_names = {str(p.get("player") or "").strip().lower() for p in picks if str(p.get("player") or "").strip()}

                for rec in combined_players:
                    upid = str(rec.get("upid") or "").strip()
                    name = str(rec.get("name") or "").strip().lower()
                    if (upid and upid in drafted_upids) or (name and name in drafted_names):
                        rec["manager"] = ""
                        rec["contract_type"] = ""

                _save_json(combined_path, combined_players)
                mutated.append(combined_path)

            # Remove draft_pick log entries for this season.
            player_log_path = "data/player_log.json"
            player_log = _load_json(player_log_path) or []
            if isinstance(player_log, list):
                kept = [
                    e
                    for e in player_log
                    if not (
                        e.get("season") == self.season
                        and e.get("source") == "draft"
                        and e.get("update_type") == "draft_pick"
                    )
                ]
                _save_json(player_log_path, kept)
                mutated.append(player_log_path)

        # Reset state fields.
        state["status"] = "not_started"
        state["current_pick_index"] = 0
        state["picks_made"] = []
        state["timer_started_at"] = None
        state["paused_at"] = None
        state["started_at"] = None
        state["completed_at"] = None
        state["team_slots"] = {}

        self.current_pick_index = 0
        self.state = state
        self.save_state()
        mutated.append(self.state_file)

        # Clear order results (live mode only).
        if not self.test_mode:
            try:
                with open(self.order_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError:
                data = None

            if data is not None:
                if isinstance(data, list):
                    picks_list = data
                    container = None
                else:
                    picks_list = data.get("picks") or data.get("rounds") or []
                    container = data

                for p in picks_list:
                    p["result"] = None

                out = container if container is not None else picks_list
                if container is not None:
                    if "picks" in container:
                        container["picks"] = picks_list
                    elif "rounds" in container:
                        container["rounds"] = picks_list

                with open(self.order_file, "w", encoding="utf-8") as f:
                    json.dump(out, f, indent=2)
                mutated.append(self.order_file)

        return mutated

    def make_pick(self, team: str, player_name: str, player_data: Optional[Dict] = None) -> Dict:
        """Record a pick and advance to next pick.

        Args:
            team: Team abbreviation (e.g. "WIZ")
            player_name: Full player name
            player_data: Optional full player record (from ProspectDatabase)

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
        pick_record: Dict = {
            **current_pick,
            "player": player_name,
            "timestamp": datetime.now().isoformat(),
            "pick_index": self.current_pick_index,
        }

        # If we have a full player record, carry through UPID and basics so
        # downstream consumers and draft_order_*.json can reference a stable ID.
        if player_data:
            upid = player_data.get("upid")
            if upid:
                pick_record["upid"] = str(upid)
            position = player_data.get("position")
            if position:
                pick_record["position"] = position
            mlb_team = player_data.get("team")
            if mlb_team:
                pick_record["mlb_team"] = mlb_team

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

        # Mirror result into the draft_order_{season}.json file so that the
        # static order also carries the final pick result and UPID.
        # In test mode we avoid writing results into the order file.
        if not self.test_mode:
            try:
                self._update_order_result(pick_record)
            except Exception as exc:
                print(f"⚠️ Failed to update draft order results: {exc}")

        # Advance to next pick
        self.current_pick_index += 1
        self.state["current_pick_index"] = self.current_pick_index

        # Check if draft complete
        if self.current_pick_index >= len(self.draft_order):
            self.state["status"] = "completed"
            self.state["completed_at"] = datetime.now().isoformat()
            print("🏁 Draft complete!")

        # Save state
        self.save_state()

        # Apply roster side effects + log.
        # In test mode, do not touch combined_players/player_log or draft_order results.
        mutated_files = [self.state_file]
        if not self.test_mode:
            mutated_files.append(self.order_file)
            try:
                mutated_files += self._apply_pick_to_rosters(pick_record, player_data)
            except Exception as exc:
                print(f"⚠️ Failed to apply pick roster side effects: {exc}")

        # Commit once per pick so GitHub becomes the persistence layer.
        try:
            msg = (
                f"Draft pick: {pick_record.get('team')} {pick_record.get('player')} "
                f"(R{pick_record.get('round')} P{pick_record.get('pick')})"
            )
            # De-dupe paths and only commit non-empty.
            unique = []
            for p in mutated_files:
                if p and p not in unique:
                    unique.append(p)
            self._commit_draft_files_async(unique, msg)
        except Exception as exc:
            print(f"⚠️ Draft pick git commit/push failed: {exc}")

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

        # Mirror undo into draft_order_{season}.json by clearing the
        # corresponding result payload.
        if not self.test_mode:
            try:
                idx = undone_pick.get("pick_index")
                if isinstance(idx, int):
                    self._clear_order_result(idx)
            except Exception as exc:
                print(f"⚠️ Failed to clear draft order result on undo: {exc}")

        self.save_state()

        # Roll back roster side effects and commit.
        mutated_files = [self.state_file]
        if not self.test_mode:
            mutated_files.append(self.order_file)
            try:
                mutated_files += self._clear_pick_from_rosters(undone_pick)
            except Exception as exc:
                print(f"⚠️ Failed to roll back roster side effects for undo: {exc}")

        try:
            unique = []
            for p in mutated_files:
                if p and p not in unique:
                    unique.append(p)
            self._commit_draft_files_async(unique, "Draft undo")
        except Exception as exc:
            print(f"⚠️ Draft undo git commit/push failed: {exc}")

        print(f"↩️ Undone pick: {undone_pick['team']} - {undone_pick['player']}")

        return undone_pick
    
    def start_draft(self) -> None:
        """Start the draft (change status to active).

        Designed to be *idempotent* so that calling it again after a bot
        restart just re-attaches to an already-active draft instead of
        throwing an error.

        We also commit the updated state file so GitHub remains the
        persistence layer across Render deploys/restarts.
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

        try:
            self._commit_draft_files_async([self.state_file], f"Draft started: {self.draft_type} {self.season}")
        except Exception as exc:
            print(f"⚠️ Draft start git commit/push failed: {exc}")

        print("🏟️ Draft started!")
    
    def pause_draft(self) -> None:
        """Pause the draft"""
        if self.state["status"] != "active":
            raise ValueError(f"Cannot pause, draft status: {self.state['status']}")
        
        self.state["status"] = "paused"
        self.state["paused_at"] = datetime.now().isoformat()
        self.save_state()

        try:
            self._commit_draft_files_async([self.state_file], f"Draft paused: {self.draft_type} {self.season}")
        except Exception as exc:
            print(f"⚠️ Draft pause git commit/push failed: {exc}")
        
        print(f"⏸️ Draft paused")
    
    def resume_draft(self) -> None:
        """Resume paused draft"""
        if self.state["status"] != "paused":
            raise ValueError(f"Cannot resume, draft status: {self.state['status']}")
        
        self.state["status"] = "active"
        self.state["paused_at"] = None
        self.save_state()

        try:
            self._commit_draft_files_async([self.state_file], f"Draft resumed: {self.draft_type} {self.season}")
        except Exception as exc:
            print(f"⚠️ Draft resume git commit/push failed: {exc}")
        
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
    
    def _update_order_result(self, pick_record: Dict) -> None:
        """Persist the result of a pick into the draft order JSON.

        Adds/updates a `result` field on the corresponding pick entry:
          {
            "player": str,
            "timestamp": str,
            "pick_index": int,
            "upid": str | None
          }

        Note: We intentionally do *not* git-commit here; we commit once per pick
        from make_pick() so that state + order + roster files stay in sync.
        """
        if not self.order_file:
            return

        try:
            with open(self.order_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return

        # Support both bare-list and wrapped {picks:[...]} / {rounds:[...]} formats.
        if isinstance(data, list):
            picks = data
            container = None
        else:
            picks = data.get("picks") or data.get("rounds") or []
            container = data

        idx = pick_record.get("pick_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(picks):
            return

        result_payload = {
            "player": pick_record.get("player"),
            "timestamp": pick_record.get("timestamp"),
            "pick_index": pick_record.get("pick_index"),
            "upid": pick_record.get("upid"),
        }

        picks[idx]["result"] = result_payload

        # Write back using the same shape we read.
        if container is not None:
            if "picks" in container:
                container["picks"] = picks
            elif "rounds" in container:
                container["rounds"] = picks
            out = container
        else:
            out = picks

        with open(self.order_file, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


    def _clear_order_result(self, pick_index: int) -> None:
        """Clear the `result` payload for a given pick index in the order file."""
        if not self.order_file:
            return

        try:
            with open(self.order_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return

        if isinstance(data, list):
            picks = data
            container = None
        else:
            picks = data.get("picks") or data.get("rounds") or []
            container = data

        if not (0 <= pick_index < len(picks)):
            return

        if "result" in picks[pick_index]:
            picks[pick_index]["result"] = None
        else:
            picks[pick_index]["result"] = None

        if container is not None:
            if "picks" in container:
                container["picks"] = picks
            elif "rounds" in container:
                container["rounds"] = picks
            out = container
        else:
            out = picks

        with open(self.order_file, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


    def _commit_draft_files(self, file_paths: List[str], message: str) -> None:
        """Best-effort helper to git commit and push draft data files.

        Uses REPO_ROOT when available (Render), otherwise current working
        directory. Failures are logged but never raised.
        """
        repo_root = os.getenv("REPO_ROOT", os.getcwd())

        try:
            if file_paths:
                subprocess.run(
                    ["git", "add", *file_paths],
                    check=True,
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                )

            subprocess.run(
                ["git", "commit", "-m", message],
                check=True,
                cwd=repo_root,
                capture_output=True,
                text=True,
            )

            token = os.getenv("GITHUB_TOKEN")
            if token:
                repo = os.getenv("GITHUB_REPO", "zpressley/FBPTradeBot")
                username = os.getenv("GITHUB_USER", "x-access-token")
                remote_url = f"https://{username}:{token}@github.com/{repo}.git"
                push_cmd = ["git", "push", remote_url, "HEAD:main"]
            else:
                push_cmd = ["git", "push"]

            subprocess.run(
                push_cmd,
                check=True,
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            print("✅ Draft git commit and push succeeded")

        except subprocess.CalledProcessError as exc:
            print(f"⚠️ Draft git commit/push failed with code {exc.returncode}")
        except Exception as exc:
            print(f"⚠️ Draft git commit/push error: {exc}")

    def _commit_draft_files_async(self, file_paths: List[str], message: str) -> None:
        """Run git commit/push in a background thread.

        This prevents slow git operations (especially push) from blocking the
        Discord bot event loop, which can make the website appear "late".
        """

        def _worker():
            try:
                with DraftManager._git_lock:
                    self._commit_draft_files(file_paths, message)
            except Exception as exc:
                print(f"⚠️ Draft git async commit/push error: {exc}")

        t = threading.Thread(target=_worker, daemon=True, name="draft-git")
        t.start()

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
    
    # Note: Requires data/draft_order_2026.json to exist
    try:
        dm = DraftManager(draft_type="prospect", season=2026)
        
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
        print(f"\n💡 Create data/draft_order_2026.json first")
