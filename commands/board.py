"""
Board Commands - DM interface for personal draft boards
Managers use these commands in DMs to build their target lists
"""

import discord
from discord import app_commands
from discord.ext import commands
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draft.board_manager import BoardManager
from draft.prospect_database import ProspectDatabase


class BoardCommands(commands.Cog):
    """
    DM commands for managing personal draft boards.
    
    Commands:
    - /board - View your board
    - /add [player] - Add player to board
    - /remove [player] - Remove player from board
    - /move [player] [position] - Move player to position
    - /clear - Clear entire board
    """
    
    def __init__(self, bot):
        self.bot = bot
        # Use 2026 draft season for manager boards
        self.board_manager = BoardManager(season=2026)
        # Prospect database for resolving names and enforcing eligibility
        self.prospect_db = ProspectDatabase(season=2026, draft_type="prospect")
        
        print("✅ Board commands loaded")
    
    def _get_team_for_user(self, user_id: int) -> str:
        """Get team abbreviation for Discord user"""
        from commands.utils import DISCORD_ID_TO_TEAM
        return DISCORD_ID_TO_TEAM.get(user_id)

    def _is_player_board_eligible(self, player: dict) -> tuple[bool, str]:
        """Check that a prospect is eligible to appear on a draft board.

        For boards we only want players who are truly available for the
        prospect draft:
          - Farm prospects only (player_type == "Farm")
          - Unowned (no current manager/owner)
          - No active prospect contract (BC/DC/PC or similar)
        """
        if player.get("player_type") != "Farm":
            return False, "Player is not a Farm prospect."

        contract_type = (player.get("contract_type") or "").strip()
        if contract_type:
            return False, "Player already has a prospect contract (BC/DC/PC)."

        manager = (player.get("manager") or "").strip()
        owner = (player.get("owner") or "").strip()
        # Treat explicit 'None' as empty
        if manager and manager.lower() != "none":
            return False, f"Player is already on an FBP roster ({manager})."
        if owner and owner.lower() != "none":
            return False, "Player already has an FBP owner."

        return True, "Eligible prospect"

    def _resolve_eligible_player(self, query: str) -> tuple[bool, str, str]:
        """Resolve a typed name to a canonical, eligible prospect.

        Returns (success, message, canonical_name). On success, message is a
        human-friendly note (used in Discord responses).
        """
        q = (query or "").strip()
        if not q:
            return False, "Player name is empty.", ""

        # 1) Exact match first
        exact = self.prospect_db.get_by_name(q)
        if exact:
            ok, reason = self._is_player_board_eligible(exact)
            if not ok:
                return False, f"{reason}", ""
            return True, f"Added {exact['name']} to your board.", exact["name"]

        # 2) Fuzzy match using the database search helper
        from difflib import get_close_matches

        all_names = list(self.prospect_db.players.keys())
        matches = get_close_matches(q, all_names, n=5, cutoff=0.7)
        if not matches:
            return False, f"Player '{q}' not found in prospect database.", ""

        # Filter to eligible matches only
        eligible = []
        for name in matches:
            player = self.prospect_db.players.get(name)
            if not player:
                continue
            ok, _ = self._is_player_board_eligible(player)
            if ok:
                eligible.append(player)

        if not eligible:
            # Closest match exists but is ineligible; surface that reason
            primary_name = matches[0]
            primary = self.prospect_db.players.get(primary_name)
            if primary:
                ok, reason = self._is_player_board_eligible(primary)
                return False, f"Closest match is {primary_name}, but: {reason}", ""
            return False, "No eligible prospects match that name.", ""

        if len(eligible) == 1:
            player = eligible[0]
            return True, f"Interpreted '{q}' as {player['name']} and added to your board.", player["name"]

        # Multiple eligible matches – ask manager to be more specific
        options = ", ".join(p["name"] for p in eligible)
        msg = (
            "Multiple matching prospects found: "
            + options
            + ". Please re-run /add with the exact name."
        )
        return False, msg, ""
    
    @app_commands.command(name="board", description="View your draft board")
    async def board_cmd(self, interaction: discord.Interaction):
        """Show manager's current draft board"""
        
        team = self._get_team_for_user(interaction.user.id)
        if not team:
            await interaction.response.send_message(
                "❌ You are not mapped to a team",
                ephemeral=True
            )
            return
        
        board = self.board_manager.get_board(team)
        
        if not board:
            embed = discord.Embed(
                title=f"📋 {team} Draft Board",
                description="Your board is empty. Add players with `/add [player name]`",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="Board Capacity",
                value=f"0/{self.board_manager.MAX_BOARD_SIZE} players",
                inline=True
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Build board display
        embed = discord.Embed(
            title=f"📋 {team} Draft Board",
            description=f"Your target list ({len(board)} players)",
            color=discord.Color.green()
        )
        
        # Show players in groups of 25 (Discord field limit)
        for i in range(0, len(board), 25):
            chunk = board[i:i+25]
            chunk_text = "\n".join(
                f"{i+j+1}. {player}"
                for j, player in enumerate(chunk)
            )
            
            field_name = "Top Targets" if i == 0 else f"Targets {i+1}-{min(i+25, len(board))}"
            embed.add_field(
                name=field_name,
                value=chunk_text,
                inline=False
            )
        
        # Add board stats
        embed.add_field(
            name="Board Status",
            value=f"{len(board)}/{self.board_manager.MAX_BOARD_SIZE} players\n"
                  f"{self.board_manager.MAX_BOARD_SIZE - len(board)} slots remaining",
            inline=False
        )
        
        embed.set_footer(text="Use /add, /remove, /move to manage your board")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="add", description="Add player to your draft board")
    @app_commands.describe(player="Player name to add")
    async def add_cmd(self, interaction: discord.Interaction, player: str):
        """Add a player to manager's board"""
        
        team = self._get_team_for_user(interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Not mapped to a team", ephemeral=True)
            return
        
        # Resolve to an eligible, canonical prospect name (with fuzzy match)
        ok, msg, canonical_name = self._resolve_eligible_player(player)
        if not ok:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        success, message = self.board_manager.add_to_board(team, canonical_name)
        
        if success:
            board = self.board_manager.get_board(team)
            
            embed = discord.Embed(
                title="✅ Player Added",
                description=message,
                color=discord.Color.green()
            )
            
            # Show last 5 on board
            recent = board[-5:]
            board_text = "\n".join(
                f"{len(board) - len(recent) + i + 1}. {p}"
                for i, p in enumerate(recent)
            )
            
            embed.add_field(
                name=f"Your Board ({len(board)}/{self.board_manager.MAX_BOARD_SIZE})",
                value=board_text,
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)
    
    @app_commands.command(name="remove", description="Remove player from your draft board")
    @app_commands.describe(player="Player name to remove")
    async def remove_cmd(self, interaction: discord.Interaction, player: str):
        """Remove a player from manager's board"""
        
        team = self._get_team_for_user(interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Not mapped to a team", ephemeral=True)
            return
        
        success, message = self.board_manager.remove_from_board(team, player.strip())
        
        if success:
            board = self.board_manager.get_board(team)
            
            embed = discord.Embed(
                title="✅ Player Removed",
                description=message,
                color=discord.Color.orange()
            )
            
            embed.add_field(
                name="Board Status",
                value=f"{len(board)}/{self.board_manager.MAX_BOARD_SIZE} players remaining",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)
    
    @app_commands.command(name="move", description="Move player to different position on board")
    @app_commands.describe(
        player="Player name to move",
        position="New position (1 = top priority)"
    )
    async def move_cmd(
        self, 
        interaction: discord.Interaction, 
        player: str, 
        position: int
    ):
        """Move a player to a new position on board"""
        
        team = self._get_team_for_user(interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Not mapped to a team", ephemeral=True)
            return
        
        success, message = self.board_manager.move_player(team, player.strip(), position)
        
        if success:
            board = self.board_manager.get_board(team)
            
            embed = discord.Embed(
                title="✅ Player Moved",
                description=message,
                color=discord.Color.blue()
            )
            
            # Show area around new position
            start_idx = max(0, position - 3)
            end_idx = min(len(board), position + 2)
            chunk = board[start_idx:end_idx]
            
            board_text = "\n".join(
                f"{'➤ ' if start_idx + i + 1 == position else '   '}{start_idx + i + 1}. {p}"
                for i, p in enumerate(chunk)
            )
            
            embed.add_field(
                name="Board Preview",
                value=board_text,
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)
    
    @app_commands.command(name="clear", description="Clear your entire draft board")
    async def clear_cmd(self, interaction: discord.Interaction):
        """Clear manager's entire board"""
        
        team = self._get_team_for_user(interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Not mapped to a team", ephemeral=True)
            return
        
        board = self.board_manager.get_board(team)
        
        if not board:
            await interaction.response.send_message("ℹ️ Your board is already empty", ephemeral=True)
            return
        
        success, message = self.board_manager.clear_board(team)
        
        embed = discord.Embed(
            title="🗑️ Board Cleared",
            description=message,
            color=discord.Color.red()
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="addmany", description="Add multiple players to board (comma-separated)")
    @app_commands.describe(players="Comma-separated list of player names")
    async def addmany_cmd(self, interaction: discord.Interaction, players: str):
        """Bulk add players to board"""
        
        team = self._get_team_for_user(interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Not mapped to a team", ephemeral=True)
            return
        
        # Parse comma-separated list
        player_list = [p.strip() for p in players.split(',') if p.strip()]
        
        if not player_list:
            await interaction.response.send_message("❌ No players provided", ephemeral=True)
            return
        
        # Add each player
        added = []
        failed = []
        
        for player_name in player_list:
            ok, msg, canonical_name = self._resolve_eligible_player(player_name)
            if not ok:
                failed.append((player_name, msg))
                continue

            success, message = self.board_manager.add_to_board(team, canonical_name)
            if success:
                added.append(canonical_name)
            else:
                failed.append((player_name, message))
        
        # Build response
        embed = discord.Embed(
            title=f"📋 Bulk Add Results",
            color=discord.Color.green() if added else discord.Color.red()
        )
        
        if added:
            added_text = "\n".join(f"✅ {p}" for p in added)
            embed.add_field(
                name=f"Added ({len(added)} players)",
                value=added_text[:1024],  # Discord field limit
                inline=False
            )
        
        if failed:
            failed_text = "\n".join(f"❌ {p[0]}: {p[1]}" for p in failed[:10])
            if len(failed) > 10:
                failed_text += f"\n... and {len(failed) - 10} more"
            
            embed.add_field(
                name=f"Failed ({len(failed)} players)",
                value=failed_text[:1024],
                inline=False
            )
        
        # Show board status
        board_size = self.board_manager.get_board_size(team)
        embed.add_field(
            name="Board Status",
            value=f"{board_size}/{self.board_manager.MAX_BOARD_SIZE} players",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BoardCommands(bot))