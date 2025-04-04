# commands/trade_logic.py

import discord
from discord.ui import View, Button, Modal, TextInput
from commands.utils import MANAGER_DISCORD_IDS, get_trade_dates, mention_manager

# Channel IDs
PENDING_CHANNEL_ID = 1356234086833848492
ADMIN_REVIEW_CHANNEL_ID = 875594022033436683
TRADE_CHANNEL_ID = 1197200421639438537

# ========== CREATE TRADE THREAD ==========

async def create_trade_thread(guild, trade_data):
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

    for team in teams:
        user_id = next((uid for label, uid in MANAGER_DISCORD_IDS.items() if label == team), None)
        if user_id:
            try:
                member = await guild.fetch_member(user_id)
                await thread.add_user(member)
            except Exception as e:
                print(f"⚠️ Could not add user {user_id}: {e}")

    msg = format_trade_review(trade_data) + "\n\n🔘 Please review this trade below."
    view = TradeApprovalView(trade_data)
    await thread.send(content=msg, view=view)
    return thread


# ========== FORMATTING HELPERS ==========

def format_block(team, assets):
    return f"🔁 **{team} receives:**\n" + "\n".join(assets)

def format_trade_review(trade_data):
    blocks = [format_block(team, trade_data["players"].get(team, [])) for team in trade_data["teams"]]
    return "\n\n".join(blocks)


# ========== VOTING VIEWS ==========

class TradeApprovalView(View):
    def __init__(self, trade_data):
        super().__init__(timeout=None)
        self.trade_data = trade_data
        self.approvals = set()
        self.rejected = False

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        if user_id in self.approvals:
            await interaction.response.send_message("You've already approved this trade.", ephemeral=True)
            return

        self.approvals.add(user_id)
        await interaction.response.send_message("✅ Your approval has been recorded.", ephemeral=True)

        involved_ids = [
            MANAGER_DISCORD_IDS.get(t) for t in self.trade_data["teams"]
        ]
        if all(uid in self.approvals for uid in involved_ids):
            await interaction.channel.send("✅ All managers have approved! Sending to admin review...")
            await send_to_admin_review(interaction.guild, self.trade_data)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: Button):
        if self.rejected:
            await interaction.response.send_message("❌ Trade already rejected.", ephemeral=True)
            return

        if interaction.user.id not in [MANAGER_DISCORD_IDS.get(t) for t in self.trade_data["teams"]]:
            await interaction.response.send_message("You are not authorized to reject this trade.", ephemeral=True)
            return

        self.rejected = True
        modal = RejectionReasonModal(interaction.user, self.trade_data)
        await interaction.response.send_modal(modal)


class AdminReviewView(View):
    def __init__(self, trade_data):
        super().__init__(timeout=None)
        self.trade_data = trade_data

    @discord.ui.button(label="✅ Approve Trade", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        sub_date, proc_date = get_trade_dates()

        msg = format_trade_review(self.trade_data)
        msg += f"\n\n📆 {sub_date}\n📆 {proc_date}"

        trade_channel = interaction.guild.get_channel(TRADE_CHANNEL_ID)
        if trade_channel:
            await trade_channel.send(msg)
            await interaction.response.send_message("✅ Trade approved and posted!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find trade channel.", ephemeral=True)

    @discord.ui.button(label="❌ Reject Trade", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: Button):
        modal = AdminRejectionModal(interaction.user, self.trade_data)
        await interaction.response.send_modal(modal)


# ========== SEND TO ADMIN ==========

async def send_to_admin_review(guild, trade_data):
    admin_channel = guild.get_channel(ADMIN_REVIEW_CHANNEL_ID)
    if not admin_channel:
        print("❌ Could not find admin review channel.")
        return

    msg = format_trade_review(trade_data)
    msg += "\n\n✅ Approve or ❌ Reject this trade below."

    view = AdminReviewView(trade_data)
    await admin_channel.send(content=msg, view=view)


# ========== MODALS ==========

class RejectionReasonModal(Modal):
    def __init__(self, rejector: discord.User, trade_data):
        super().__init__(title="Trade Rejection Reason")
        self.rejector = rejector
        self.trade_data = trade_data

        self.reason = TextInput(
            label="Why are you rejecting this trade?",
            style=discord.TextStyle.paragraph,
            placeholder="Explain your reasoning...",
            required=True,
            max_length=300
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        mentions = [mention_manager(team) for team in self.trade_data["teams"]]
        msg = f"""❌ **Trade rejected by <@{self.rejector.id}>**
💬 Reason: {self.reason.value}

{' '.join(mentions)}

---

{format_trade_review(self.trade_data)}
"""
        await interaction.message.channel.send(msg)
        pass  # No response needed


class AdminRejectionModal(Modal):
    def __init__(self, rejector: discord.User, trade_data):
        super().__init__(title="Admin Rejection Reason")
        self.rejector = rejector
        self.trade_data = trade_data

        self.reason = TextInput(
            label="Why is this trade being rejected?",
            style=discord.TextStyle.paragraph,
            placeholder="Explain your admin-level rejection...",
            required=True,
            max_length=300
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        mentions = [mention_manager(team) for team in self.trade_data["teams"]]
        msg = f"""❌ **Trade rejected by Admin <@{self.rejector.id}>**
💬 Reason: {self.reason.value}

{' '.join(mentions)}

---

{format_trade_review(self.trade_data)}
"""
        await interaction.message.channel.send(msg)
        await interaction.response.send_message("✅ Admin rejection sent to thread.", ephemeral=True)
