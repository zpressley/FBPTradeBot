import discord
from discord.ext import commands
from discord import app_commands

from auction_manager import AuctionManager, AuctionPhase
from commands.utils import DISCORD_ID_TO_TEAM

TEST_AUCTION_CHANNEL_ID = 1197200421639438537  # test channel for auction logs


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
        prospect_id="Prospect identifier (JSON id or exact name)",
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
        await interaction.followup.send(
            (
                f"✅ Bid placed!\\n"
                f"Team: `{bid_data['team']}`\\n"
                f"Prospect: `{bid_data['prospect_id']}`\\n"
                f"Amount: ${bid_data['amount']} WB\\n"
                f"Type: {bid_data['bid_type']}\\n"
            ),
            ephemeral=True,
        )

        # Log to test auction channel
        channel = self.bot.get_channel(TEST_AUCTION_CHANNEL_ID)
        if channel:
            is_ob = bid_data["bid_type"] == "OB"
            header = "📣 Originating Bid Posted" if is_ob else "⚔️ Challenging Bid Placed"
            content = (
                f"{header}\\n\\n"
                f"🏷️ Team: {bid_data['team']}\\n"
                f"💰 Bid: ${bid_data['amount']}\\n"
                f"🧢 Player: {bid_data['prospect_id']}\\n\\n"
                f"Source: Discord /bid"
            )
            try:
                await channel.send(content)
            except Exception as exc:  # pragma: no cover - logging only
                print(f"⚠️ Failed to send auction log message: {exc}")

    # ------------------------------------------------------------------
    # /auction_weekly_results - Saturday-style summary
    # ------------------------------------------------------------------

    @app_commands.command(
        name="auction_weekly_results",
        description="Post weekly auction winners and OB decisions to the test channel",
    )
    async def auction_weekly_results(self, interaction: discord.Interaction) -> None:
        """Summarize current auction state for Saturday morning review.

        This does *not* run resolution; it only inspects current bids
        and match decisions to show:
        - Winners (OBs with no challengers)
        - Entries waiting on OB managers to Match/Forfeit
        """

        await interaction.response.defer(ephemeral=True)

        state = self.manager._load_or_initialize_auction(self.manager._monday_for_date(self.manager._monday_for_date))  # type: ignore[attr-defined]
        bids = state.get("bids", [])
        matches = state.get("matches", [])

        # Index matches (team, prospect) -> decision
        match_map = {(m["team"], str(m["prospect_id"])): m for m in matches}

        # Group bids by prospect
        by_prospect = {}
        for b in bids:
            pid = str(b["prospect_id"])
            by_prospect.setdefault(pid, []).append(b)

        winners_lines = []
        waiting_lines = []

        for prospect_id, pbids in by_prospect.items():
            ob = next((b for b in pbids if b["type"] == "OB"), None)
            cbs = [b for b in pbids if b["type"] == "CB"]

            if not ob:
                continue

            ob_team = ob["team"]

            if not cbs:
                # Winner by default at $10
                winners_lines.append(f"• {ob_team} wins {prospect_id} for $10 (no challengers)")
                continue

            # At least one CB, check for match decision
            key = (ob_team, prospect_id)
            decision = match_map.get(key, {}).get("decision")

            # Determine current high CB
            max_amt = max(int(cb["amount"]) for cb in cbs)
            top_cbs = [cb for cb in cbs if int(cb["amount"]) == max_amt]
            top_cb = top_cbs[0]
            cb_team = top_cb["team"]

            if decision == "match":
                winners_lines.append(
                    f"• {ob_team} (OB) matched and wins {prospect_id} for ${max_amt} over {cb_team}"
                )
            elif decision == "forfeit":
                winners_lines.append(
                    f"• {cb_team} (CB) wins {prospect_id} for ${max_amt}; {ob_team} forfeited"
                )
            else:
                waiting_lines.append(
                    f"• {ob_team}: high bid ${max_amt} by {cb_team} for {prospect_id} – OB may Match or Forfeit"
                )

        channel = self.bot.get_channel(TEST_AUCTION_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Test auction channel not found.", ephemeral=True)
            return

        parts = ["🏁 Weekly Auction Results"]
        if winners_lines:
            parts.append("\n✅ Winners")
            parts.extend(winners_lines)
        if waiting_lines:
            parts.append("\n⏳ Waiting on OB Manager")
            parts.extend(waiting_lines)

        message = "\n".join(parts)
        await channel.send(message)
        await interaction.followup.send("Posted weekly auction results to test channel.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Auction(bot))
