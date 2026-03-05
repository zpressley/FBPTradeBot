"""
Board Manager - Personal draft board management
Handles target lists, reordering, and autopick logic for each team.

Storage format: boards are stored as Caesar-shifted UPIDs so that the
JSON file and API responses are opaque to casual snooping.  The shift
value lives in config/board_cipher.json.
"""

import json
import os
import subprocess
from typing import Callable, List, Optional, Dict

# Injected by health.py at module load — same pattern as api_settings, api_notes, etc.
_board_commit_fn: Optional[Callable[[list[str], str], None]] = None


def set_board_commit_fn(fn: Callable[[list[str], str], None]) -> None:
    """Set the commit function (called from health.py at startup)."""
    global _board_commit_fn
    _board_commit_fn = fn


class BoardManager:
    """
    Manages personal draft boards for all teams.

    Each team can maintain up to 50 prospect targets.
    Boards persist between sessions and are used for autopick.

    Internal storage is a list of **encoded** UPID strings per team.
    Callers pass *real* UPIDs; encoding/decoding is handled internally.
    """

    def __init__(self, season: int = 2026):
        self.season = season
        self.boards_file = f"data/manager_boards_{season}.json"

        # Configuration
        self.MAX_BOARD_SIZE = 50

        # Cipher
        self._shift = self._load_shift()

        # Lazy player lookups (populated on first use)
        self._upid_to_name: Optional[Dict[str, str]] = None
        self._name_to_upid: Optional[Dict[str, str]] = None

        # Load (and possibly migrate) boards
        self.boards = self.load_boards()

    # ------------------------------------------------------------------ #
    #  Cipher helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_shift() -> int:
        """Read Caesar-shift value from config/board_cipher.json."""
        path = "config/board_cipher.json"
        try:
            with open(path, "r") as f:
                return int(json.load(f).get("shift", 0))
        except Exception:
            return 0

    def _encode(self, upid: str) -> str:
        """Caesar-shift each digit of a UPID forward by ``self._shift``."""
        return "".join(
            str((int(ch) + self._shift) % 10) if ch.isdigit() else ch
            for ch in str(upid)
        )

    def _decode(self, encoded: str) -> str:
        """Reverse a Caesar-shifted UPID back to the real value."""
        return "".join(
            str((int(ch) - self._shift) % 10) if ch.isdigit() else ch
            for ch in str(encoded)
        )

    @property
    def shift(self) -> int:
        """Expose the shift value (used by API to send to frontend)."""
        return self._shift

    # ------------------------------------------------------------------ #
    #  Player lookup (lazy)
    # ------------------------------------------------------------------ #

    def _load_player_lookup(self) -> None:
        """Build upid<->name dicts from combined_players.json (once)."""
        if self._upid_to_name is not None:
            return
        self._upid_to_name = {}
        self._name_to_upid = {}
        try:
            with open("data/combined_players.json", "r") as f:
                players = json.load(f)
            for p in players:
                upid = str(p.get("upid", "")).strip()
                name = (p.get("name") or "").strip()
                if upid and name:
                    self._upid_to_name[upid] = name
                    self._name_to_upid[name.lower()] = upid
        except Exception as exc:
            print(f"⚠️ BoardManager: failed to load player lookup: {exc}")
            self._upid_to_name = {}
            self._name_to_upid = {}

    def upid_to_name(self, upid: str) -> str:
        """Resolve a real UPID to a player name (empty string if unknown)."""
        self._load_player_lookup()
        return self._upid_to_name.get(str(upid), "")

    def name_to_upid(self, name: str) -> str:
        """Resolve a player name to a real UPID (empty string if unknown)."""
        self._load_player_lookup()
        return self._name_to_upid.get(name.lower().strip(), "")

    # ------------------------------------------------------------------ #
    #  Board accessors
    # ------------------------------------------------------------------ #

    def get_board(self, team: str) -> List[str]:
        """Return the raw (encoded) board — used by the API layer."""
        return self.boards.get(team, []).copy()

    def get_board_decoded(self, team: str) -> List[str]:
        """Return a list of *real* UPIDs for server-side logic."""
        return [self._decode(e) for e in self.boards.get(team, [])]

    def resolve_board(self, team: str) -> List[Dict]:
        """Return [{"upid": real_upid, "name": "..."}] for display."""
        self._load_player_lookup()
        result = []
        for encoded in self.boards.get(team, []):
            real = self._decode(encoded)
            name = self._upid_to_name.get(real, f"Unknown ({real})")
            result.append({"upid": real, "name": name})
        return result

    # ------------------------------------------------------------------ #
    #  Load / save / migrate
    # ------------------------------------------------------------------ #

    def load_boards(self) -> Dict[str, List[str]]:
        """Load boards from file.  Migrates legacy name-based boards
        to encoded UPIDs automatically on first load."""
        if not os.path.exists(self.boards_file):
            teams = ["WIZ", "B2J", "CFL", "HAM", "RV", "SAD", "JEP",
                     "TBB", "DRO", "DMN", "LFB", "WAR"]
            boards = {team: [] for team in teams}
            self.save_boards(boards)
            print(f"✅ Initialized empty boards for {len(teams)} teams")
            return boards

        with open(self.boards_file, "r") as f:
            boards = json.load(f)

        # --- Migration: detect legacy name-based entries ---
        needs_migration = False
        for team, entries in boards.items():
            for entry in entries:
                # Real/encoded UPIDs are purely numeric 4-digit strings.
                # Anything non-numeric is a legacy player name.
                if entry and not entry.isdigit():
                    needs_migration = True
                    break
            if needs_migration:
                break

        if needs_migration:
            self._load_player_lookup()
            migrated_count = 0
            dropped = []
            for team in list(boards.keys()):
                new_list = []
                for entry in boards[team]:
                    if entry.isdigit():
                        new_list.append(entry)  # already encoded
                        continue
                    # Legacy name -> look up real UPID -> encode
                    upid = self._name_to_upid.get(entry.lower().strip(), "")
                    if upid:
                        new_list.append(self._encode(upid))
                        migrated_count += 1
                    else:
                        dropped.append((team, entry))
                boards[team] = new_list

            print(f"🔄 Migrated {migrated_count} board entries from names → encoded UPIDs")
            if dropped:
                for t, n in dropped:
                    print(f"   ⚠️ Dropped {t}: '{n}' (no UPID found)")

            # Persist migrated data
            self.boards = boards
            self.save_boards(boards)
        else:
            print(f"✅ Loaded boards for {len(boards)} teams")

        return boards

    def save_boards(self, boards: Optional[Dict] = None) -> None:
        """Persist boards to disk and best-effort commit to Git."""
        if boards is None:
            boards = self.boards

        os.makedirs("data", exist_ok=True)

        with open(self.boards_file, "w") as f:
            json.dump(boards, f, indent=2)

        self._commit_boards_file()

    # ------------------------------------------------------------------ #
    #  Mutations (callers pass REAL UPIDs; encoding is internal)
    # ------------------------------------------------------------------ #

    def add_to_board(self, team: str, upid: str, display_name: str = "") -> tuple[bool, str]:
        """Add a player to a team's board by UPID.

        Args:
            team: Team abbreviation
            upid: The player's real (unencoded) UPID
            display_name: Optional name for the response message
        """
        if team not in self.boards:
            self.boards[team] = []

        encoded = self._encode(upid)

        # Check duplicate (compare encoded values)
        if encoded in self.boards[team]:
            name = display_name or self.upid_to_name(upid) or upid
            return False, f"{name} is already on your board"

        if len(self.boards[team]) >= self.MAX_BOARD_SIZE:
            return False, f"Board is full ({self.MAX_BOARD_SIZE}/{self.MAX_BOARD_SIZE}). Remove a player first."

        self.boards[team].append(encoded)
        self.save_boards()

        name = display_name or self.upid_to_name(upid) or upid
        position = len(self.boards[team])
        return True, f"Added {name} to your board (#{position})"

    def remove_from_board(self, team: str, upid: str) -> tuple[bool, str]:
        """Remove a player from a team's board by UPID."""
        if team not in self.boards or not self.boards[team]:
            return False, "Your board is empty"

        encoded = self._encode(upid)
        if encoded in self.boards[team]:
            self.boards[team].remove(encoded)
            self.save_boards()
            name = self.upid_to_name(upid) or upid
            return True, f"Removed {name} from your board"

        return False, "Player not found on your board"

    def reorder_board(self, team: str, new_order: List[str]) -> tuple[bool, str]:
        """Replace a team's board with *new_order* (encoded values)."""
        if team not in self.boards:
            return False, "You don't have a board yet"

        if set(self.boards[team]) != set(new_order):
            return False, "New order must include exactly the same players"

        self.boards[team] = new_order
        self.save_boards()
        return True, "Board reordered successfully"

    def move_player(self, team: str, upid: str, new_position: int) -> tuple[bool, str]:
        """Move a player (by real UPID) to a new position on the board."""
        if team not in self.boards or not self.boards[team]:
            return False, "Your board is empty"

        encoded = self._encode(upid)
        if encoded not in self.boards[team]:
            return False, "Player not found on your board"

        self.boards[team].remove(encoded)
        insert_index = max(0, min(new_position - 1, len(self.boards[team])))
        self.boards[team].insert(insert_index, encoded)
        self.save_boards()

        name = self.upid_to_name(upid) or upid
        return True, f"Moved {name} to position {insert_index + 1}"

    # ------------------------------------------------------------------ #
    #  Autopick / stats helpers
    # ------------------------------------------------------------------ #

    def get_next_available(self, team: str, drafted_players: List[str]) -> Optional[str]:
        """Return the next available player *name* from a team's board.

        Args:
            team: Team abbreviation
            drafted_players: List of already-drafted player **names**

        Returns:
            The canonical player name, or None.
        """
        self._load_player_lookup()
        drafted_set = set(p.lower() for p in drafted_players)

        for encoded in self.boards.get(team, []):
            real_upid = self._decode(encoded)
            name = self._upid_to_name.get(real_upid, "")
            if name and name.lower() not in drafted_set:
                return name

        return None

    def mark_as_drafted(self, player_name: str) -> None:
        """Placeholder — we check availability at autopick time."""
        pass

    def get_board_stats(self, team: str, drafted_players: List[str]) -> Dict:
        """Board statistics (resolves encoded entries to names for comparison)."""
        self._load_player_lookup()
        board = self.boards.get(team, [])
        drafted_set = set(p.lower() for p in drafted_players)

        total = len(board)
        drafted_count = 0
        for encoded in board:
            real_upid = self._decode(encoded)
            name = self._upid_to_name.get(real_upid, "")
            if name and name.lower() in drafted_set:
                drafted_count += 1

        return {
            "total": total,
            "available": total - drafted_count,
            "drafted": drafted_count,
            "slots_remaining": self.MAX_BOARD_SIZE - total,
        }

    def get_board_size(self, team: str) -> int:
        """Get number of players on team's board."""
        return len(self.boards.get(team, []))

    def clear_board(self, team: str) -> tuple[bool, str]:
        """Clear all players from team's board."""
        if team not in self.boards:
            return False, "No board to clear"

        count = len(self.boards[team])
        self.boards[team] = []
        self.save_boards()
        return True, f"Cleared {count} players from your board"

    # ------------------------------------------------------------------ #
    #  Git persistence (unchanged)
    # ------------------------------------------------------------------ #

    def _commit_boards_file(self) -> None:
        """Best-effort commit via the injected queue (set by health.py).

        Falls back to a no-op log if the commit function was never injected
        (e.g. running board_manager standalone for tests).
        """
        if _board_commit_fn is not None:
            try:
                _board_commit_fn([self.boards_file], f"Update draft boards for season {self.season}")
                print("✅ Draft boards commit queued via health pipeline")
            except Exception as exc:
                print(f"⚠️ Draft boards commit queue error: {exc}")
        else:
            print("⚠️ Draft boards commit skipped (no commit function injected)")


# Testing
if __name__ == "__main__":
    print("🧪 Testing BoardManager")
    print("=" * 50)
    
    bm = BoardManager(season=2025)
    
    # Test adding players
    print("\n📋 Test 1: Add players to WIZ board")
    success, msg = bm.add_to_board("WIZ", "Jackson Chourio")
    print(f"   {msg}")
    
    success, msg = bm.add_to_board("WIZ", "Kyle Teel")
    print(f"   {msg}")
    
    success, msg = bm.add_to_board("WIZ", "James Wood")
    print(f"   {msg}")
    
    # Test duplicate
    print("\n📋 Test 2: Try to add duplicate")
    success, msg = bm.add_to_board("WIZ", "Jackson Chourio")
    print(f"   {msg}")
    
    # View board
    print("\n📋 Test 3: View board")
    board = bm.get_board("WIZ")
    print(f"   WIZ Board ({len(board)} players):")
    for i, player in enumerate(board, 1):
        print(f"      {i}. {player}")
    
    # Test next available (for autopick)
    print("\n📋 Test 4: Get next available (autopick logic)")
    drafted = ["Jackson Chourio"]  # Simulate this player already picked
    next_player = bm.get_next_available("WIZ", drafted)
    print(f"   Next available: {next_player}")
    
    # Test board stats
    print("\n📋 Test 5: Board statistics")
    stats = bm.get_board_stats("WIZ", drafted)
    print(f"   Total: {stats['total']}")
    print(f"   Available: {stats['available']}")
    print(f"   Drafted: {stats['drafted']}")
    print(f"   Slots remaining: {stats['slots_remaining']}")
    
    # Test move player
    print("\n📋 Test 6: Move player")
    success, msg = bm.move_player("WIZ", "James Wood", 1)
    print(f"   {msg}")
    
    board = bm.get_board("WIZ")
    print(f"   Updated board:")
    for i, player in enumerate(board, 1):
        print(f"      {i}. {player}")
    
    # Test remove
    print("\n📋 Test 7: Remove player")
    success, msg = bm.remove_from_board("WIZ", "Kyle Teel")
    print(f"   {msg}")
    
    board = bm.get_board("WIZ")
    print(f"   Board now has {len(board)} players")
    
    print("\n✅ BoardManager tests complete!")