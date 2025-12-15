# commands/trade_logic.py - Simplified version with fixed rejection modal

import discord
from discord.ui import View, Button, Modal, TextInput
from commands.utils import MANAGER_DISCORD_IDS, get_trade_dates, mention_manager
import json

# Channel IDs
PENDING_CHANNEL_ID = 1356234086833848492  # For trade discussion threads
TRADE_CHANNEL_ID = 1197200421639438537    # For final approved trades

# ========== TRADE VALIDATION ==========

def validate_trade(trade_data):
    """
    Validate trade against FBP rules (post-TAC)
    Returns (is_valid, error_messages)
    """
    errors = []
    
    # TODO: Add validation rules as needed
    # - Wiz Bucks balance checking (already done in trade command)
    # - Farm contract trading rules
    # - Draft pick validation
    # - Keeper limits
    
    return len(errors) == 0, errors

# ========== CREATE TRADE THREAD ==========

async def create_trade_thread(guild, trade_data):
    """Create private thread for trade discussion"""
    channel = guild.get_channel(PENDING_CHANNEL_ID)
    if not channel:
        print("❌ Could not find #pending-trades channel.")
        return

    teams = trade_data["teams"]
    thread_name = f"Trade: {' ↔ '.join(teams)}"
    thread = await channel.create_thread(
        name=thread_name,
        type=discord.ChannelType.private_thread,
        invitable=True
    )

    # Add all involved managers to thread
    for team in teams:
        user_id = next((uid for label, uid in MANAGER_DISCORD_IDS.items() if label == team), None)
        if user_id:
            try:
                member = await guild.fetch_member(user_id)
                await thread.add_user(member)
            except Exception as e:
                print(f"⚠️ Could not add user {user_id}: {e}")

    # Validate trade first
    is_valid, errors = validate_trade(trade_data)
    
    if not is_valid:
        msg = "❌ **TRADE VALIDATION FAILED**\n\n"
        msg += "\n".join(f"• {error}" for error in errors)
        msg += f"\n\n{format_trade_review(trade_data)}"
        await thread.send(content=msg)
        return thread

    # If valid, show for approval
    msg = format_trade_review(trade_data) + "\n\n🔘 Please review and approve this trade below."
    view = TradeApprovalView(trade_data)
    await thread.send(content=msg, view=view)
    return thread

# ========== FORMATTING HELPERS ==========

def format_block(team, assets):
    return f"🔁 **{team} receives:**\n" + "\n".join(assets)

def format_trade_review(trade_data):
    blocks = [format_block(team, trade_data["players"].get(team, [])) for team in trade_data["teams"]]
    return "\n\n".join(blocks)

# ========== APPROVAL VIEW ==========

class TradeApprovalView(View):
    def __init__(self, trade_data):
        super().__init__(timeout=None)
        self.trade_data = trade_data
        self.approvals = set()
        self.rejected = False

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        
        # Check if trade has been rejected
        if self.rejected:
            await interaction.response.send_message("❌ This trade has been rejected and cannot be approved.", ephemeral=True)
            return
        
        # Check if user is involved in trade
        involved_ids = [MANAGER_DISCORD_IDS.get(t) for t in self.trade_data["teams"]]
        if user_id not in involved_ids:
            await interaction.response.send_message("You are not involved in this trade.", ephemeral=True)
            return
            
        if user_id in self.approvals:
            await interaction.response.send_message("You've already approved this trade.", ephemeral=True)
            return

        self.approvals.add(user_id)
        await interaction.response.send_message("✅ Your approval has been recorded.", ephemeral=True)

        # Check if all parties have approved
        if all(uid in self.approvals for uid in involved_ids if uid is not None):
            await interaction.channel.send("✅ All managers have approved! Posting trade...")
            await post_approved_trade(interaction.guild, self.trade_data)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        
        # Check if user is involved in trade
        involved_ids = [MANAGER_DISCORD_IDS.get(t) for t in self.trade_data["teams"]]
        if user_id not in involved_ids:
            await interaction.response.send_message("You are not involved in this trade.", ephemeral=True)
            return
            
        if self.rejected:
            await interaction.response.send_message("❌ Trade already rejected.", ephemeral=True)
            return

        self.rejected = True
        
        # Disable all buttons after rejection
        for item in self.children:
            item.disabled = True
        
        modal = RejectionReasonModal(interaction.user, self.trade_data, interaction.channel, self)
        await interaction.response.send_modal(modal)

# ========== POST APPROVED TRADE ==========

async def post_approved_trade(guild, trade_data):
    """Post the approved trade to the main trade channel"""
    trade_channel = guild.get_channel(TRADE_CHANNEL_ID)
    if not trade_channel:
        print("❌ Could not find trade channel.")
        return

    sub_date, proc_date = get_trade_dates()
    
    msg = format_trade_review(trade_data)
    msg += f"\n\n📆 {sub_date}\n📆 {proc_date}"
    msg += "\n\n✅ **TRADE APPROVED**"

    await trade_channel.send(msg)

# ========== REJECTION MODAL (FIXED) ==========

class RejectionReasonModal(Modal):
    def __init__(self, rejector: discord.User, trade_data, channel, view):
        super().__init__(title="Trade Rejection Reason")
        self.rejector = rejector
        self.trade_data = trade_data
        self.channel = channel
        self.view = view

        self.reason = TextInput(
            label="Why are you rejecting this trade?",
            style=discord.TextStyle.paragraph,
            placeholder="Explain your reasoning...",
            required=True,
            max_length=300
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # First respond to the interaction
            await interaction.response.send_message("✅ Rejection recorded.", ephemeral=True)
            
            # Update the view to disable buttons
            await interaction.edit_original_response(view=self.view)
            
            # Then send the rejection message to the channel
            mentions = [mention_manager(team) for team in self.trade_data["teams"]]
            msg = f"""❌ **Trade rejected by <@{self.rejector.id}>**
💬 Reason: {self.reason.value}

{' '.join(mentions)}

---

{format_trade_review(self.trade_data)}
"""
            await self.channel.send(msg)
            
        except Exception as e:
            print(f"Error in rejection modal: {e}")
            # If something fails, just respond with a simple message
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Trade rejected. Reason: {self.reason.value}",
                    ephemeral=False
                )