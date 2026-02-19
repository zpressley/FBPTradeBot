# commands/trade_logic.py - Simplified version with fixed rejection modal

import os
import json
import asyncio

import discord
from discord.ui import View, Button, Modal, TextInput

from commands.utils import MANAGER_DISCORD_IDS, DISCORD_ID_TO_TEAM, get_trade_dates, mention_manager


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


# Channel IDs (overrideable via env for different servers / staging)
# Trade approval threads are created as private threads under this parent channel.
# League setting (Feb 2026): use the transactions channel as the parent.
PENDING_CHANNEL_ID = _env_int("TRADE_PENDING_CHANNEL_ID", 1089979265619083444)  # trade discussion threads

# League setting (Feb 2026): post final approved trade messages to the transactions channel.
TRADE_CHANNEL_ID = _env_int("TRADE_CHANNEL_ID", 1089979265619083444)  # final approved trades

# Admin review channel
TRADE_ADMIN_REVIEW_CHANNEL_ID = _env_int("TRADE_ADMIN_REVIEW_CHANNEL_ID", 875594022033436683)

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
    """Create private thread for trade discussion.

    If `trade_data` contains `trade_id`, it is assumed to be a website-submitted
    trade and will be synced to data/trades.json as approvals happen.
    """
    channel = guild.get_channel(PENDING_CHANNEL_ID)
    if not channel:
        try:
            channel = await guild.fetch_channel(PENDING_CHANNEL_ID)
        except Exception as exc:
            print(
                "❌ Could not resolve pending-trades channel",
                {"guild": getattr(guild, "name", None), "channel_id": PENDING_CHANNEL_ID, "error": str(exc)},
            )
            return

    if not channel:
        print(
            "❌ Pending-trades channel not found",
            {"guild": getattr(guild, "name", None), "channel_id": PENDING_CHANNEL_ID},
        )
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
    source = trade_data.get("source") or ("🌐 Website" if trade_data.get("trade_id") else "💬 Discord")
    trade_id = trade_data.get("trade_id")
    header = f"{source}"
    if trade_id:
        header += f" | Trade ID: `{trade_id}`"

    msg = header + "\n\n" + format_trade_review(trade_data) + "\n\n🔘 Please review and approve this trade below."

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

        # If the initiator team is known, treat them as auto-approved (submission implies consent).
        initiator_team = str(trade_data.get("initiator_team") or "").strip().upper()
        if initiator_team:
            initiator_id = MANAGER_DISCORD_IDS.get(initiator_team)
            if initiator_id:
                self.approvals.add(int(initiator_id))

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

        # Sync website trade (if applicable) BEFORE recording approval in-thread.
        trade_id = self.trade_data.get("trade_id")
        accepting_team = DISCORD_ID_TO_TEAM.get(user_id)
        if trade_id and accepting_team:
            try:
                from fastapi import HTTPException
                from trade import trade_store

                trade, all_accepted = trade_store.accept_trade(trade_id, accepting_team)

                # Now record approval locally (Discord view state)
                self.approvals.add(user_id)
                await interaction.response.send_message("✅ Your approval has been recorded.", ephemeral=True)

                if all_accepted:
                    # Disable buttons to prevent further interaction noise
                    for item in self.children:
                        item.disabled = True
                    try:
                        await interaction.message.edit(view=self)
                    except Exception:
                        pass

                    await interaction.channel.send("✅ All managers have approved! Sending to admin review...")
                    await send_to_admin_review(interaction.guild, self.trade_data)

                return
            except HTTPException as exc:
                if exc.status_code == 409:
                    # Store has auto-withdrawn the trade due to conflicting assets.
                    self.rejected = True
                    for item in self.children:
                        item.disabled = True
                    try:
                        await interaction.message.edit(view=self)
                    except Exception:
                        pass

                    await interaction.response.send_message(
                        "❌ This trade was automatically withdrawn due to a conflicting trade (assets are no longer owned).",
                        ephemeral=True,
                    )
                    try:
                        await interaction.channel.send(
                            "⚠️ Trade auto-withdrawn due to conflicting trade (assets no longer owned)."
                        )
                    except Exception:
                        pass
                    return

                await interaction.response.send_message(f"❌ Failed to sync acceptance: {exc.detail}", ephemeral=True)
                return
            except Exception as exc:
                print(f"⚠️ Failed to sync trade acceptance to store: {exc}")
                await interaction.response.send_message(
                    "❌ Could not record your acceptance (trade store sync failed).",
                    ephemeral=True,
                )
                return

        # Discord-only trades: record approval locally
        self.approvals.add(user_id)
        await interaction.response.send_message("✅ Your approval has been recorded.", ephemeral=True)

        # Check if all parties have approved
        if all(uid in self.approvals for uid in involved_ids if uid is not None):
            # Disable buttons to prevent further interaction noise
            for item in self.children:
                item.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

            await interaction.channel.send("✅ All managers have approved! Sending to admin review...")
            await send_to_admin_review(interaction.guild, self.trade_data)

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

def _load_admin_discord_ids() -> set[int]:
    """Load admin discord IDs from config/managers.json (role == 'admin')."""
    try:
        with open("config/managers.json", "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        teams = cfg.get("teams") or {}
        ids: set[int] = set()
        for meta in teams.values():
            if not isinstance(meta, dict):
                continue
            if str(meta.get("role") or "").strip().lower() != "admin":
                continue
            raw = meta.get("discord_id")
            if raw:
                try:
                    ids.add(int(raw))
                except Exception:
                    pass
        return ids
    except Exception:
        return set()


async def send_to_admin_review(guild, trade_data):
    """Send a manager-approved trade to the admin review channel."""
    admin_channel = guild.get_channel(TRADE_ADMIN_REVIEW_CHANNEL_ID)
    if not admin_channel:
        try:
            admin_channel = await guild.fetch_channel(TRADE_ADMIN_REVIEW_CHANNEL_ID)
        except Exception as exc:
            print(
                "❌ Could not resolve admin review channel",
                {"guild": getattr(guild, "name", None), "channel_id": TRADE_ADMIN_REVIEW_CHANNEL_ID, "error": str(exc)},
            )
            return

    if not admin_channel:
        print("❌ Admin review channel not found.", {"channel_id": TRADE_ADMIN_REVIEW_CHANNEL_ID})
        return

    trade_id = trade_data.get("trade_id")
    source = trade_data.get("source") or ("🌐 Website" if trade_id else "💬 Discord")

    msg = f"🛡️ **Admin Review Required** ({source})\n"
    if trade_id:
        msg += f"Trade ID: `{trade_id}`\n"
    msg += "\n" + format_trade_review(trade_data)

    # Try to include a direct link to the thread when available (web trades store this on submit)
    try:
        if trade_id:
            from trade import trade_store

            trade = trade_store.get_trade(trade_id)
            thread_url = (trade.get("discord") or {}).get("thread_url")
            if thread_url:
                msg += f"\n\nThread: {thread_url}"
    except Exception:
        pass

    view = AdminReviewView(trade_data)
    await admin_channel.send(content=msg, view=view)


async def post_approved_trade(guild, trade_data):
    """Post the approved trade to the main trade channel"""
    trade_channel = guild.get_channel(TRADE_CHANNEL_ID)
    if not trade_channel:
        try:
            trade_channel = await guild.fetch_channel(TRADE_CHANNEL_ID)
        except Exception as exc:
            print(
                "❌ Could not resolve trade channel",
                {"guild": getattr(guild, "name", None), "channel_id": TRADE_CHANNEL_ID, "error": str(exc)},
            )
            return

    if not trade_channel:
        print("❌ Trade channel not found.", {"channel_id": TRADE_CHANNEL_ID})
        return

    sub_date, proc_date = get_trade_dates()
    
    msg = format_trade_review(trade_data)
    msg += f"\n\n📆 {sub_date}\n📆 {proc_date}"
    msg += "\n\n✅ **TRADE APPROVED**"

    await trade_channel.send(msg)

# ========== REJECTION MODAL (FIXED) ==========

class AdminRejectionModal(Modal):
    def __init__(self, rejector: discord.User, trade_data):
        super().__init__(title="Admin Trade Rejection")
        self.rejector = rejector
        self.trade_data = trade_data

        self.reason = TextInput(
            label="Reason for rejection",
            style=discord.TextStyle.paragraph,
            placeholder="Explain why this trade is being rejected...",
            required=True,
            max_length=300,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        # ACK immediately to avoid Discord's modal-submit timeout.
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        trade_id = self.trade_data.get("trade_id")
        admin_team = DISCORD_ID_TO_TEAM.get(interaction.user.id) or "ADMIN"

        if trade_id:
            try:
                from trade import trade_store

                trade_store.admin_reject(trade_id, admin_team, self.reason.value)
            except Exception as exc:
                print(f"⚠️ Failed to sync admin rejection to store: {exc}")

        try:
            await interaction.followup.send("❌ Trade rejected.", ephemeral=True)
        except Exception:
            pass


class AdminReviewView(View):
    def __init__(self, trade_data):
        super().__init__(timeout=None)
        self.trade_data = trade_data
        self.admin_ids = _load_admin_discord_ids()
        self._decision_lock = asyncio.Lock()
        self._decision_made = False

    def _is_admin(self, user_id: int) -> bool:
        if self.admin_ids:
            return user_id in self.admin_ids
        # fallback: allow any known manager if config fails
        return True

    @discord.ui.button(label="✅ Approve Trade", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        if not self._is_admin(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        async with self._decision_lock:
            if self._decision_made:
                await interaction.response.send_message("⚠️ This trade has already been processed.", ephemeral=True)
                return

            trade_id = self.trade_data.get("trade_id")
            admin_team = DISCORD_ID_TO_TEAM.get(interaction.user.id) or "ADMIN"

            # High-signal log: admin approvals are rare and debugging timeouts is hard.
            try:
                print(
                    "🧾 DISCORD_ADMIN_APPROVE_CLICK",
                    {"trade_id": trade_id, "admin": admin_team, "user_id": interaction.user.id},
                )
            except Exception:
                pass

            # ACK the interaction immediately to avoid Discord's 3s timeout.
            # We'll edit the admin-review message directly via interaction.message.
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                # If defer fails (e.g. already timed out), we still try best-effort message edits.
                pass

            # For website-backed trades, approve in the store first (this applies data + can auto-withdraw on conflict)
            if trade_id:
                try:
                    from fastapi import HTTPException
                    from trade import trade_store

                    trade_store.admin_approve(trade_id, admin_team)
                except HTTPException as exc:
                    if exc.status_code == 409:
                        # Disable buttons after decision
                        for item in self.children:
                            item.disabled = True

                        self._decision_made = True
                        try:
                            await interaction.message.edit(
                                content="❌ Trade auto-withdrawn due to conflicting trade (assets no longer owned).",
                                view=self,
                            )
                        except Exception:
                            pass

                        try:
                            trade = trade_store.get_trade(trade_id)
                            details = trade.get("withdraw_details") or []
                            if details:
                                detail_lines = "\n".join(f"• {d}" for d in details[:6])
                                await interaction.channel.send("⚠️ Auto-withdraw details:\n" + detail_lines)
                        except Exception:
                            pass

                        try:
                            await interaction.followup.send(
                                "❌ Trade auto-withdrawn due to conflicting trade.",
                                ephemeral=True,
                            )
                        except Exception:
                            pass

                        return

                    if exc.status_code == 400:
                        # Likely already approved/processed by another admin.
                        self._decision_made = True
                        try:
                            await interaction.followup.send("⚠️ Trade has already been processed.", ephemeral=True)
                        except Exception:
                            pass
                        return

                    try:
                        await interaction.followup.send(f"❌ Approval failed: {exc.detail}", ephemeral=True)
                    except Exception:
                        pass
                    return
                except Exception as exc:
                    import traceback

                    print(f"⚠️ Failed to sync admin approval to store: {exc}")
                    traceback.print_exc()
                    try:
                        await interaction.followup.send("❌ Approval failed (store sync error).", ephemeral=True)
                    except Exception:
                        pass
                    return

            # Post to #trades
            await post_approved_trade(interaction.guild, self.trade_data)

            # Disable buttons after decision
            for item in self.children:
                item.disabled = True

            self._decision_made = True
            try:
                await interaction.message.edit(content="✅ Approved and posted.", view=self)
            except Exception:
                pass

            try:
                await interaction.followup.send("✅ Approved and posted.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="❌ Reject Trade", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: Button):
        if not self._is_admin(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        async with self._decision_lock:
            if self._decision_made:
                await interaction.response.send_message("⚠️ This trade has already been processed.", ephemeral=True)
                return

            modal = AdminRejectionModal(interaction.user, self.trade_data)
            await interaction.response.send_modal(modal)


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

            # Sync website trade (if applicable)
            trade_id = self.trade_data.get("trade_id")
            rejecting_team = DISCORD_ID_TO_TEAM.get(self.rejector.id)
            if trade_id and rejecting_team:
                try:
                    from trade import trade_store

                    trade_store.reject_trade(trade_id, rejecting_team, self.reason.value)
                except Exception as exc:
                    print(f"⚠️ Failed to sync trade rejection to store: {exc}")
            
        except Exception as e:
            print(f"Error in rejection modal: {e}")
            # If something fails, just respond with a simple message
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Trade rejected. Reason: {self.reason.value}",
                    ephemeral=False
                )