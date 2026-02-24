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
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draft.draft_manager import DraftManager
from draft.pick_validator import PickValidator
from draft.forklift_manager import ForkliftManager

# In TEST_MODE, these user IDs can submit picks for whatever team is on the clock.
# (Useful for validating website/Discord pick flows without having to login as every manager.)
TEST_USER_IDS = {
    664280448788201522,  # WAR (existing test user)
    161967242118955008,  # WIZ
    875750135005597728,  # SAD
}


class PickConfirmationView(discord.ui.View):
    """Interactive confirmation buttons for draft picks.

    NOTE: Discord "ephemeral" messages are only available for Interaction
    responses (slash commands/buttons). For message-based picks, we emulate
    a hidden confirmation by sending the confirmation UI to the manager via DM.
    """

    def __init__(
        self,
        draft_cog,
        team: str,
        player_data: dict,
        pick_info: dict,
        allowed_user_ids: set[int],
    ):
        super().__init__(timeout=None)
        self.draft_cog = draft_cog
        self.team = team
        self.player = player_data
        self.pick_info = pick_info
        self.allowed_user_ids = allowed_user_ids
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the intended manager/tester can confirm/cancel."""
        if interaction.user and interaction.user.id in self.allowed_user_ids:
            return True

        msg = "❌ Only the manager who submitted this pick can confirm/cancel it."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass
        return False
    
    @discord.ui.button(label="✅ Confirm Pick", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Guard against double-clicks / race conditions.
        if self.confirmed:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("ℹ️ This pick has already been processed.", ephemeral=True)
                else:
                    await interaction.response.send_message("ℹ️ This pick has already been processed.", ephemeral=True)
            except Exception:
                pass
            return

        current = self.draft_cog.draft_manager.get_current_pick()
        if not current or current["team"] != self.team:
            # Pick is no longer actionable; disable buttons to prevent spam.
            for item in self.children:
                item.disabled = True
            try:
                await interaction.response.edit_message(content="❌ No longer your turn", embed=None, view=None)
            except Exception:
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send("❌ No longer your turn", ephemeral=True)
                    else:
                        await interaction.response.send_message("❌ No longer your turn", ephemeral=True)
                except Exception:
                    pass
            self.stop()
            return

        # Immediately disable buttons and acknowledge the click so we don't
        # hit Discord's interaction timeout while we persist state + announce.
        self.confirmed = True
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(content="⏳ Confirming pick...", view=self)
        except Exception:
            # If we can't edit immediately, still continue.
            pass

        try:
            # Record pick (this advances the draft). Pass full player record
            # so DraftManager can persist UPID/metadata into state and order.
            pick_record = self.draft_cog.draft_manager.make_pick(
                self.team,
                self.player["name"],
                self.player,
            )

            # Cancel timer
            if self.draft_cog.pick_timer_task:
                self.draft_cog.pick_timer_task.cancel()
                self.draft_cog.pick_timer_task = None

            # Announce pick in the *draft channel* even if the confirmation UI
            # was shown in DMs.
            announce_channel = interaction.channel
            if self.draft_cog.DRAFT_CHANNEL_ID:
                try:
                    announce_channel = self.draft_cog.bot.get_channel(self.draft_cog.DRAFT_CHANNEL_ID)
                    if announce_channel is None:
                        announce_channel = await self.draft_cog.bot.fetch_channel(self.draft_cog.DRAFT_CHANNEL_ID)
                except Exception:
                    announce_channel = interaction.channel

            await self.draft_cog.announce_pick(announce_channel, pick_record, self.player)

            # Update confirmation message (remove buttons)
            try:
                await interaction.edit_original_response(content="✅ Pick confirmed!", embed=None, view=None)
            except Exception:
                try:
                    await interaction.message.edit(content="✅ Pick confirmed!", embed=None, view=None)
                except Exception:
                    pass

            # Update draft board thread (status card + timer are handled
            # inside announce_pick and the timer loop).
            if self.draft_cog.draft_board_thread:
                await self.draft_cog.update_draft_board()

            self.stop()

        except Exception as e:
            # On error, still remove buttons so the user can't keep clicking.
            try:
                await interaction.message.edit(content=f"❌ Error confirming pick: {str(e)}", embed=None, view=None)
            except Exception:
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(f"❌ Error confirming pick: {str(e)}", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"❌ Error confirming pick: {str(e)}", ephemeral=True)
                except Exception:
                    pass
            self.stop()
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Disable buttons immediately to avoid double-click spam.
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(
                content=f"❌ **{self.team}** cancelled pick for **{self.player['name']}**",
                embed=None,
                view=None,
            )
        except Exception:
            try:
                await interaction.message.edit(
                    content=f"❌ **{self.team}** cancelled pick for **{self.player['name']}**",
                    embed=None,
                    view=None,
                )
            except Exception:
                pass

        self.stop()


class DraftCommands(commands.Cog):
    """Complete Discord integration for FBP Draft system - Phase 3"""
    
    def __init__(self, bot):
        self.bot = bot
        self.DRAFT_CHANNEL_ID = None
        self.ADMIN_ROLE_NAMES = ["Admin", "Commissioner"]
        # Default pick clock (seconds). Forklift mode may override per-team.
        self.PICK_TIMER_DURATION = 240
        self.WARNING_TIME = 60
        self.current_timer_duration = self.PICK_TIMER_DURATION
        
        # Draft test mode (defaults to OFF). When enabled, designated testers
        # can submit picks for whichever team is on the clock.
        self.TEST_MODE = os.getenv("DRAFT_TEST_MODE", "false").lower() == "true"
        
        self.draft_manager = None
        self.pick_validator = None
        self.board_manager = None
        self.prospect_db = None
        self.forklift_manager: ForkliftManager | None = None
        self.pending_confirmations = {}
        self.status_message = None
        self.draft_board_thread = None
        self.draft_board_messages = {}
        self.pick_timer_task = None
        self.timer_start_time = None
        self.warning_sent = False
        
        print("✅ Draft commands loaded")
        if self.TEST_MODE:
            print(f"⚠️ TEST MODE - Users {sorted(TEST_USER_IDS)} can pick for any team")
    
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

    def _ensure_forklift_manager(self, *, season: int = 2026, draft_type: str = "prospect") -> None:
        """Ensure self.forklift_manager is initialized for current season/type."""
        if self.forklift_manager is not None:
            return

        # Prefer draft_manager's configuration if available.
        if self.draft_manager is not None:
            season = getattr(self.draft_manager, "season", season)
            draft_type = getattr(self.draft_manager, "draft_type", draft_type)

        self.forklift_manager = ForkliftManager(season=season, draft_type=draft_type)

    def _is_forklift_team(self, team: str) -> bool:
        if not team:
            return False

        # Forklift mode is only intended for the prospect draft (board-driven).
        if self.draft_manager is not None and getattr(self.draft_manager, "draft_type", None) != "prospect":
            return False

        if self.forklift_manager is None:
            return False
        return self.forklift_manager.is_forklift_enabled(team)

    def _get_pick_timer_duration(self, team: str) -> int:
        """Return per-team clock duration in seconds."""
        if self._is_forklift_team(team):
            return self.forklift_manager.get_timer_duration(team)  # type: ignore[union-attr]
        return self.PICK_TIMER_DURATION
    
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
        
        # TEST MODE: Allow test admins to pick for current team
        if self.TEST_MODE and message.author.id in TEST_USER_IDS:
            user_team = current_pick['team']
        else:
            user_team = self._get_team_for_user(message.author.id)
            if not user_team:
                return
        
        # Check if it's their turn
        if current_pick["team"] != user_team:
            # Message-based picks can't be truly ephemeral, so we DM the user
            # with a "not your turn" notice.
            msg = (
                f"⏰ Not your turn yet!\n\n"
                f"Current pick: **{current_pick['team']}** (Pick {current_pick['pick']})\n"
                f"You pick next at: {self._find_next_pick_for_team(user_team)}"
            )
            if is_dm:
                await message.channel.send(msg)
            elif is_draft_channel:
                try:
                    dm = await message.author.create_dm()
                    await dm.send(msg)
                except Exception:
                    pass
            return
        
        player_input = message.content.strip()
        
        # Skip commands and very short messages
        if player_input.startswith('/') or player_input.startswith('!') or len(player_input) < 3:
            return
        
        # Show confirmation.
        # - If the user typed in the draft channel, send the confirmation UI
        #   to their DMs (hidden).
        # - If they typed in DM, keep it in DM.
        delivery = "dm" if is_draft_channel and not is_dm else "channel"
        await self.show_pick_confirmation(
            message.channel,
            message.author,
            user_team,
            player_input,
            current_pick,
            is_dm,
            delivery=delivery,
        )
    
    def _find_next_pick_for_team(self, team: str) -> str:
        """Find when team picks next"""
        current_idx = self.draft_manager.current_pick_index
        
        for i in range(current_idx, len(self.draft_manager.draft_order)):
            if self.draft_manager.draft_order[i]['team'] == team:
                pick_info = self.draft_manager.draft_order[i]
                return f"Round {pick_info['round']}, Pick {pick_info['pick']}"
        
        return "No more picks"
    
    async def show_pick_confirmation(
        self,
        channel,
        user,
        team,
        player_input,
        pick_info,
        is_dm=False,
        delivery: str = "channel",
    ):
        """Show a confirmation card with validation and board suggestions.

        delivery:
          - "channel": post confirmation in the provided channel
          - "dm": send confirmation to the user's DMs (hidden)
        """

        if delivery == "dm":
            try:
                channel = await user.create_dm()
                is_dm = True
            except Exception:
                # If DM creation fails (privacy settings), fall back to channel.
                is_dm = False
        
        # Validate pick if we have validator
        if self.pick_validator:
            valid, message, player_data = self.pick_validator.validate_pick(team, player_input)
            
            if not valid:
                # Show error
                error_msg = f"❌ {message}"
                
                # If in DM, can be more helpful
                if is_dm and self.board_manager:
                    resolved = self.board_manager.resolve_board(team)
                    drafted = set(p['player'].lower() for p in self.draft_manager.state["picks_made"])
                    available = [e['name'] for e in resolved if e['name'].lower() not in drafted]
                    
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
        
        allowed_user_ids = {user.id}
        if not is_dm and self.TEST_MODE:
            # In test mode, allow designated testers (and the mapped manager)
            # to confirm picks that were posted publicly in the draft channel
            # (e.g. website flow).
            allowed_user_ids = set(TEST_USER_IDS) | {user.id}

        view = PickConfirmationView(
            self,
            team,
            player_data,
            pick_info,
            allowed_user_ids=allowed_user_ids,
        )

        # In DMs, no need to ping/mention.
        mention = None if is_dm else (user.mention if not self.TEST_MODE else f"**{team}**")
        
        # In DM, show board suggestions
        if is_dm and self.board_manager:
            resolved = self.board_manager.resolve_board(team)
            drafted = set(p['player'].lower() for p in self.draft_manager.state["picks_made"])
            available = [e['name'] for e in resolved if e['name'].lower() not in drafted]
            
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
        
        # First message: pick summary (no on-the-clock info here).
        await channel.send(pick_text)
        
        # Second message: status / ON THE CLOCK card for the NEXT pick.
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            if not self.TEST_MODE:
                await self.notify_manager_on_clock(current_pick['team'])
            await self.start_pick_timer(channel)
            await self.post_on_clock_status(channel)
        else:
            # Draft complete after this pick.
            await channel.send("🏁 **DRAFT COMPLETE!**")
    
    async def start_pick_timer(self, channel):
        """Start pick timer for the current pick.

        Duration is dynamic:
        - normal teams: PICK_TIMER_DURATION (default 240s)
        - forklift teams: 10s

        We persist both the clock start time and the duration so the website can
        compute the countdown accurately.
        """
        # Avoid cancelling the currently-running timer task if this method is
        # called from inside the timer itself (e.g. forklift board empty → restart
        # clock with normal duration).
        current_task = asyncio.current_task()
        if self.pick_timer_task and self.pick_timer_task != current_task:
            self.pick_timer_task.cancel()

        current_pick = self.draft_manager.get_current_pick() if self.draft_manager else None
        if not current_pick:
            return

        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type if self.draft_manager else "prospect")

        team = current_pick["team"]
        duration = self._get_pick_timer_duration(team)
        self.current_timer_duration = duration

        self.timer_start_time = datetime.now()
        self.warning_sent = False

        # Persist timer start to state so website can sync countdown
        self.draft_manager.state["timer_started_at"] = self.timer_start_time.isoformat()
        self.draft_manager.state["timer_duration_seconds"] = duration
        self.draft_manager.save_state()

        # Persist to GitHub so restarts don't lose the clock.
        try:
            self.draft_manager._commit_draft_files_async(
                [self.draft_manager.state_file],
                f"Draft clock started: {self.draft_manager.draft_type} {getattr(self.draft_manager, 'season', '')}",
            )
        except Exception:
            pass

        self.pick_timer_task = asyncio.create_task(self.pick_timer_countdown(channel, duration))
    
    async def pick_timer_countdown(self, channel, duration_seconds: int):
        """Timer countdown.

        - For normal teams: warns near end of clock (WARNING_TIME remaining)
        - For forklift teams (10s): we skip the warning to avoid noise
        """
        try:
            elapsed = 0
            refresh_interval = 1 if duration_seconds <= 15 else 30

            while elapsed < duration_seconds:
                if self.draft_manager.state["status"] == "paused":
                    await asyncio.sleep(1)
                    continue

                await asyncio.sleep(1)
                elapsed = (datetime.now() - self.timer_start_time).total_seconds()

                if (
                    duration_seconds > self.WARNING_TIME
                    and elapsed >= (duration_seconds - self.WARNING_TIME)
                    and not self.warning_sent
                ):
                    await self.send_time_warning(channel)
                    self.warning_sent = True

                # Refresh status embed periodically so the time bucket stays
                # reasonably up to date.
                if int(elapsed) % refresh_interval == 0 and self.status_message:
                    try:
                        embed = self.build_status_embed()
                        await self.status_message.edit(embed=embed)
                    except Exception:
                        self.status_message = None

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

        Normal autopick rules:
        - Prefer a valid player from the manager's board.
        - In rounds 1–2 (FYPD rounds), fallback must be the top *FYPD* player
          from the universal pool.
        - In later rounds, fallback is the top eligible prospect overall.
        - All autopicks MUST pass PickValidator (same rules as manual picks).

        Forklift mode rules:
        - 10-second clock
        - Board-only autopick. If the board is empty/depleted/invalid, we
          auto-disable forklift mode and restart the clock in normal mode.
        """
        current_pick = self.draft_manager.get_current_pick()
        if not current_pick:
            return

        team = current_pick["team"]
        round_num = current_pick["round"]

        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type)
        forklift_enabled = self._is_forklift_team(team)

        # Helper to test whether a candidate is valid under PickValidator.
        def _is_valid_autopick(name: str):
            if not self.pick_validator:
                return True, None
            valid, _msg, player = self.pick_validator.validate_pick(team, name)
            return valid, player

        autopicked_name = None
        player_data = None
        source = "universal board"

        # Forklift mode: board-only.
        if forklift_enabled:
            resolved_board = self.board_manager.resolve_board(team) if self.board_manager else []

            for entry in resolved_board or []:
                candidate_name = entry["name"]
                valid, player = _is_valid_autopick(candidate_name)
                if valid:
                    autopicked_name = player["name"] if player else candidate_name
                    player_data = player
                    source = f"{team}'s board (forklift)"
                    break

            if not autopicked_name:
                # Nothing valid to pick from board. Disable forklift and restart clock.
                try:
                    ok, _msg = self.forklift_manager.disable_forklift(team, disabled_by="autopick")  # type: ignore[union-attr]
                    if ok and self.draft_manager:
                        try:
                            self.draft_manager._commit_draft_files_async(
                                [self.forklift_manager.state_file],
                                f"Forklift disabled: {team} ({self.draft_manager.draft_type} {self.draft_manager.season})",
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

                await channel.send(
                    f"<:forklift:1068270227344867358> Forklift Mode could not auto-pick for **{team}** (empty/depleted/invalid board).\n"
                    f"Forklift Mode disabled — please pick manually. Restarting clock with 4 minutes."
                )

                # Restart clock for the SAME pick, now in normal mode.
                self.current_timer_duration = self.PICK_TIMER_DURATION
                await self.start_pick_timer(channel)
                await self.post_on_clock_status(channel)
                return

        else:
            # 1) Try manager's personal board, in order.
            if self.board_manager:
                resolved_board = self.board_manager.resolve_board(team)
                for entry in resolved_board:
                    candidate_name = entry["name"]
                    valid, player = _is_valid_autopick(candidate_name)
                    if valid:
                        autopicked_name = player["name"] if player else candidate_name
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

        pick_record = self.draft_manager.make_pick(team, autopicked_name, player_data)

        prefix = "<:forklift:1068270227344867358> " if forklift_enabled else ""
        pick_text = f"**{prefix}⏰ Round {pick_record['round']}, Pick {pick_record['pick']} - AUTOPICK**\n"
        pick_text += f"**{team}** time expired\n"
        pick_text += f"Autopicked: **{autopicked_name}**\n"

        info_parts = []
        if player_data.get("position") and player_data["position"] != "?":
            info_parts.append(player_data["position"])
        if player_data.get("team") and player_data["team"] != "?":
            info_parts.append(f"[{player_data['team']}]")

        if info_parts:
            pick_text += " • ".join(info_parts) + "\n"

        pick_text += f"Source: {source}\n"
        pick_text += "─" * 35

        await channel.send(pick_text)

        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            if not self.TEST_MODE:
                await self.notify_manager_on_clock(current_pick["team"])
            if self.draft_board_thread:
                await self.update_draft_board()
            await self.start_pick_timer(channel)
            await self.post_on_clock_status(channel)
        else:
            if self.draft_board_thread:
                await self.update_draft_board()
            await channel.send("🏁 **DRAFT COMPLETE!**")
    
    async def post_on_clock_status(self, channel):
        """Post a non-pinned status card for the current pick.

        This uses the same embed builder as the old pinned scoreboard,
        but sends a fresh message so the status "follows" the draft
        down the channel instead of living at the top. We also retain
        a reference to the most recent status message so the timer
        loop can edit it with updated remaining time buckets.
        """
        embed = self.build_status_embed()
        try:
            msg = await channel.send(embed=embed)
            self.status_message = msg
        except Exception:
            # If send fails for any reason, don't crash the draft flow.
            self.status_message = None
    
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
            round_type = (current_pick.get('round_type') or 'standard')
            embed.add_field(
                name="Current Round",
                value=f"Round {current_pick['round']} ({str(round_type).title()})",
                inline=True
            )
        
        embed.add_field(
            name="Progress",
            value=f"{progress['picks_made']}/{progress['total_picks']} picks",
            inline=True
        )
        
        if self.timer_start_time and progress['status'] == 'active':
            elapsed = (datetime.now() - self.timer_start_time).total_seconds()
            duration = self.current_timer_duration or self.PICK_TIMER_DURATION
            remaining = max(0, duration - elapsed)

            # Show coarse buckets instead of exact mm:ss. For forklift (10s)
            # we show second-level granularity.
            if remaining <= 0:
                time_label = "Autodraft"
            elif duration <= 15:
                time_label = f"{int(remaining)} Seconds Left"
            else:
                if remaining <= 30:
                    time_label = "30 Seconds Left"
                elif remaining <= 60:
                    time_label = "1 Minute Left"
                elif remaining <= 120:
                    time_label = "2 Minutes Left"
                elif remaining <= 180:
                    time_label = "3 Minutes Left"
                else:
                    time_label = "4 Minutes Left"

            embed.add_field(name="⏱️ Time", value=time_label, inline=True)
        
        if current_pick:
            team_display = f"**{current_pick['team']}**"
            extra = ""
            if self._is_forklift_team(current_pick["team"]):
                team_display += " <:forklift:1068270227344867358>"
                extra = "\nForklift Mode: 10s • auto-pick from board"

            embed.add_field(
                name="⏰ ON THE CLOCK",
                value=f"# {team_display}\nPick {current_pick['pick']}{extra}",
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
        
        # Footer shows both the update time and the exact scheduled
        # autodraft time for the current pick (if a timer is running).
        now = datetime.now()
        footer = f"Updated: {now.strftime('%I:%M:%S %p')}"
        if self.timer_start_time and progress['status'] == 'active':
            duration = self.current_timer_duration or self.PICK_TIMER_DURATION
            deadline = self.timer_start_time + timedelta(seconds=duration)
            footer += f" | Autodraft at {deadline.strftime('%I:%M:%S %p')}"
        embed.set_footer(text=footer)
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
        
        rt = (current_round_picks[0].get('round_type') or 'standard')
        content_parts.append(f"**ROUND {current_round}** ({str(rt).upper()}) {status}")
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

        # Forklift teams are intentionally "hands off"; they likely don't want
        # DM spam during rapid-fire auto-picking.
        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type if self.draft_manager else "prospect")
        if self._is_forklift_team(team):
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
            
            self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type if self.draft_manager else "prospect")
            duration = self._get_pick_timer_duration(team)
            time_label = f"{duration} seconds" if duration < 60 else f"{int(duration // 60)} minutes"
            if self._is_forklift_team(team):
                time_label += " (Forklift Mode)"

            embed.add_field(name="Time Limit", value=time_label, inline=True)
            embed.add_field(
                name="Round Type",
                value=str((current_pick.get('round_type') or 'standard')).title(),
                inline=True
            )
            
            # Add board suggestions if available
            if self.board_manager:
                resolved = self.board_manager.resolve_board(team)
                drafted = set(p['player'].lower() for p in self.draft_manager.state["picks_made"])
                available = [e['name'] for e in resolved if e['name'].lower() not in drafted]
                
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
    @app_commands.describe(action="What to do", draft_type="Type of draft", team="Team abbreviation (forklift actions)")
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="pause", value="pause"),
        app_commands.Choice(name="continue", value="continue"),
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="undo", value="undo"),
        app_commands.Choice(name="order", value="order"),
        app_commands.Choice(name="reset", value="reset"),
        app_commands.Choice(name="forklift_enable", value="forklift_enable"),
        app_commands.Choice(name="forklift_disable", value="forklift_disable"),
        app_commands.Choice(name="forklift_status", value="forklift_status"),
        app_commands.Choice(name="forklift_list", value="forklift_list"),
    ])
    @app_commands.choices(draft_type=[
        app_commands.Choice(name="Prospect Draft", value="prospect"),
        app_commands.Choice(name="Keeper Draft", value="keeper"),
    ])
    async def draft_cmd(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        draft_type: app_commands.Choice[str] = None,
        team: str | None = None,
    ):
        action_value = action.value
        admin_actions = [
            "start",
            "pause",
            "continue",
            "undo",
            "reset",
            "forklift_enable",
            "forklift_disable",
            "forklift_status",
            "forklift_list",
        ]
        
        if action_value in admin_actions and not self._is_admin(interaction):
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        try:
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
            elif action_value == "reset":
                await self._handle_reset(interaction, draft_type)
            elif action_value == "forklift_enable":
                await self._handle_forklift_enable(interaction, team)
            elif action_value == "forklift_disable":
                await self._handle_forklift_disable(interaction, team)
            elif action_value == "forklift_status":
                await self._handle_forklift_status(interaction, team)
            elif action_value == "forklift_list":
                await self._handle_forklift_list(interaction)
        except Exception as e:
            # Ensure we never silently time out the interaction. Log the
            # full traceback to stdout (Render logs) and send a concise
            # error back to the caller.
            import traceback
            traceback.print_exc()
            error_msg = f"Unexpected error handling /draft {action_value}: {e}"
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ {error_msg}", ephemeral=True)
    
    async def _handle_start(self, interaction, draft_type):
        if draft_type is None:
            await interaction.response.send_message("❌ Specify draft type", ephemeral=True)
            return

        if self.draft_manager and self.draft_manager.state["status"] == "active":
            await interaction.response.send_message("❌ Draft already active", ephemeral=True)
            return

        # Interactions can expire if the bot is under load and does not
        # acknowledge within Discord's window. In that case, fall back to
        # sending messages directly in the channel.
        use_followup = True
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.errors.NotFound:
            use_followup = False
        except Exception:
            # Any other unexpected defer failure: try to continue and
            # communicate via channel.
            use_followup = False

        async def _send(msg: str | None = None, *, embed: discord.Embed | None = None, ephemeral: bool = False):
            if use_followup:
                try:
                    await interaction.followup.send(content=msg, embed=embed, ephemeral=ephemeral)
                    return
                except Exception:
                    pass
            # Channel fallback (ephemeral not supported here)
            try:
                await interaction.channel.send(content=msg, embed=embed)
            except Exception:
                pass

        try:
            # Use 2026 season for both keeper and prospect drafts
            self.draft_manager = DraftManager(draft_type=draft_type.value, season=2026, test_mode=self.TEST_MODE)
            
            # Initialize BoardManager for autopick
            from draft.board_manager import BoardManager
            self.board_manager = BoardManager(season=2026)
            
            # Initialize ProspectDatabase for validation
            from draft.prospect_database import ProspectDatabase
            self.prospect_db = ProspectDatabase(season=2026, draft_type=draft_type.value)
            
            # Initialize PickValidator
            self.pick_validator = PickValidator(self.prospect_db, self.draft_manager)

            # Initialize ForkliftManager (persists forklift teams across restarts)
            self.forklift_manager = ForkliftManager(season=2026, draft_type=draft_type.value)
            
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
            embed.add_field(name="Timer", value="4 minutes per pick", inline=True)
            
            # Show database info
            if self.prospect_db:
                embed.add_field(
                    name="Database",
                    value=f"{len(self.prospect_db.players)} players loaded",
                    inline=True
                )
            
            await _send(embed=embed)
            
            await self.create_draft_board_thread(interaction.channel)
            await self.start_pick_timer(interaction.channel)
            await self.post_on_clock_status(interaction.channel)
            
        except FileNotFoundError as e:
            await _send(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            await _send(f"❌ Error: {str(e)}", ephemeral=True)
    
    async def _handle_pause(self, interaction):
        if not self.draft_manager or self.draft_manager.state["status"] != "active":
            await interaction.response.send_message("❌ No active draft", ephemeral=True)
            return
        
        self.draft_manager.pause_draft()
        
        if self.pick_timer_task:
            self.pick_timer_task.cancel()
            self.pick_timer_task = None
        
        # Clear timer so website shows paused state
        self.draft_manager.state["timer_started_at"] = None
        self.draft_manager.state["timer_duration_seconds"] = None
        self.current_timer_duration = self.PICK_TIMER_DURATION
        self.draft_manager.save_state()

        try:
            self.draft_manager._commit_draft_files_async(
                [self.draft_manager.state_file],
                f"Draft paused (clock cleared): {self.draft_manager.draft_type} {getattr(self.draft_manager, 'season', '')}",
            )
        except Exception:
            pass
        
        embed = discord.Embed(title="⏸️ Draft Paused", color=discord.Color.orange())
        
        current_pick = self.draft_manager.get_current_pick()
        if current_pick:
            embed.add_field(name="Next Pick", value=f"{current_pick['team']} - Pick {current_pick['pick']}", inline=False)
        
        await interaction.response.send_message(embed=embed)
    
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
        await self.post_on_clock_status(interaction.channel)
    
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
            await self.post_on_clock_status(interaction.channel)
        
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

    async def _handle_reset(self, interaction: discord.Interaction, draft_type_choice: app_commands.Choice[str] | None):
        """Admin-only: reset the active draft back to pick 1.

        This always clears:
        - draft_state (picks + clock)

        Live mode additionally clears:
        - draft_order results
        - combined_players ownership changes from draft picks
        - player_log draft_pick entries for the season
        """
        # Determine which draft type we're resetting.
        draft_type = None
        if draft_type_choice is not None:
            draft_type = draft_type_choice.value
        elif self.draft_manager is not None:
            draft_type = self.draft_manager.draft_type

        if not draft_type:
            await interaction.response.send_message("❌ Specify draft type", ephemeral=True)
            return

        # Cancel any timers/tasks and clear runtime references.
        if self.pick_timer_task:
            self.pick_timer_task.cancel()
            self.pick_timer_task = None

        self.timer_start_time = None
        self.warning_sent = False
        self.pending_confirmations = {}
        self.status_message = None
        self.draft_board_thread = None
        self.draft_board_messages = {}

        await interaction.response.defer(ephemeral=True)
        try:
            mgr = DraftManager(draft_type=draft_type, season=2026, test_mode=self.TEST_MODE)
            mutated = mgr.reset_to_pick_one()

            # Commit reset artifacts so GitHub stays the persistence layer.
            try:
                # De-dupe
                unique = []
                for p in mutated:
                    if p and p not in unique:
                        unique.append(p)
                mgr._commit_draft_files(unique, f"Draft reset: {draft_type} 2026")
            except Exception as exc:
                print(f"⚠️ Draft reset git commit/push failed: {exc}")

            # Reattach cog state to the reset manager.
            self.draft_manager = mgr

            # Rebuild validator/db for immediate pick validation.
            from draft.prospect_database import ProspectDatabase
            from draft.board_manager import BoardManager

            self.board_manager = BoardManager(season=2026)
            self.prospect_db = ProspectDatabase(season=2026, draft_type=draft_type)
            self.pick_validator = PickValidator(self.prospect_db, self.draft_manager)

            await interaction.followup.send(
                f"✅ Draft reset to Pick 1. Cleared {len(mutated)} data artifact(s).",
                ephemeral=True,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            await interaction.followup.send(f"❌ Draft reset failed: {e}", ephemeral=True)

    async def _handle_forklift_enable(self, interaction: discord.Interaction, team: str | None):
        if not team:
            await interaction.response.send_message("❌ Provide a team (e.g. `team=SAD`)", ephemeral=True)
            return

        team = team.upper().strip()
        from commands.utils import MANAGER_DISCORD_IDS
        if team not in MANAGER_DISCORD_IDS:
            await interaction.response.send_message(f"❌ Invalid team: {team}", ephemeral=True)
            return

        # Ensure we have draft components needed for board size + pick flow.
        if self.board_manager is None:
            from draft.board_manager import BoardManager
            self.board_manager = BoardManager(season=2026)

        if self.draft_manager is None:
            # Create a lightweight manager just so we can git-commit/persist.
            self.draft_manager = DraftManager(draft_type="prospect", season=2026, test_mode=self.TEST_MODE)

        if self.draft_manager.draft_type != "prospect":
            await interaction.response.send_message("❌ Forklift mode is only supported for the prospect draft.", ephemeral=True)
            return

        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type)

        ok, msg = self.forklift_manager.enable_forklift(team, enabled_by=interaction.user.name)  # type: ignore[union-attr]
        if not ok:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
            return

        # Persist forklift mode state to GitHub.
        try:
            self.draft_manager._commit_draft_files_async(
                [self.forklift_manager.state_file],
                f"Forklift enabled: {team} ({self.draft_manager.draft_type} {self.draft_manager.season})",
            )
        except Exception:
            pass

        board = self.board_manager.get_board(team) if self.board_manager else []
        board_warning = ""
        if not board:
            board_warning = "\n\n⚠️ WARNING: This team’s board is empty. Forklift will auto-disable if it can’t find a valid pick."

        manager_id = MANAGER_DISCORD_IDS.get(team)
        manager_mention = f"<@{manager_id}>" if manager_id else team

        await interaction.response.send_message(
            f"<:forklift:1068270227344867358> **FORKLIFT MODE ENABLED**\n\n"
            f"**Team:** {team} ({manager_mention})\n"
            f"**Enabled by:** {interaction.user.mention}\n\n"
            f"**Settings:**\n"
            f"└─ ⏱️ Timer: **10 seconds**\n"
            f"└─ 🤖 Auto-pick: **Board-only**\n"
            f"└─ 📋 Board size: **{len(board)} players**"
            f"{board_warning}",
            ephemeral=False,
        )

        # If this team is currently on the clock, restart the timer with the
        # forklift duration immediately.
        try:
            current_pick = self.draft_manager.get_current_pick() if self.draft_manager else None
            if current_pick and current_pick.get("team") == team and self.draft_manager.state.get("status") == "active":
                await self.start_pick_timer(interaction.channel)
                await self.post_on_clock_status(interaction.channel)
        except Exception:
            pass

    async def _handle_forklift_disable(self, interaction: discord.Interaction, team: str | None):
        if not team:
            await interaction.response.send_message("❌ Provide a team (e.g. `team=SAD`)", ephemeral=True)
            return

        team = team.upper().strip()

        if self.draft_manager is None:
            self.draft_manager = DraftManager(draft_type="prospect", season=2026, test_mode=self.TEST_MODE)

        if self.draft_manager.draft_type != "prospect":
            await interaction.response.send_message("❌ Forklift mode is only supported for the prospect draft.", ephemeral=True)
            return

        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type)

        ok, msg = self.forklift_manager.disable_forklift(team, disabled_by=interaction.user.name)  # type: ignore[union-attr]
        if not ok:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
            return

        try:
            self.draft_manager._commit_draft_files_async(
                [self.forklift_manager.state_file],
                f"Forklift disabled: {team} ({self.draft_manager.draft_type} {self.draft_manager.season})",
            )
        except Exception:
            pass

        await interaction.response.send_message(f"✅ **Forklift Mode Disabled** for {team}", ephemeral=False)

        # If this team is currently on the clock, restart the timer with the
        # normal duration immediately.
        try:
            current_pick = self.draft_manager.get_current_pick() if self.draft_manager else None
            if current_pick and current_pick.get("team") == team and self.draft_manager.state.get("status") == "active":
                # Normal clock
                self.current_timer_duration = self.PICK_TIMER_DURATION
                await self.start_pick_timer(interaction.channel)
                await self.post_on_clock_status(interaction.channel)
        except Exception:
            pass

    async def _handle_forklift_status(self, interaction: discord.Interaction, team: str | None):
        if not team:
            await interaction.response.send_message("❌ Provide a team (e.g. `team=SAD`)", ephemeral=True)
            return

        team = team.upper().strip()

        if self.board_manager is None:
            from draft.board_manager import BoardManager
            self.board_manager = BoardManager(season=2026)

        if self.draft_manager is None:
            self.draft_manager = DraftManager(draft_type="prospect", season=2026, test_mode=self.TEST_MODE)

        if self.draft_manager.draft_type != "prospect":
            await interaction.response.send_message("❌ Forklift mode is only supported for the prospect draft.", ephemeral=True)
            return

        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type)

        enabled = self._is_forklift_team(team)
        duration = self._get_pick_timer_duration(team)
        board = self.board_manager.get_board(team) if self.board_manager else []

        if enabled:
            msg = (
                f"<:forklift:1068270227344867358> **FORKLIFT MODE: ENABLED**\n\n"
                f"Team: **{team}**\n"
                f"Clock: **{duration}s**\n"
                f"Board size: **{len(board)}**\n\n"
                f"*Auto-picks from the team’s board when the 10s clock expires.*"
            )
        else:
            msg = (
                f"✅ **Forklift Mode: Disabled**\n\n"
                f"Team: **{team}**\n"
                f"Clock: **{duration}s (normal)**\n"
                f"Board size: **{len(board)}**"
            )

        await interaction.response.send_message(msg, ephemeral=True)

    async def _handle_forklift_list(self, interaction: discord.Interaction):
        if self.board_manager is None:
            from draft.board_manager import BoardManager
            self.board_manager = BoardManager(season=2026)

        if self.draft_manager is None:
            self.draft_manager = DraftManager(draft_type="prospect", season=2026, test_mode=self.TEST_MODE)

        if self.draft_manager.draft_type != "prospect":
            await interaction.response.send_message("❌ Forklift mode is only supported for the prospect draft.", ephemeral=True)
            return

        self._ensure_forklift_manager(season=2026, draft_type=self.draft_manager.draft_type)

        teams = self.forklift_manager.get_forklift_teams() if self.forklift_manager else []
        if not teams:
            await interaction.response.send_message("📋 No teams are currently in Forklift Mode", ephemeral=True)
            return

        lines = ["<:forklift:1068270227344867358> **FORKLIFT MODE TEAMS**", ""]
        for t in teams:
            board = self.board_manager.get_board(t) if self.board_manager else []
            lines.append(f"• **{t}** — {len(board)} players on board")

        recent = self.forklift_manager.get_recent_changes(limit=5) if self.forklift_manager else []
        if recent:
            lines.append("\nRecent changes:")
            for ch in recent:
                who = ch.get("by") or "?"
                lines.append(f"- {ch.get('action')} {ch.get('team', '')} by {who}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot):
    await bot.add_cog(DraftCommands(bot))
