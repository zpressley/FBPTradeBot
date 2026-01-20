"""
Draft Commands - Phase 3 Complete (Fixed)
Fixes:
- Removed Phase 4 hooks causing double-advance on picks
- Fixed pick order progression
- BoardManager integration for autopick
- Fuzzy matching in board commands
"""

import discord
from discord import app_commands
from discord.ext import commands
import sys
import os
import asyncio
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draft.draft_manager import DraftManager
from draft.pick_validator import PickValidator

TEST_USER_ID = 664280448788201522


class PickConfirmationView(discord.ui.View):
    """Interactive confirmation buttons for draft picks"""
    
    def __init__(self, draft_cog, team: str, player_data: dict, pick_info: dict):
        super().__init__(timeout=None)
        self.draft_cog = draft_cog
        self.team = team
        self.player = player_data
        self.pick_info = pick_info
        self.confirmed = False
    
    @discord.ui.button(label="✅ Confirm Pick", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.draft_cog.draft_manager.get_current_pick()
        if not current or current['team'] != self.team:
            await interaction.response.send_message("❌ No longer your turn", ephemeral=True)
            return
        
        try:
            # Record pick (this advances the draft)
            pick_record = self.draft_cog.draft_manager.make_pick(self.team, self.player['name'])
            self.confirmed = True
            
            # Cancel timer
            if self.draft_cog.pick_timer_task:
                self.draft_cog.pick_timer_task.cancel()
                self.draft_cog.pick_timer_task = None
            
            # Announce pick
            await self.draft_cog.announce_pick(interaction.channel, pick_record, self.player)
            
            # Start timer for NEXT pick
            await self.draft_cog.start_pick_timer(interaction.channel)
            
            # Update confirmation message
            await interaction.response.edit_message(content="✅ Pick confirmed!", embed=None, view=None)
            
            # Update UI
            if self.draft_cog.status_message:
                await self.draft_cog.update_status_message()
            if self.draft_cog.draft_board_thread:
                await self.draft_cog.update_draft_board()
            
            self.stop()
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"❌ **{self.team}** cancelled pick for **{self.player['name']}**",
            embed=None,
            view=None
        )
        self.stop()


class DraftCommands(commands.Cog):
    """Complete Discord integration for FBP Draft system - Phase 3"""
    
    def __init__(self, bot):
        self.bot = bot
        self.DRAFT_CHANNEL_ID = None
        self.ADMIN_ROLE_NAMES = ["Admin", "Commissioner"]
        # 4-minute pick clock (240s) with 1-minute warning.
        self.PICK_TIMER_DURATION = 240
        self.WARNING_TIME = 60
        
        self.TEST_MODE = True
        
        self.draft_manager = None
        self.pick_validator = None
        self.board_manager = None
        self.prospect_db = None
        self.pending_confirmations = {}
        self.status_message = None
        self.draft_board_thread = None
        self.draft_board_messages = {}
        self.pick_timer_task = None
        self.timer_start_time = None
        self.warning_sent = False
        
        print("✅ Draft commands loaded")
        if self.TEST_MODE:
            print(f"⚠️ TEST MODE - User {TEST_USER_ID} can pick for any team")
    
    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        user_roles = [role.name for role in interaction.user.roles]
        return any(role in self.ADMIN_ROLE_NAMES for role in user_roles)
    
    def _get_team_for_user(self, user_id: int) -> str:
        from commands.utils import DISCORD_ID_TO_TEAM
        return DISCORD_ID_TO_TEAM.get(user_id)
    
    def _get_user_for_team(self, team: str) -> int:
        from commands.utils import MANAGER_DISCORD_IDS
        return MANAGER_DISCORD_IDS.get(team)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for picks in draft channel AND DMs"""
        if message.author.bot:
            return
        
        # Check if draft is active
        if not self.draft_manager or self.draft_manager.state["status"] != "active":
            return
        
        current_pick = self.draft_manager.get_current_pick()
        if not current_pick:
            return
        
        # Determine if this is draft channel or DM
        is_draft_channel = (self.DRAFT_CHANNEL_ID and message.channel.id == self.DRAFT_CHANNEL_ID)
        is_dm = isinstance(message.channel, discord.DMChannel)
        
        # Only respond in draft channel or DMs
        if not is_draft_channel and not is_dm:
            return
        
        # TEST MODE: Allow test user to pick for current team
        if self.TEST_MODE and message.author.id == TEST_USER_ID:
            user_team = current_pick['team']
        else:
            user_team = self._get_team_for_user(message.author.id)
            if not user_team:
                return
        
        # Check if it's their turn
        if current_pick['team'] != user_team:
            # Not their turn - if in DM, show helpful message
            if is_dm:
                await message.channel.send(
                    f"⏰ Not your turn yet!\n\n"
                    f"Current pick: **{current_pick['team']}** (Pick {current_pick['pick']})\n"
                    f"You pick next at: {self._find_next_pick_for_team(user_team)}"
                )
            return
        
        player_input = message.content.strip()
        
        # Skip commands and very short messages
        if player_input.startswith('/') or player_input.startswith('!') or len(player_input) < 3:
            return
        
        # Show confirmation (DM or channel)
        await self.show_pick_confirmation(message.channel, message.author, user_team, player_input, current_pick, is_dm)
    
    def _find_next_pick_for_team(self, team: str) -> str:
        """Find when team picks next"""
        current_idx = self.draft_manager.current_pick_index
        
        for i in range(current_idx, len(self.draft_manager.draft_order)):
            if self.draft_manager.draft_order[i]['team'] == team:
                pick_info = self.draft_manager.draft_order[i]
                return f"Round {pick_info['round']}, Pick {pick_info['pick']}"
        
        return "No more picks"
    
    async def show_pick_confirmation(self, channel, user, team, player_input, pick_info, is_dm=False):
        """Show ephemeral confirmation card with validation and board suggestions"""
        
        # Validate pick if we have validator
        if self.pick_validator:
            valid, message, player_data = self.pick_validator.validate_pick(team, player_input)
            
            if not valid:
                # Show error
                error_msg = f"❌ {message}"
                
                # If in DM, can be more helpful
                if is_dm and self.board_manager:
                    board = self.board_manager.get_board(team)
                    drafted = [p['player'] for p in self.draft_manager.state["picks_made"]]
                    available = [p for p in board if p not in drafted]
                    
                    if available[:3]:
                        error_msg += f"\n\n**Your board (available):**\n"
                        error_msg += "\n".join(f"{i+1}. {p}" for i, p in enumerate(available[:3]))
                
                await channel.send(error_msg)
                return
            
            if not player_data:
                player_data = {"name": player_input, "position": "?", "team": "?", "rank": "?"}
        else:
            player_data = {"name": player_input, "position": "?", "team": "?", "rank": "?"}
        
        embed = discord.Embed(
            title="🎯 Confirm Your Pick",
            description=f"**{player_data['name']}**",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Pick Info",
            value=f"Round {pick_info['round']}, Pick {pick_info['pick']}\nType: {pick_info['round_type'].title()}",
            inline=True
        )
        
        player_info_parts = []
        if player_data.get('position') and player_data['position'] != '?':
            player_info_parts.append(f"Position: {player_data['position']}")
        if player_data.get('team') and player_data['team'] != '?':
            player_info_parts.append(f"MLB Team: {player_data['team']}")
        
        # Do not surface internal ownership codes like UC/PC/FC/DC in the UI
        # Prospect draft only shows unowned prospects, so contracts are implicit.
        
        if player_info_parts:
            embed.add_field(
                name="Player Info",
                value="\n".join(player_info_parts),
                inline=True
            )
        
        view = PickConfirmationView(self, team, player_data, pick_info)
        
        mention = user.mention if not self.TEST_MODE else f"**{team}**"
        
        # In DM, show board suggestions
        if is_dm and self.board_manager:
            board = self.board_manager.get_board(team)
            drafted = [p['player'] for p in self.draft_manager.state["picks_made"]]
            available = [p for p in board if p not in drafted]
            
            if available[:3]:
                suggestions = "\n\n**💡 Your board (top 3 available):**\n"
                suggestions += "\n".join(f"{i+1}. {p}" for i, p in enumerate(available[:3]))
                embed.description += suggestions
        
        await channel.send(content=mention, embed=embed, view=view, delete_after=600)
        
        if user.id in self.pending_confirmations:
            self.pending_confirmations[user.id].stop()
        self.pending_confirmations[user.id] = view
    
    async def announce_pick(self, channel, pick_record, player_data):
        """Announce confirmed pick publicly with better formatting"""
        
        # Main pick announcement
        pick_text = f"**Round {pick_record['round']}, Pick {pick_record['pick']}**\n"
        pick_text += f"**{pick_record['team']}** selects **{player_data['name']}**"
        
        # Player info on same line if available
        info_parts = []
        if player_data.get('position') and player_data['position'] != '?':
            info_parts.append(player_data['position'])
        if player_data.get('team') and player_data['team'] != '?':
            info_parts.append(f"[{player_data['team']}]")
        if player_data.get('rank') and player_data['rank'] != '?':
            info_parts.append(f"Rank #{player_data['rank']}")
        
        if info_parts:
            pick_text += "\n" + " • ".join(info_parts)
        
        # Visual separator (shortened for mobile)
        pick_text += "\n" + "─" * 35
        
        # Show CURRENT pick (draft already advanced after make_pick)
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            if self.TEST_MODE:
                next_display = f"**{current_pick['team']}**"
            else:
                next_user_id = self._get_user_for_team(current_pick['team'])
                next_display = f"<@{next_user_id}>" if next_user_id else current_pick['team']
            
            pick_text += f"\n\n**⏰ ON THE CLOCK**\n"
            pick_text += f"# {next_display}\n"
            pick_text += f"Pick {current_pick['pick']}"
            
            if not self.TEST_MODE:
                await self.notify_manager_on_clock(current_pick['team'])
        else:
            pick_text += f"\n\n🏁 **DRAFT COMPLETE!**"
        
        await channel.send(pick_text)
    
    async def start_pick_timer(self, channel):
        """Start pick timer for current pick (duration = PICK_TIMER_DURATION)."""
        if self.pick_timer_task:
            self.pick_timer_task.cancel()
        
        self.timer_start_time = datetime.now()
        self.warning_sent = False
        self.pick_timer_task = asyncio.create_task(self.pick_timer_countdown(channel))
    
    async def pick_timer_countdown(self, channel):
        """Timer countdown - warns at 2min, autopicks at 0"""
        try:
            elapsed = 0
            while elapsed < self.PICK_TIMER_DURATION:
                if self.draft_manager.state["status"] == "paused":
                    await asyncio.sleep(1)
                    continue
                
                await asyncio.sleep(1)
                elapsed = (datetime.now() - self.timer_start_time).total_seconds()
                
                if elapsed >= (self.PICK_TIMER_DURATION - self.WARNING_TIME) and not self.warning_sent:
                    await self.send_time_warning(channel)
                    self.warning_sent = True
                
                if int(elapsed) % 30 == 0 and self.status_message:
                    await self.update_status_message()
            
            await self.execute_autopick(channel)
            
        except asyncio.CancelledError:
            pass
    
    async def send_time_warning(self, channel):
        """Send warning near end of clock (WARNING_TIME seconds remaining)."""
        current_pick = self.draft_manager.get_current_pick()
        if not current_pick:
            return
        
        if self.TEST_MODE:
            team_display = f"**{current_pick['team']}**"
        else:
            user_id = self._get_user_for_team(current_pick['team'])
            team_display = f"<@{user_id}>" if user_id else current_pick['team']
        
        remaining_minutes = max(1, int(self.WARNING_TIME // 60))
        embed = discord.Embed(
            title=f"⚠️ {remaining_minutes} Minute Warning",
            description=f"{team_display} you have about {remaining_minutes} minute(s) to make your pick!",
            color=discord.Color.orange()
        )
        
        await channel.send(embed=embed)
        
        if not self.TEST_MODE:
            user_id = self._get_user_for_team(current_pick['team'])
            if user_id:
                try:
                    user = await self.bot.fetch_user(user_id)
                    await user.send(
                        f"⏰ **Clock almost up!**\n\n"
                        f"Round {current_pick['round']}, Pick {current_pick['pick']}\n"
                        f"Make your pick now!"
                    )
                except:
                    pass
    
    async def execute_autopick(self, channel):
        """Execute autopick when timer expires.

        Rules:
        - Always prefer a valid player from the manager's board.
        - In rounds 1–2 (FYPD rounds), fallback must be the top *FYPD* player
          from the universal pool.
        - In later rounds, fallback is the top eligible prospect overall.
        - All autopicks MUST pass PickValidator (same rules as manual picks).
        """
        current_pick = self.draft_manager.get_current_pick()
        if not current_pick:
            return
        
        team = current_pick['team']
        round_num = current_pick['round']
        
        # Helper to test whether a candidate is valid under PickValidator.
        def _is_valid_autopick(name: str):
            if not self.pick_validator:
                # Shouldn't happen in prospect draft, but be defensive.
                return True, None
            valid, _msg, player = self.pick_validator.validate_pick(team, name)
            return valid, player
        
        autopicked_name = None
        player_data = None
        source = "universal board"
        
        # 1) Try manager's personal board, in order.
        if self.board_manager:
            board = self.board_manager.get_board(team)
            for candidate in board:
                valid, player = _is_valid_autopick(candidate)
                if valid:
                    autopicked_name = player["name"] if player else candidate
                    player_data = player
                    source = f"{team}'s board"
                    break
        
        # 2) Fall back to universal pool from ProspectDatabase.
        if not autopicked_name and self.prospect_db:
            candidates = list(self.prospect_db.players.values())
            
            # In FYPD rounds, restrict to FYPD pool.
            if self.pick_validator and round_num in self.pick_validator.FYPD_ROUNDS:
                candidates = [p for p in candidates if p.get("fypd")]
                # Round 1 uses dedicated FYPD_rank; later rounds use global rank.
                if round_num == min(self.pick_validator.FYPD_ROUNDS):
                    source_label = "FYPD rankings (top eligible)"
                    rank_field = "fypd_rank"
                else:
                    source_label = "universal board (top eligible)"
                    rank_field = "rank"
            else:
                source_label = "universal board (top eligible)"
                rank_field = "rank"
            
            # Sort by the chosen rank field; unknown ranks go last.
            def _rank_key(p):
                r = p.get(rank_field)
                return r if isinstance(r, int) else 9999
            candidates.sort(key=_rank_key)
            
            for p in candidates:
                name = p["name"]
                valid, player = _is_valid_autopick(name)
                if valid:
                    autopicked_name = player["name"] if player else name
                    player_data = player or p
                    source = source_label
                    break
        
        # 3) Last resort placeholder if absolutely nothing is valid.
        if not autopicked_name:
            autopicked_name = "[AUTOPICK - No eligible players available]"
            player_data = {"name": autopicked_name, "position": "?", "team": "?", "rank": "?"}
            source = "ERROR: no eligible players"
        
        # Ensure we have a player_data dict for announcement.
        if not player_data:
            player_data = {"name": autopicked_name, "position": "?", "team": "?", "rank": "?"}
        
        # Record the pick (this advances draft)
        pick_record = self.draft_manager.make_pick(team, autopicked_name)
        
        # Announce autopick
        pick_text = f"**⏰ Round {pick_record['round']}, Pick {pick_record['pick']} - AUTOPICK**\n"
        pick_text += f"**{team}** time expired\n"
        pick_text += f"Autopicked: **{autopicked_name}**\n"
        
        # Add player info if available
        info_parts = []
        if player_data.get('position') and player_data['position'] != '?':
            info_parts.append(player_data['position'])
        if player_data.get('team') and player_data['team'] != '?':
            info_parts.append(f"[{player_data['team']}]")
        
        if info_parts:
            pick_text += " • ".join(info_parts) + "\n"
        
        pick_text += f"Source: {source}\n"
        pick_text += "─" * 35
        
        # Show CURRENT pick (draft already advanced)
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            if self.TEST_MODE:
                next_display = f"**{current_pick['team']}**"
            else:
                next_user_id = self._get_user_for_team(current_pick['team'])
                next_display = f"<@{next_user_id}>" if next_user_id else current_pick['team']
            
            pick_text += f"\n\n**⏰ ON THE CLOCK**\n"
            pick_text += f"# {next_display}\n"
            pick_text += f"Pick {current_pick['pick']}"
        
        await channel.send(pick_text)
        
        if self.status_message:
            await self.update_status_message()
        if self.draft_board_thread:
            await self.update_draft_board()
        
        # Start timer for next pick
        await self.start_pick_timer(channel)
    
    async def create_status_message(self, channel):
        """Create and pin live status message"""
        embed = self.build_status_embed()
        self.status_message = await channel.send(embed=embed)
        try:
            await self.status_message.pin()
        except:
            pass
        return self.status_message
    
    async def update_status_message(self):
        """Update pinned status message"""
        if not self.status_message:
            return
        embed = self.build_status_embed()
        try:
            await self.status_message.edit(embed=embed)
        except:
            pass
    
    def build_status_embed(self):
        """Build status embed with current draft info"""
        progress = self.draft_manager.get_draft_progress()
        current_pick = self.draft_manager.get_current_pick()
        
        status_emoji = {"not_started": "⏸️", "active": "🟢", "paused": "⏸️", "completed": "✅"}
        emoji = status_emoji.get(progress['status'], "❓")
        
        season = getattr(self.draft_manager, "season", 2026)
        title = f"{emoji} {season} {self.draft_manager.draft_type.upper()} DRAFT"
        if self.TEST_MODE:
            title += " [TEST MODE]"
        
        embed = discord.Embed(
            title=title,
            color=discord.Color.green() if progress['status'] == 'active' else discord.Color.orange()
        )
        
        if current_pick:
            embed.add_field(
                name="Current Round",
                value=f"Round {current_pick['round']} ({current_pick['round_type'].title()})",
                inline=True
            )
        
        embed.add_field(
            name="Progress",
            value=f"{progress['picks_made']}/{progress['total_picks']} picks",
            inline=True
        )
        
        if self.timer_start_time and progress['status'] == 'active':
            elapsed = (datetime.now() - self.timer_start_time).total_seconds()
            remaining = max(0, self.PICK_TIMER_DURATION - elapsed)
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            embed.add_field(name="⏱️ Time", value=f"{minutes}:{seconds:02d}", inline=True)
        
        if current_pick:
            team_display = f"**{current_pick['team']}**"
            embed.add_field(
                name="⏰ ON THE CLOCK",
                value=f"# {team_display}\nPick {current_pick['pick']}",
                inline=False
            )
        
        next_pick = self.draft_manager.get_next_pick()
        after_next = self.draft_manager.get_pick_after_next()
        
        next_info = []
        if next_pick:
            next_info.append(f"On Deck: **{next_pick['team']}** (Pick {next_pick['pick']})")
        if after_next:
            next_info.append(f"In Hole: **{after_next['team']}** (Pick {after_next['pick']})")
        
        if next_info:
            embed.add_field(name="Up Next", value="\n".join(next_info), inline=False)
        
        recent = self.draft_manager.state["picks_made"][-3:]
        if recent:
            picks_text = "\n".join(f"• {p['team']} - {p['player']}" for p in reversed(recent))
            embed.add_field(name="Recent Picks", value=picks_text, inline=False)
        
        embed.set_footer(text=f"Updated: {datetime.now().strftime('%I:%M:%S %p')}")
        return embed
    
    async def create_draft_board_thread(self, channel):
        """Create thread for full draft board"""
        season = getattr(self.draft_manager, "season", 2026)
        thread = await channel.create_thread(
            name=f"📊 {season} Draft Board",
            type=discord.ChannelType.public_thread
        )
        
        await thread.send(
            "📋 **DRAFT BOARD**\n\n"
            "This thread shows all picks as they're made.\n"
            "Updates automatically after each selection."
        )
        
        self.draft_board_thread = thread
        self.draft_board_messages = {}
        return thread
    
    async def update_draft_board(self):
        """Update draft board thread - one message per round, edits in place"""
        if not self.draft_board_thread:
            return
        
        current_pick = self.draft_manager.get_current_pick()
        if not current_pick:
            return
        
        current_round = current_pick['round']
        
        # Get all picks for current round
        current_round_picks = [p for p in self.draft_manager.draft_order if p['round'] == current_round]
        made_picks = [p for p in self.draft_manager.state["picks_made"] if p['round'] == current_round]
        
        # Build content for this round
        content_parts = []
        content_parts.append("═" * 50)
        
        # Round header with status
        total_in_round = len(current_round_picks)
        made_in_round = len(made_picks)
        
        if made_in_round == total_in_round:
            status = "✅ COMPLETE"
        elif made_in_round > 0:
            status = f"⏳ IN PROGRESS ({made_in_round}/{total_in_round})"
        else:
            status = "⏸️ NOT STARTED"
        
        content_parts.append(f"**ROUND {current_round}** ({current_round_picks[0]['round_type'].upper()}) {status}")
        content_parts.append("─" * 50)
        
        # List all picks in this round
        for pick_info in current_round_picks:
            pick_num = pick_info['pick']
            team = pick_info['team']
            
            # Check if this pick was made
            made_pick = next((p for p in made_picks if p['pick'] == pick_num), None)
            
            if made_pick:
                content_parts.append(f"{pick_num:3d}. {team} - {made_pick['player']}")
            elif pick_num == current_pick['pick']:
                content_parts.append(f"{pick_num:3d}. {team} - ⏰ **ON THE CLOCK**")
            else:
                content_parts.append(f"{pick_num:3d}. {team}")
        
        content_parts.append("═" * 50)
        content = "\n".join(content_parts)
        
        # Check if we already have a message for this round
        if current_round in self.draft_board_messages:
            # Edit existing message
            try:
                msg_id = self.draft_board_messages[current_round]
                message = await self.draft_board_thread.fetch_message(msg_id)
                await message.edit(content=content)
            except:
                # Message not found, create new one
                msg = await self.draft_board_thread.send(content)
                self.draft_board_messages[current_round] = msg.id
        else:
            # Create new message for this round
            msg = await self.draft_board_thread.send(content)
            self.draft_board_messages[current_round] = msg.id
    
    async def notify_manager_on_clock(self, team: str):
        """DM manager when it's their turn with board suggestions"""
        if self.TEST_MODE:
            return
        
        user_id = self._get_user_for_team(team)
        if not user_id:
            return
        
        current_pick = self.draft_manager.get_current_pick()
        if not current_pick:
            return
        
        try:
            user = await self.bot.fetch_user(user_id)
            
            embed = discord.Embed(
                title="⏰ You're On the Clock!",
                description=f"Round {current_pick['round']}, Pick {current_pick['pick']}",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="How to Pick", 
                value="Type player name in draft channel or reply here in DM",
                inline=False
            )
            
            embed.add_field(name="Time Limit", value="10 minutes", inline=True)
            embed.add_field(
                name="Round Type",
                value=current_pick['round_type'].title(),
                inline=True
            )
            
            # Add board suggestions if available
            if self.board_manager:
                board = self.board_manager.get_board(team)
                drafted = [p['player'] for p in self.draft_manager.state["picks_made"]]
                available = [p for p in board if p not in drafted]
                
                if available[:5]:
                    board_text = "\n".join(f"{i+1}. {p}" for i, p in enumerate(available[:5]))
                    embed.add_field(
                        name="💡 Your Board (Top 5 Available)",
                        value=board_text,
                        inline=False
                    )
            
            await user.send(embed=embed)
            
        except Exception as e:
            print(f"⚠️ Could not DM {team}: {e}")
    
    @app_commands.command(name="draft", description="Draft management")
    @app_commands.describe(action="What to do", draft_type="Type of draft")
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="pause", value="pause"),
        app_commands.Choice(name="continue", value="continue"),
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="undo", value="undo"),
        app_commands.Choice(name="order", value="order"),
    ])
    @app_commands.choices(draft_type=[
        app_commands.Choice(name="Prospect Draft", value="prospect"),
        app_commands.Choice(name="Keeper Draft", value="keeper"),
    ])
    async def draft_cmd(self, interaction: discord.Interaction, action: app_commands.Choice[str], draft_type: app_commands.Choice[str] = None):
        action_value = action.value
        admin_actions = ["start", "pause", "continue", "undo"]
        
        if action_value in admin_actions and not self._is_admin(interaction):
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        if action_value == "start":
            await self._handle_start(interaction, draft_type)
        elif action_value == "pause":
            await self._handle_pause(interaction)
        elif action_value == "continue":
            await self._handle_continue(interaction)
        elif action_value == "status":
            await self._handle_status(interaction)
        elif action_value == "undo":
            await self._handle_undo(interaction)
        elif action_value == "order":
            await self._handle_order(interaction)
    
    async def _handle_start(self, interaction, draft_type):
        if draft_type is None:
            await interaction.response.send_message("❌ Specify draft type", ephemeral=True)
            return
        
        if self.draft_manager and self.draft_manager.state["status"] == "active":
            await interaction.response.send_message("❌ Draft already active", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        try:
            # Use 2026 season for both keeper and prospect drafts
            self.draft_manager = DraftManager(draft_type=draft_type.value, season=2026)
            
            # Initialize BoardManager for autopick
            from draft.board_manager import BoardManager
            self.board_manager = BoardManager(season=2026)
            
            # Initialize ProspectDatabase for validation
            from draft.prospect_database import ProspectDatabase
            self.prospect_db = ProspectDatabase(season=2026, draft_type=draft_type.value)
            
            # Initialize PickValidator
            self.pick_validator = PickValidator(self.prospect_db, self.draft_manager)
            
            if not self.DRAFT_CHANNEL_ID:
                self.DRAFT_CHANNEL_ID = interaction.channel.id
            
            self.draft_manager.start_draft()
            current_pick = self.draft_manager.get_current_pick()
            
            embed = discord.Embed(
                title=f"🏟️ 2026 {draft_type.value.upper()} DRAFT STARTING",
                description="Draft is now live!" + (" [TEST MODE]" if self.TEST_MODE else ""),
                color=discord.Color.green()
            )
            
            team_display = f"**{current_pick['team']}**"
            
            embed.add_field(
                name="First Pick",
                value=f"Round {current_pick['round']}, Pick {current_pick['pick']}\n{team_display} is on the clock",
                inline=False
            )
            
            embed.add_field(name="How to Pick", value="Type player name in this channel", inline=False)
            
            progress = self.draft_manager.get_draft_progress()
            embed.add_field(name="Total Picks", value=f"{progress['total_picks']} picks", inline=True)
            embed.add_field(name="Timer", value="10 minutes per pick", inline=True)
            
            # Show database info
            if self.prospect_db:
                embed.add_field(
                    name="Database",
                    value=f"{len(self.prospect_db.players)} players loaded",
                    inline=True
                )
            
            await interaction.followup.send(embed=embed)
            
            await self.create_status_message(interaction.channel)
            await self.create_draft_board_thread(interaction.channel)
            await self.start_pick_timer(interaction.channel)
            
        except FileNotFoundError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    async def _handle_pause(self, interaction):
        if not self.draft_manager or self.draft_manager.state["status"] != "active":
            await interaction.response.send_message("❌ No active draft", ephemeral=True)
            return
        
        self.draft_manager.pause_draft()
        
        if self.pick_timer_task:
            self.pick_timer_task.cancel()
            self.pick_timer_task = None
        
        embed = discord.Embed(title="⏸️ Draft Paused", color=discord.Color.orange())
        
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            embed.add_field(name="Next Pick", value=f"{current_pick['team']} - Pick {current_pick['pick']}", inline=False)
        
        await interaction.response.send_message(embed=embed)
        
        if self.status_message:
            await self.update_status_message()
    
    async def _handle_continue(self, interaction):
        if not self.draft_manager or self.draft_manager.state["status"] != "paused":
            await interaction.response.send_message("❌ No paused draft", ephemeral=True)
            return
        
        self.draft_manager.resume_draft()
        
        embed = discord.Embed(title="▶️ Draft Resumed", color=discord.Color.green())
        
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            team_display = f"**{current_pick['team']}**"
            embed.add_field(name="On Clock", value=f"{team_display} - Pick {current_pick['pick']}", inline=False)
        
        await interaction.response.send_message(embed=embed)
        await self.start_pick_timer(interaction.channel)
        
        if self.status_message:
            await self.update_status_message()
    
    async def _handle_status(self, interaction):
        if not self.draft_manager:
            await interaction.response.send_message("ℹ️ No draft loaded", ephemeral=True)
            return
        
        embed = self.build_status_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def _handle_undo(self, interaction):
        if not self.draft_manager:
            await interaction.response.send_message("❌ No draft", ephemeral=True)
            return
        
        undone = self.draft_manager.undo_last_pick()
        if not undone:
            await interaction.response.send_message("❌ No picks to undo", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="↩️ Pick Undone",
            description=f"Removed: **{undone['team']}** - {undone['player']}",
            color=discord.Color.orange()
        )
        
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            team_display = f"**{current_pick['team']}**"
            embed.add_field(name="Back on Clock", value=f"{team_display} - Pick {current_pick['pick']}", inline=False)
        
        await interaction.response.send_message(embed=embed)
        
        if self.draft_manager.state["status"] == "active":
            await self.start_pick_timer(interaction.channel)
        
        if self.status_message:
            await self.update_status_message()
        if self.draft_board_thread:
            await self.update_draft_board()
    
    async def _handle_order(self, interaction):
        if not self.draft_manager:
            await interaction.response.send_message("❌ No draft", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"📋 {self.draft_manager.draft_type.upper()} Draft Order",
            description="First 3 rounds:",
            color=discord.Color.blue()
        )
        
        for round_num in [1, 2, 3]:
            round_picks = [p for p in self.draft_manager.draft_order if p['round'] == round_num]
            if round_picks:
                teams = [p['team'] for p in round_picks]
                made = [p for p in self.draft_manager.state["picks_made"] if p['round'] == round_num]
                status = f" ({len(made)}/{len(round_picks)} picked)" if made else ""
                
                embed.add_field(
                    name=f"Round {round_num} ({round_picks[0]['round_type'].title()}){status}",
                    value=" → ".join(teams),
                    inline=False
                )
        
        progress = self.draft_manager.get_draft_progress()
        embed.set_footer(text=f"Total: {progress['total_picks']} picks")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    


async def setup(bot):
    await bot.add_cog(DraftCommands(bot))