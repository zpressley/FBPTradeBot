"""
Board Manager - Personal draft board management
Handles target lists, reordering, and autopick logic for each team
"""

import json
import os
from typing import List, Optional, Dict


class BoardManager:
    """
    Manages personal draft boards for all teams.
    
    Each team can maintain up to 50 prospect targets.
    Boards persist between sessions and are used for autopick.
    """
    
    def __init__(self, season: int = 2026):
        self.season = season
        self.boards_file = f"data/manager_boards_{season}.json"
        self.boards = self.load_boards()
        
        # Configuration
        self.MAX_BOARD_SIZE = 50
    
    def load_boards(self) -> Dict[str, List[str]]:
        """
        Load manager boards from file.
        
        Returns:
            Dict mapping team -> list of player names
            Example: {"WIZ": ["Jackson Chourio", "Kyle Teel", ...]}
        """
        if not os.path.exists(self.boards_file):
            # Initialize empty boards for all teams
            teams = ["WIZ", "B2J", "CFL", "HAM", "RV", "SAD", "JEP", "TBB", "DRO", "DMN", "LFB", "WAR"]
            boards = {team: [] for team in teams}
            self.save_boards(boards)
            print(f"✅ Initialized empty boards for {len(teams)} teams")
            return boards
        
        with open(self.boards_file, 'r') as f:
            boards = json.load(f)
        
        print(f"✅ Loaded boards for {len(boards)} teams")
        return boards
    
    def save_boards(self, boards: Optional[Dict] = None) -> None:
        """Persist boards to disk"""
        if boards is None:
            boards = self.boards
        
        os.makedirs("data", exist_ok=True)
        
        with open(self.boards_file, 'w') as f:
            json.dump(boards, f, indent=2)
    
    def add_to_board(self, team: str, player_name: str) -> tuple[bool, str]:
        """
        Add a player to team's board.
        
        Args:
            team: Team abbreviation
            player_name: Full player name
            
        Returns:
            (success: bool, message: str)
        """
        if team not in self.boards:
            self.boards[team] = []
        
        # Check if already on board
        if player_name in self.boards[team]:
            return False, f"{player_name} is already on your board"
        
        # Check board size limit
        if len(self.boards[team]) >= self.MAX_BOARD_SIZE:
            return False, f"Board is full ({self.MAX_BOARD_SIZE}/{self.MAX_BOARD_SIZE}). Remove a player first."
        
        # Add to end of board
        self.boards[team].append(player_name)
        self.save_boards()
        
        position = len(self.boards[team])
        return True, f"Added {player_name} to your board (#{position})"
    
    def remove_from_board(self, team: str, player_input: str) -> tuple[bool, str]:
        """
        Remove a player from team's board (with fuzzy matching).
        
        Returns:
            (success: bool, message: str)
        """
        if team not in self.boards or not self.boards[team]:
            return False, "Your board is empty"
        
        # Try exact match first (case insensitive)
        exact_match = None
        for player in self.boards[team]:
            if player.lower() == player_input.lower():
                exact_match = player
                break
        
        if exact_match:
            self.boards[team].remove(exact_match)
            self.save_boards()
            return True, f"Removed {exact_match} from your board"
        
        # Try fuzzy match
        from difflib import get_close_matches
        matches = get_close_matches(player_input, self.boards[team], n=1, cutoff=0.6)
        
        if matches:
            matched_player = matches[0]
            self.boards[team].remove(matched_player)
            self.save_boards()
            
            # Let them know we fuzzy matched
            if matched_player.lower() != player_input.lower():
                return True, f"Removed **{matched_player}** from your board (matched '{player_input}')"
            return True, f"Removed {matched_player} from your board"
        
        return False, f"'{player_input}' not found on your board"
    
    def reorder_board(self, team: str, new_order: List[str]) -> tuple[bool, str]:
        """
        Reorder team's entire board.
        
        Args:
            team: Team abbreviation
            new_order: List of player names in new order
            
        Returns:
            (success: bool, message: str)
        """
        if team not in self.boards:
            return False, "You don't have a board yet"
        
        # Validate all players are on the board
        current_board = set(self.boards[team])
        new_board = set(new_order)
        
        if current_board != new_board:
            return False, "New order must include exactly the same players"
        
        self.boards[team] = new_order
        self.save_boards()
        
        return True, "Board reordered successfully"
    
    def move_player(self, team: str, player_input: str, new_position: int) -> tuple[bool, str]:
        """
        Move a player to a specific position on the board (with fuzzy matching).
        
        Args:
            team: Team abbreviation
            player_input: Player to move (fuzzy matched)
            new_position: New position (1-indexed)
            
        Returns:
            (success: bool, message: str)
        """
        if team not in self.boards or not self.boards[team]:
            return False, "Your board is empty"
        
        # Try exact match first (case insensitive)
        player_name = None
        for player in self.boards[team]:
            if player.lower() == player_input.lower():
                player_name = player
                break
        
        # Try fuzzy match if no exact match
        if not player_name:
            from difflib import get_close_matches
            matches = get_close_matches(player_input, self.boards[team], n=1, cutoff=0.6)
            
            if matches:
                player_name = matches[0]
            else:
                return False, f"'{player_input}' not found on your board"
        
        # Remove from current position
        self.boards[team].remove(player_name)
        
        # Insert at new position (convert to 0-indexed)
        insert_index = max(0, min(new_position - 1, len(self.boards[team])))
        self.boards[team].insert(insert_index, player_name)
        
        self.save_boards()
        
        # Show what we matched if different
        if player_name.lower() != player_input.lower():
            return True, f"Moved **{player_name}** to position {insert_index + 1} (matched '{player_input}')"
        
        return True, f"Moved {player_name} to position {insert_index + 1}"
    
    def get_board(self, team: str) -> List[str]:
        """Get team's current board"""
        return self.boards.get(team, []).copy()
    
    def get_board_size(self, team: str) -> int:
        """Get number of players on team's board"""
        return len(self.boards.get(team, []))
    
    def clear_board(self, team: str) -> tuple[bool, str]:
        """Clear all players from team's board"""
        if team not in self.boards:
            return False, "No board to clear"
        
        count = len(self.boards[team])
        self.boards[team] = []
        self.save_boards()
        
        return True, f"Cleared {count} players from your board"
    
    def get_next_available(self, team: str, drafted_players: List[str]) -> Optional[str]:
        """
        Get next available player from team's board.
        Used for autopick logic.
        
        Args:
            team: Team abbreviation
            drafted_players: List of already-drafted player names
            
        Returns:
            Next available player name, or None if board empty/all drafted
        """
        board = self.boards.get(team, [])
        drafted_set = set(p.lower() for p in drafted_players)
        
        for player in board:
            if player.lower() not in drafted_set:
                return player
        
        return None  # No available players on board
    
    def mark_as_drafted(self, player_name: str) -> None:
        """
        Mark player as drafted across all boards.
        (Optional - can be used to show strikethrough in board display)
        
        For now, we just leave them on the board and check availability at autopick time.
        """
        pass
    
    def get_board_stats(self, team: str, drafted_players: List[str]) -> Dict:
        """
        Get statistics about a team's board.
        
        Returns:
            Dict with total, available, drafted counts
        """
        board = self.boards.get(team, [])
        drafted_set = set(p.lower() for p in drafted_players)
        
        total = len(board)
        drafted_count = sum(1 for p in board if p.lower() in drafted_set)
        available = total - drafted_count
        
        return {
            "total": total,
            "available": available,
            "drafted": drafted_count,
            "slots_remaining": self.MAX_BOARD_SIZE - total
        }


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