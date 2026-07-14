import json
import os

import discord
from discord.ext import commands, tasks
from discord import app_commands

from auction_manager import AuctionManager, AuctionPhase, ET
from commands.utils import DISCORD_ID_TO_TEAM, MANAGER_DISCORD_IDS
from data_lock import DATA_LOCK

AUCTION_CHANNEL_ID = 1351376690319851520  # dedicated auction channel
AUCTION_BOARD_URL = "https://zpressley.github.io/fbp-hub/auction.html"
PLAYER_PROFILE_BASE_URL = "https://zpressley.github.io/fbp-hub/player-profile.html"
_AUCTION_RESOLVED_STATE_FILE = "data/auction_resolved_state.json"
_PERSIST_WARNING_HEADER = "⚠️ **Auction Resolution Pending Persistence**"
_PERSIST_RECOVERY_HEADER = "✅ **Auction Persistence Recovered — Transactions Applied**"
_RESOLVED_SUCCESS_HEADER = "🏁 **Auction Resolved — Transactions Applied**"
_PERSIST_WARNING_MEME_CAPTION = "THE AUCTION BOT WILL PERSIST"
_PERSIST_SUCCESS_MEME_CAPTION = "THE AUCTION BOT WILL NOT PERSIST."
_PERSIST_WARNING_MEME_URL = os.getenv("AUCTION_PERSIST_WARNING_MEME_URL", "").strip()
_PERSIST_SUCCESS_MEME_URL = os.getenv("AUCTION_PERSIST_SUCCESS_MEME_URL", "").strip()
_RESOLVE_RETRY_DELAYS_SECONDS = (60, 120, 300, 600, 900)

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
        self._resolve_persist_warn_week = None  # guard for persistence failure warning
        self._resolve_recovered_week = None  # guard for persistence recovery message
        self._resolve_retry_week = None  # week key for retry backoff state
        self._resolve_retry_attempts = 0  # backoff attempt counter for Sunday resolve retries
        self._resolve_next_retry_at = None  # ET datetime for next allowed retry
        self._last_summary_date = None  # YYYY-MM-DD in ET
        self._last_synced_phase = None  # track phase for website sync

        # Start background task that checks phase/time once per minute
        self.auction_tick.start()

    def _commit_auction_files(
        self,
        file_paths: list[str],
        message: str,
        *,
        wait: bool = False,
        timeout_seconds: float = 30.0,
    ) -> bool:
        """Run auction commit callback with optional acknowledgement."""
        if not _auction_commit_fn:
            return False

        try:
            if wait:
                try:
                    result = _auction_commit_fn(
                        file_paths,
                        message,
                        wait=True,
                        timeout_seconds=timeout_seconds,
                    )
                except TypeError:
                    _auction_commit_fn(file_paths, message)
                    return True
                return True if result is None else bool(result)

            _auction_commit_fn(file_paths, message)
            return True
        except Exception as exc:
            print(f"⚠️ Auction commit callback failed: {exc}")
            return False

    def _write_resolved_guard(self, week_key: str) -> None:
        """Persist the weekly resolve guard locally."""
        with open(_AUCTION_RESOLVED_STATE_FILE, "w") as f:
            json.dump({"resolved_week": week_key}, f)

    def _clear_resolved_guard(self) -> None:
        """Clear in-memory + local resolved guard so retries continue."""
        self._resolved_week = None
        try:
            if os.path.exists(_AUCTION_RESOLVED_STATE_FILE):
                os.remove(_AUCTION_RESOLVED_STATE_FILE)
        except Exception:
            pass

    def _resolve_retry_delay_seconds(self) -> int:
        """Return retry delay for the current Sunday persistence attempt."""
        idx = min(self._resolve_retry_attempts, len(_RESOLVE_RETRY_DELAYS_SECONDS) - 1)
        return _RESOLVE_RETRY_DELAYS_SECONDS[idx]

    async def _has_weekly_notice(
        self,
        channel: discord.TextChannel,
        *,
        header: str,
        week_key: str,
        limit: int = 120,
    ) -> bool:
        """Check recent channel history for a bot notice tied to this week."""
        try:
            me = self.bot.user
            async for msg in channel.history(limit=limit):
                if me and msg.author.id != me.id:
                    continue
                content = msg.content or ""
                if header in content and f"Week: `{week_key}`" in content:
                    return True
        except Exception as exc:
            print(f"⚠️ Failed checking weekly notice history: {exc}")
        return False

    @staticmethod
    def _append_meme_block(lines: list[str], caption: str, meme_url: str) -> list[str]:
        """Append meme caption + URL (when configured) to a notice payload."""
        lines.extend(["", f"🖼️ **{caption}**"])
        if meme_url:
            lines.append(meme_url)
        return lines

    def _build_persistence_failure_notice(self, week_key: str, retry_seconds: int) -> str:
        lines = [
            _PERSIST_WARNING_HEADER,
            f"Week: `{week_key}`",
            "",
            "Resolution ran, but transactions are **not yet confirmed** because persistence failed.",
            f"The bot will retry automatically in about **{max(1, retry_seconds // 60)} minute(s)**.",
        ]
        self._append_meme_block(lines, _PERSIST_WARNING_MEME_CAPTION, _PERSIST_WARNING_MEME_URL)
        return "\n".join(lines)

    def _build_resolved_success_notice(
        self,
        *,
        week_key: str,
        winners: dict[str, dict],
        recovered: bool,
    ) -> str:
        lines = [
            _PERSIST_RECOVERY_HEADER if recovered else _RESOLVED_SUCCESS_HEADER,
            f"Week: `{week_key}`",
            "",
        ]
        if winners:
            for pid, info in winners.items():
                name = info.get("name", _resolve_prospect_name(pid))
                lines.append(f"✅ **{info['team']}** → {_player_profile_link(pid, name)} (${info['amount']} WB)")
        else:
            lines.append("No winners recorded for this week.")
        self._append_meme_block(lines, _PERSIST_SUCCESS_MEME_CAPTION, _PERSIST_SUCCESS_MEME_URL)
        return "\n".join(lines)

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
            AuctionPhase.OB_FINAL: "OB managers may match or forfeit (Sat\u2013Sun 10am ET).",
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

        from datetime import datetime, timedelta

        now = datetime.now(tz=ET)
        phase = self.manager.get_current_phase(now)

        # Sync phase to auction_current.json + GitHub so the website stays current.
        # Skip the commit during the Sunday resolve window to avoid triggering
        # a Railway redeploy before the resolve guard lands.
        phase_val = phase.value
        is_resolve_window = now.weekday() == 6 and now.hour >= 10
        if phase_val != self._last_synced_phase:
            try:
                state = self.manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
                if state.get("phase") != phase_val:
                    state["phase"] = phase_val
                    self.manager._save_auction_state(state)  # type: ignore[attr-defined]
                    if _auction_commit_fn and not is_resolve_window:
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
        if self._resolve_retry_week != week_key:
            self._resolve_retry_week = week_key
            self._resolve_retry_attempts = 0
            self._resolve_next_retry_at = None
            self._resolve_persist_warn_week = None
            self._resolve_recovered_week = None

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

        # Sunday 10:00am+ ET: resolve the week once (assign winners, charge WB).
        # Keep trying all Sunday after 10am so brief deploys/restarts don't miss
        # the entire resolve.
        if now.weekday() == 6 and now.hour >= 10 and self._resolved_week != week_key:
            if self._resolve_next_retry_at and now < self._resolve_next_retry_at:
                return
            try:

                # Same read-modify-write cycle health.py's /api/admin/auction/resolve-now
                # endpoint protects with DATA_LOCK — without it, this tick loop and a
                # manual/admin-triggered resolve-now call could interleave and both
                # believe they're the one resolving the week. resolve_week() itself
                # sets state["resolved_at"] before returning, so locking just this
                # call is enough to make that guard atomic; the git-commit calls below
                # stay outside the lock so we never block the event loop across the
                # Discord awaits that sit between resolving and persisting.
                with DATA_LOCK:
                    result = self.manager.resolve_week(now=now)
                status = result.get("status", "")
                winners = result.get("winners", {}) or {}
                channel = await self._get_auction_channel()
                warning_seen_in_history = False
                if channel:
                    warning_seen_in_history = await self._has_weekly_notice(
                        channel,
                        header=_PERSIST_WARNING_HEADER,
                        week_key=week_key,
                    )
                had_persistence_warning = (
                    self._resolve_persist_warn_week == week_key
                    or warning_seen_in_history
                )

                if status in {"resolved", "already_resolved"}:
                    with DATA_LOCK:
                        commit_ok = self._commit_auction_files(
                            [
                                "data/auction_current.json",
                                "data/combined_players.json",
                                "data/wizbucks.json",
                                "data/wizbucks_transactions.json",
                                "data/player_log.json",
                            ],
                            f"Auction resolved: week of {week_key}",
                            wait=True,
                            timeout_seconds=60.0,
                        )

                    if commit_ok:
                        self._resolve_retry_attempts = 0
                        self._resolve_next_retry_at = None
                        guard_ok = False
                        try:
                            with DATA_LOCK:
                                self._write_resolved_guard(week_key)
                                guard_ok = self._commit_auction_files(
                                    [_AUCTION_RESOLVED_STATE_FILE],
                                    f"Auction resolve guard: week of {week_key}",
                                    wait=True,
                                    timeout_seconds=30.0,
                                )
                        except Exception as guard_exc:
                            print(f"⚠️ Failed to persist auction resolve guard: {guard_exc}")

                        if guard_ok:
                            self._resolved_week = week_key
                        else:
                            self._clear_resolved_guard()
                            print("⚠️ Failed to persist auction resolve guard commit")
                        if channel and status == "resolved":
                            success_header = (
                                _PERSIST_RECOVERY_HEADER if had_persistence_warning else _RESOLVED_SUCCESS_HEADER
                            )
                            already_announced = await self._has_weekly_notice(
                                channel,
                                header=success_header,
                                week_key=week_key,
                            )
                            if not already_announced:
                                await channel.send(
                                    self._build_resolved_success_notice(
                                        week_key=week_key,
                                        winners=winners,
                                        recovered=had_persistence_warning,
                                    )
                                )
                            if had_persistence_warning:
                                self._resolve_recovered_week = week_key
                        elif channel and had_persistence_warning:
                            already_recovered = (
                                self._resolve_recovered_week == week_key
                                or await self._has_weekly_notice(
                                    channel,
                                    header=_PERSIST_RECOVERY_HEADER,
                                    week_key=week_key,
                                )
                            )
                            if not already_recovered:
                                await channel.send(
                                    self._build_resolved_success_notice(
                                        week_key=week_key,
                                        winners=winners,
                                        recovered=True,
                                    )
                                )
                            self._resolve_recovered_week = week_key

                        if had_persistence_warning:
                            self._resolve_persist_warn_week = None

                        if status == "resolved":
                            print(f"✅ Auction resolved: {len(winners)} winners")
                        else:
                            print(f"ℹ️ Auction already resolved for week {week_key}; persistence confirmed")
                    else:
                        self._clear_resolved_guard()
                        retry_seconds = self._resolve_retry_delay_seconds()
                        self._resolve_retry_attempts += 1
                        self._resolve_next_retry_at = now + timedelta(seconds=retry_seconds)
                        if channel:
                            already_warned = (
                                self._resolve_persist_warn_week == week_key
                                or await self._has_weekly_notice(
                                    channel,
                                    header=_PERSIST_WARNING_HEADER,
                                    week_key=week_key,
                                )
                            )
                            if not already_warned:
                                await channel.send(
                                    self._build_persistence_failure_notice(
                                        week_key,
                                        retry_seconds,
                                    )
                                )
                        self._resolve_persist_warn_week = week_key
                elif status == "no_bids":
                    guard_ok = False
                    try:
                        with DATA_LOCK:
                            self._write_resolved_guard(week_key)
                            guard_ok = self._commit_auction_files(
                                [_AUCTION_RESOLVED_STATE_FILE, "data/auction_current.json"],
                                f"Auction resolve guard: week of {week_key} [no bids]",
                                wait=True,
                                timeout_seconds=30.0,
                            )
                    except Exception as guard_exc:
                        print(f"⚠️ Failed to persist no-bid resolve guard: {guard_exc}")

                    if guard_ok:
                        self._resolved_week = week_key
                    else:
                        self._clear_resolved_guard()
                        print("⚠️ Failed to persist no-bid resolve guard commit")
                    self._resolve_persist_warn_week = None
                    self._resolve_recovered_week = None
                    self._resolve_retry_attempts = 0
                    self._resolve_next_retry_at = None
                    print("Auction resolve: no bids this week")
                elif status == "inactive":
                    pass
            except Exception as exc:
                import traceback
                print(f"⚠️ Auction resolve failed: {exc}")
                traceback.print_exc()
        # Daily summary: 9:00–9:59am ET, Tue–Fri only (Saturday has weekly summary).
        # Hour-long window prevents missed updates after brief restarts.
        if now.weekday() in (1, 2, 3, 4) and now.hour == 9:
            if self._last_summary_date != date_key:
                sent_or_already_done = await self._send_daily_summary()
                if sent_or_already_done:
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

    async def _daily_summary_already_posted_today(
        self,
        channel: discord.TextChannel,
        date_key: str,
    ) -> bool:
        """Check recent history to prevent duplicate daily summaries after restarts."""
        try:
            me = self.bot.user
            async for msg in channel.history(limit=100):
                if me and msg.author.id != me.id:
                    continue
                if not (msg.content or "").startswith("📊 Daily Auction Summary"):
                    continue
                if msg.created_at.astimezone(ET).date().isoformat() == date_key:
                    return True
        except Exception as exc:
            print(f"⚠️ Failed checking daily summary history: {exc}")
        return False

    async def _send_daily_summary(self) -> bool:
        """Post a daily auction summary similar to legacy Apps Script."""

        channel = await self._get_auction_channel()
        if not channel:
            return False

        # Load current auction state
        from datetime import datetime

        now = datetime.now(tz=ET)
        date_key = now.date().isoformat()

        if await self._daily_summary_already_posted_today(channel, date_key):
            print(f"ℹ️ Daily auction summary already posted for {date_key}; skipping duplicate send")
            return True
        state = self.manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
        bids = state.get("bids", [])

        if not bids:
            await channel.send(
                "📊 Daily Auction Summary\n\nNo active bids yet this week.\n\n"
                f"🔗 Auction Board: {AUCTION_BOARD_URL}"
            )
            return True

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
        return True


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
