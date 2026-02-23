import discord
from discord.ext import commands, tasks
from discord import app_commands

from auction_manager import AuctionManager, AuctionPhase, ET
from commands.utils import DISCORD_ID_TO_TEAM

TEST_AUCTION_CHANNEL_ID = 1197200421639438537  # test channel for auction logs


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
        self._last_summary_date = None  # YYYY-MM-DD in ET

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
            AuctionPhase.OB_WINDOW: "Originating bids are open (Mon 3pm–Tue night).",
            AuctionPhase.CB_WINDOW: "Challenge bids are open (Wed–Fri 9pm).",
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

        # Log to test auction channel
        channel = self.bot.get_channel(TEST_AUCTION_CHANNEL_ID)
        if channel:
            is_ob = bid_data["bid_type"] == "OB"
            header = "📣 Originating Bid Posted" if is_ob else "⚔️ Challenging Bid Placed"
            content = (
                f"{header}\n\n"
                f"🏷️ Team: {bid_data['team']}\n"
                f"💰 Bid: ${bid_data['amount']}\n"
                f"🧢 Player: {prospect_name}\n\n"
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

        # If auctions are not active this week, do nothing
        if phase is AuctionPhase.OFF_WEEK:
            return

        # Determine week key (Monday of this week) and date key
        week_start = self.manager._monday_for_date(now.date())  # type: ignore[attr-defined]
        week_key = week_start.isoformat()
        date_key = now.date().isoformat()

        # OB window open: Monday 3:00pm ET
        if now.weekday() == 0 and now.hour == 15 and now.minute == 0:
            if self._ob_open_week != week_key:
                await self._send_ob_open_alert()
                self._ob_open_week = week_key

        # OB window closed: Tuesday 11:00pm ET
        if now.weekday() == 1 and now.hour == 23 and now.minute == 0:
            if self._ob_close_week != week_key:
                await self._send_ob_closed_alert()
                self._ob_close_week = week_key

        # CB window open: Wednesday 12:00am ET
        if now.weekday() == 2 and now.hour == 0 and now.minute == 0:
            if self._cb_open_week != week_key:
                await self._send_cb_open_alert()
                self._cb_open_week = week_key

        # CB window closed: Friday 9:00pm ET
        if now.weekday() == 4 and now.hour == 21 and now.minute == 0:
            if self._cb_close_week != week_key:
                await self._send_cb_closed_alert()
                self._cb_close_week = week_key

        # Daily summary: 9:30am ET, once per calendar day
        if now.hour == 9 and now.minute == 30:
            if self._last_summary_date != date_key:
                await self._send_daily_summary()
                self._last_summary_date = date_key

    # ------------------------------------------------------------------
    # Alert helpers
    # ------------------------------------------------------------------

    async def _get_test_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(TEST_AUCTION_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def _send_ob_open_alert(self) -> None:
        channel = await self._get_test_channel()
        if not channel:
            return
        msg = (
            "@everyone 🟢 **Auction Window is Now Open!**\n"
            "You can now place your Originating Bids (OB) in the Weekly Auction Portal."
        )
        await channel.send(msg)

    async def _send_ob_closed_alert(self) -> None:
        channel = await self._get_test_channel()
        if not channel:
            return
        msg = (
            "@everyone 🛑 **Originating Bid Window is Now Closed!**\n"
            "You may no longer place OBs for this week."
        )
        await channel.send(msg)

    async def _send_cb_open_alert(self) -> None:
        channel = await self._get_test_channel()
        if not channel:
            return
        msg = (
            "@everyone 🟡 **Challenge Bid Window is Now Open!**\n"
            "You may now place Challenge Bids (CBs) against existing OBs."
        )
        await channel.send(msg)

    async def _send_cb_closed_alert(self) -> None:
        channel = await self._get_test_channel()
        if not channel:
            return
        msg = (
            "@everyone ⛔ **Challenge Bid Window is Now Closed!**\n"
            "Final bids are locked until resolution."
        )
        await channel.send(msg)

    async def _send_daily_summary(self) -> None:
        """Post a daily auction summary similar to legacy Apps Script."""

        channel = await self._get_test_channel()
        if not channel:
            return

        # Load current auction state
        from datetime import datetime

        now = datetime.now(tz=ET)
        state = self.manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
        bids = state.get("bids", [])

        if not bids:
            await channel.send("📊 Daily Auction Summary\n\nNo active bids yet this week.")
            return

        # Group bids by prospect and identify OB + high CB
        by_prospect: dict[str, list[dict]] = {}
        for b in bids:
            pid = str(b["prospect_id"])
            by_prospect.setdefault(pid, []).append(b)

        lines: list[str] = ["📊 Daily Auction Summary", ""]

        for prospect_id, pbids in by_prospect.items():
            ob = next((b for b in pbids if b["type"] == "OB"), None)
            if not ob:
                continue

            cbs = [b for b in pbids if b["type"] == "CB"]
            lines.append(f"🧢 Player: {_resolve_prospect_name(prospect_id)}")
            lines.append(f"📌 Originating Team: {ob['team']}")

            if cbs:
                max_amt = max(int(cb["amount"]) for cb in cbs)
                top_cbs = [cb for cb in cbs if int(cb["amount"]) == max_amt]
                top_cb = top_cbs[0]
                lines.append(f"⚔️ High Challenge: ${max_amt} by {top_cb['team']}")
            else:
                lines.append("🚫 No Challenges Yet")

            lines.append("──────────────────────")

        msg = "\n".join(lines).rstrip()
        await channel.send(msg)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Auction(bot))
