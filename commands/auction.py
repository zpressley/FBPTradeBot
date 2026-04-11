import json

import discord
from discord.ext import commands, tasks
from discord import app_commands

from auction_manager import AuctionManager, AuctionPhase, ET
from commands.utils import DISCORD_ID_TO_TEAM, MANAGER_DISCORD_IDS

AUCTION_CHANNEL_ID = 1351376690319851520  # dedicated auction channel
AUCTION_BOARD_URL = "https://zpressley.github.io/fbp-hub/auction.html"
PLAYER_PROFILE_BASE_URL = "https://zpressley.github.io/fbp-hub/player-profile.html"
_AUCTION_RESOLVED_STATE_FILE = "data/auction_resolved_state.json"

# Module-level commit function, set by health.py on_ready
_auction_commit_fn = None


def set_auction_commit_fn(fn):
    global _auction_commit_fn
    _auction_commit_fn = fn


def _resolve_prospect_name(prospect_id: str) -> str:
    """Resolve a UPID to a player name for readable Discord messages."""
    import json
    try:
        with open("data/combined_players.json", "r", encoding="utf-8") as f:
            players = json.load(f)
        for p in players:
            if str(p.get("upid", "")) == str(prospect_id):
                return p.get("name", prospect_id)
    except Exception:
        pass
    return str(prospect_id)


def _player_profile_link(prospect_id: str, name: str | None = None) -> str:
    """Return a Discord markdown link to the FBP Hub player profile."""
    from urllib.parse import quote
    display = name or _resolve_prospect_name(prospect_id)
    url = f"{PLAYER_PROFILE_BASE_URL}?upid={quote(str(prospect_id))}"
    return f"[{display}]({url})"


class Auction(commands.Cog):
    """Discord interface for the Prospect Auction Portal.

    This cog provides:
    - /auction: summary of current phase, user WB, and active bids
    - /bid: place OB/CB bids that delegate to AuctionManager

    Match/Forfeit flows will be layered on via follow-up interactions
    and/or DMs using AuctionManager.record_match.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = AuctionManager()

        # State for scheduled alerts
        self._ob_open_week = None
        self._ob_close_week = None
        self._cb_open_week = None
        self._cb_close_week = None
        self._weekly_summary_week = None  # Saturday weekly summary guard
        self._resolved_week = None  # Sunday resolve guard
        self._last_summary_date = None  # YYYY-MM-DD in ET
        self._last_synced_phase = None  # track phase for website sync

        # Start background task that checks phase/time once per minute
        self.auction_tick.start()

    # ------------------------------------------------------------------
    # /auction status
    # ------------------------------------------------------------------

    @app_commands.command(name="auction", description="View current prospect auction portal status")
    async def auction_status(self, interaction: discord.Interaction) -> None:
        """Show current phase and a quick summary for the calling manager."""

        await interaction.response.defer(ephemeral=True)

        team = DISCORD_ID_TO_TEAM.get(interaction.user.id)
        if not team:
            await interaction.followup.send(
                "❌ You are not mapped to an FBP team. Contact an admin to be linked.",
                ephemeral=True,
            )
            return

        phase = self.manager.get_current_phase()

        # Basic phase copy; website will provide the full portal UI.
        phase_text = {
            AuctionPhase.OFF_WEEK: "No auction this week.",
            AuctionPhase.OB_WINDOW: "Originating bids are open (Mon 3pm\u2013Tue midnight ET).",
            AuctionPhase.CB_WINDOW: "Challenge bids are open (Wed midnight\u2013Fri 9:00pm ET).",
            AuctionPhase.OB_FINAL: "OB managers may match or forfeit (Saturday).",
            AuctionPhase.PROCESSING: "Auction is processing (Sunday).",
        }[phase]

        embed = discord.Embed(
            title="🏆 Weekly Prospect Auction Portal",
            description=phase_text,
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Your team",
            value=f"`{team}`",
            inline=True,
        )
        embed.add_field(
            name="Web Portal",
            value="[Open Auction Portal](https://zpressley.github.io/fbp-hub/auction.html)",
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /bid
    # ------------------------------------------------------------------

    @app_commands.command(name="bid", description="Place an Originating Bid (OB) or Challenge Bid (CB)")
    @app_commands.describe(
        prospect_id="Prospect UPID or exact name",
        amount="Bid amount in WB",
        bid_type="OB for originating bid, CB for challenge bid",
    )
    @app_commands.choices(
        bid_type=[
            app_commands.Choice(name="Originating Bid (OB)", value="OB"),
            app_commands.Choice(name="Challenge Bid (CB)", value="CB"),
        ]
    )
    async def bid(
        self,
        interaction: discord.Interaction,
        prospect_id: str,
        amount: int,
        bid_type: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        team = DISCORD_ID_TO_TEAM.get(interaction.user.id)
        if not team:
            await interaction.followup.send(
                "❌ You are not mapped to an FBP team. Contact an admin to be linked.",
                ephemeral=True,
            )
            return

        phase = self.manager.get_current_phase()
        if phase in {AuctionPhase.OFF_WEEK, AuctionPhase.PROCESSING}:
            await interaction.followup.send(
                "❌ Auction is not accepting bids right now.", ephemeral=True
            )
            return

        result = self.manager.place_bid(
            team=team,
            prospect_id=prospect_id,
            amount=amount,
            bid_type=bid_type.value,
        )

        if not result.get("success"):
            await interaction.followup.send(
                f"❌ Bid failed: {result.get('error', 'Unknown error')}",
                ephemeral=True,
            )
            return

        bid_data = result["bid"]
        prospect_name = _resolve_prospect_name(bid_data['prospect_id'])
        await interaction.followup.send(
            (
                f"✅ Bid placed!\n"
                f"Team: `{bid_data['team']}`\n"
                f"Prospect: {prospect_name}\n"
                f"Amount: ${bid_data['amount']} WB\n"
                f"Type: {bid_data['bid_type']}\n"
            ),
            ephemeral=True,
        )

        # Commit to GitHub so the website sees it immediately
        if _auction_commit_fn:
            _auction_commit_fn(
                ["data/auction_current.json"],
                f"Auction bid: {bid_data['bid_type']} ${bid_data['amount']} on {prospect_name} by {bid_data['team']} (Discord)",
            )

        # Log to auction channel
        channel = self.bot.get_channel(AUCTION_CHANNEL_ID)
        if channel:
            is_ob = bid_data["bid_type"] == "OB"
            header = "📣 Originating Bid Posted" if is_ob else "⚔️ Challenging Bid Placed"
            player_link = _player_profile_link(bid_data["prospect_id"], prospect_name)
            content = (
                f"{header}\n\n"
                f"🏷️ Team: {bid_data['team']}\n"
                f"💰 Bid: ${bid_data['amount']}\n"
                f"🧢 Player: {player_link}\n\n"
                f"Source: Discord /bid"
            )
            try:
                await channel.send(content)
            except Exception as exc:  # pragma: no cover - logging only
                print(f"⚠️ Failed to send auction log message: {exc}")

    # ------------------------------------------------------------------
    # Background task: scheduled alerts & daily summary
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def auction_tick(self) -> None:
        """Check auction phase/time and send scheduled alerts.

        Runs every minute in ET. Uses in-memory guards so each alert is
        only sent once per relevant day/week.
        """

        from datetime import datetime

        now = datetime.now(tz=ET)
        phase = self.manager.get_current_phase(now)

        # Sync phase to auction_current.json + GitHub so the website stays current
        phase_val = phase.value
        if phase_val != self._last_synced_phase:
            try:
                state = self.manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
                if state.get("phase") != phase_val:
                    state["phase"] = phase_val
                    self.manager._save_auction_state(state)  # type: ignore[attr-defined]
                    if _auction_commit_fn:
                        _auction_commit_fn(
                            ["data/auction_current.json"],
                            f"Auction phase sync: {phase_val}",
                        )
            except Exception as exc:
                print(f"⚠️ Failed to sync auction phase: {exc}")
            self._last_synced_phase = phase_val

        # If auctions are not active this week, do nothing
        if phase is AuctionPhase.OFF_WEEK:
            return

        # Determine week key (Monday of this week) and date key
        week_start = self.manager._monday_for_date(now.date())  # type: ignore[attr-defined]
        week_key = week_start.isoformat()
        date_key = now.date().isoformat()

        # Hydrate _resolved_week from disk on first tick after restart so
        # Railway redeploys don't reset the Sunday resolve guard.
        if self._resolved_week is None:
            try:
                with open(_AUCTION_RESOLVED_STATE_FILE, "r") as f:
                    self._resolved_week = json.load(f).get("resolved_week")
            except Exception:
                pass

        # OB window open: Monday 3:00pm ET
        if now.weekday() == 0 and now.hour == 15 and now.minute == 0:
            if self._ob_open_week != week_key:
                await self._send_ob_open_alert()
                self._ob_open_week = week_key

        # OB window closed: Wednesday 8:00am ET (window closed at midnight Tue/Wed)
        if now.weekday() == 2 and now.hour == 8 and now.minute == 0:
            if self._ob_close_week != week_key:
                await self._send_ob_closed_alert()
                self._ob_close_week = week_key
        # CB window open message: Wednesday 8:00am ET (window opened at midnight Tue/Wed)
        if now.weekday() == 2 and now.hour == 8 and now.minute == 0:
            if self._cb_open_week != week_key:
                await self._send_cb_open_alert()
                self._cb_open_week = week_key

        # CB window closed: Friday 9:00pm ET
        if now.weekday() == 4 and now.hour == 21 and now.minute == 0:
            if self._cb_close_week != week_key:
                await self._send_cb_closed_alert()
                self._cb_close_week = week_key

        # Saturday 9:00am ET: weekly summary (results + match/forfeit questions)
        if now.weekday() == 5 and now.hour == 9 and now.minute == 0:
            if self._weekly_summary_week != week_key:
                await self._send_weekly_summary()
                self._weekly_summary_week = week_key

        # Sunday 2:00pm+ ET: resolve the week once (assign winners, charge WB).
        # Catch-up window avoids missing resolution if the bot restarts at 2:01+.
        if now.weekday() == 6 and now.hour >= 14 and self._resolved_week != week_key:
            try:
                result = self.manager.resolve_week(now=now)
                status = result.get("status", "")
                winners = result.get("winners", {})
                if status == "resolved" and winners:
                    channel = await self._get_auction_channel()
                    if channel:
                        lines = ["🏁 **Auction Resolved — Transactions Applied**", ""]
                        for pid, info in winners.items():
                            name = info.get("name", _resolve_prospect_name(pid))
                            lines.append(f"✅ **{info['team']}** → {_player_profile_link(pid, name)} (${info['amount']} WB)")
                        await channel.send("\n".join(lines))
                    # Commit updated files
                    if _auction_commit_fn:
                        _auction_commit_fn(
                            ["data/combined_players.json", "data/wizbucks.json",
                             "data/wizbucks_transactions.json", "data/auction_current.json",
                             "data/player_log.json"],
                            f"Auction resolved: week of {week_key}",
                        )
                    print(f"✅ Auction resolved: {len(winners)} winners")
                    self._resolved_week = week_key
                    try:
                        with open(_AUCTION_RESOLVED_STATE_FILE, "w") as f:
                            json.dump({"resolved_week": week_key}, f)
                    except Exception:
                        pass
                elif status == "no_bids":
                    print("Auction resolve: no bids this week")
                    self._resolved_week = week_key
                elif status == "inactive":
                    self._resolved_week = week_key
                elif status == "already_resolved":
                    print(f"ℹ️ Auction already resolved for week {week_key} — skipping")
                    self._resolved_week = week_key
                    try:
                        with open(_AUCTION_RESOLVED_STATE_FILE, "w") as f:
                            json.dump({"resolved_week": week_key}, f)
                    except Exception:
                        pass
            except Exception as exc:
                print(f"⚠️ Auction resolve failed: {exc}")
        # Daily summary: 9:00–9:59am ET, Tue–Fri only (Saturday has weekly summary).
        # Hour-long window prevents missed updates after brief restarts.
        if now.weekday() in (1, 2, 3, 4) and now.hour == 9:
            if self._last_summary_date != date_key:
                await self._send_daily_summary()
                self._last_summary_date = date_key

    # ------------------------------------------------------------------
    # Alert helpers
    # ------------------------------------------------------------------

    async def _get_auction_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(AUCTION_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(AUCTION_CHANNEL_ID)
            except Exception as exc:
                print(f"⚠️ Failed to resolve auction channel {AUCTION_CHANNEL_ID}: {exc}")
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        print(f"⚠️ Auction channel {AUCTION_CHANNEL_ID} is not a text channel")
        return None

    async def _send_ob_open_alert(self) -> None:
        channel = await self._get_auction_channel()
        if not channel:
            return
        msg = (
            "@everyone 🟢 **Auction Window is Now Open!**\n"
            "You can now place your Originating Bids (OB) in the Weekly Auction Portal."
        )
        await channel.send(msg)

    async def _send_ob_closed_alert(self) -> None:
        channel = await self._get_auction_channel()
        if not channel:
            return
        msg = (
            "@everyone 🛑 **Originating Bid Window is Now Closed!**\n"
            "OBs closed at midnight ET. Challenge Bid window is now open."
        )
        await channel.send(msg)

    async def _send_cb_open_alert(self) -> None:
        channel = await self._get_auction_channel()
        if not channel:
            return
        msg = (
            "@everyone 🟡 **Challenge Bid Window is Now Open!**\n"
            "CBs opened at midnight ET. Challenge Bid window runs Wed\u2013Fri 9:00 PM ET."
        )
        await channel.send(msg)

    async def _send_cb_closed_alert(self) -> None:
        channel = await self._get_auction_channel()
        if not channel:
            return
        msg = (
            "@everyone ⛔ **Challenge Bid Window is Now Closed!**\n"
            "Final bids are locked until resolution."
        )
        await channel.send(msg)

    async def _send_daily_summary(self) -> None:
        """Post a daily auction summary similar to legacy Apps Script."""

        channel = await self._get_auction_channel()
        if not channel:
            return

        # Load current auction state
        from datetime import datetime

        now = datetime.now(tz=ET)
        state = self.manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
        bids = state.get("bids", [])

        if not bids:
            await channel.send(
                "📊 Daily Auction Summary\n\nNo active bids yet this week.\n\n"
                f"🔗 Auction Board: {AUCTION_BOARD_URL}"
            )
            return

        # Group bids by prospect and identify OB + high CB
        by_prospect: dict[str, list[dict]] = {}
        for b in bids:
            pid = str(b["prospect_id"])
            by_prospect.setdefault(pid, []).append(b)

        lines: list[str] = ["📊 Daily Auction Summary", ""]

        for prospect_id, pbids in by_prospect.items():
            ob = next((b for b in pbids if b["bid_type"] == "OB"), None)
            if not ob:
                continue

            cbs = [b for b in pbids if b["bid_type"] == "CB"]
            player_name = _resolve_prospect_name(prospect_id)
            lines.append(f"🧢 Player: {player_name}")
            lines.append(f"📌 Originating Team: {ob['team']}")

            if cbs:
                max_amt = max(int(cb["amount"]) for cb in cbs)
                top_cbs = [cb for cb in cbs if int(cb["amount"]) == max_amt]
                top_cb = top_cbs[0]
                lines.append(f"⚔️ High Challenge: ${max_amt} by {top_cb['team']}")
            else:
                lines.append("🚫 No Challenges Yet")

            lines.append("──────────────────────")
        lines.append("")
        lines.append(f"🔗 Auction Board: {AUCTION_BOARD_URL}")

        msg = "\n".join(lines).rstrip()
        await channel.send(msg)


    async def _send_weekly_summary(self) -> None:
        """Saturday 9am — weekly auction results + match/forfeit questions.

        Mirrors the Apps Script sendWeeklySummary:
        - Uncontested OBs → "✅ TEAM wins PLAYER — no challengers."
        - Contested prospects → "🔔 TEAM, do you want to match $X by CHALLENGER for PLAYER?"
        """

        channel = await self._get_auction_channel()
        if not channel:
            return

        from datetime import datetime

        now = datetime.now(tz=ET)
        state = self.manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
        bids = state.get("bids", [])

        if not bids:
            await channel.send("🏁 **Weekly Auction Results**\n\nNo bids this week.")
            return

        # Group bids by prospect
        by_prospect: dict[str, list[dict]] = {}
        for b in bids:
            pid = str(b["prospect_id"])
            by_prospect.setdefault(pid, []).append(b)

        winners_lines: list[str] = ["🏁 **Weekly Auction Results**", ""]
        match_lines: list[str] = ["❓ **Do You Want to Match?**", ""]
        has_contested = False
        contested_prompts: list[tuple[str, str, int, str]] = []

        for prospect_id, pbids in by_prospect.items():
            ob = next((b for b in pbids if b["bid_type"] == "OB"), None)
            if not ob:
                continue

            cbs = [b for b in pbids if b["bid_type"] == "CB"]
            player_name = _resolve_prospect_name(prospect_id)
            ob_team = ob["team"]

            linked = _player_profile_link(prospect_id, player_name)
            if not cbs:
                winners_lines.append(f"✅ **{ob_team} wins** {linked} — no challengers.")
            else:
                max_amt = max(int(cb["amount"]) for cb in cbs)
                top_cb = next(cb for cb in cbs if int(cb["amount"]) == max_amt)
                match_lines.append(
                    f"🔔 **{ob_team}**, do you want to match the high bid of "
                    f"**${max_amt}** by **{top_cb['team']}** for {linked}?"
                )
                contested_prompts.append((ob_team, prospect_id, max_amt, top_cb["team"]))
                has_contested = True

        await channel.send("\n".join(winners_lines))
        if has_contested:
            await channel.send("\n".join(match_lines))
            for ob_team, prospect_id, max_amt, challenger_team in contested_prompts:
                await self._send_match_prompt_dm(ob_team, prospect_id, max_amt, challenger_team)

    async def _send_match_prompt_dm(
        self,
        ob_team: str,
        prospect_id: str,
        amount: int,
        challenger_team: str,
    ) -> None:
        """DM OB managers on Saturday for contested prospects."""
        manager_id = MANAGER_DISCORD_IDS.get(ob_team)
        if not manager_id:
            return

        user = self.bot.get_user(manager_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(manager_id)
            except Exception as exc:
                print(f"⚠️ Failed to fetch OB manager for DM ({ob_team}): {exc}")
                return

        player_name = _resolve_prospect_name(prospect_id)
        player_link = _player_profile_link(prospect_id, player_name)
        message = (
            "🔔 **Auction Match Decision Needed**\n\n"
            f"Your OB is contested for {player_link}.\n"
            f"High challenge bid: **${amount}** by **{challenger_team}**.\n\n"
            f"Please submit your decision in the Auction Portal: {AUCTION_BOARD_URL}\n"
            "(Saturday match/forfeit window)"
        )

        try:
            await user.send(message)
        except Exception as exc:
            print(f"⚠️ Failed to DM OB manager ({ob_team}) for match prompt: {exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Auction(bot))
