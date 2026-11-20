"""Spec 03's automatic jobs on the bot's loop.

A thin cog: every decision lives in `league/jobs.py`, which is why that module can be
tested without a bot or a Discord connection. This file only supplies the clock, the
database connection, the MLB Stats API fetcher and the All-Star break date, then reports
what happened to the admin channel.

Gated on FBP_LEAGUE_DB, which defaults to off. With the flag unset the loop does nothing
at all, so the bot behaves exactly as it did before this cog existed.
"""

from __future__ import annotations

import traceback
from datetime import datetime

import discord
from discord.ext import commands, tasks

import fbp_db
from auction_manager import ET
# One fact in one place: the admin-task-log channel id already lives in the auction cog.
from commands.auction import ADMIN_LOG_CHANNEL_ID

#: How often the dispatcher looks for due work. The jobs themselves decide what is due
#: (nightly after 6am ET, or a window date), so this only has to be finer than that.
TICK_MINUTES = 5


class LeagueJobs(commands.Cog):
    """The scheduler for Spec 03: prospect refresh, debut watch, graduation, phase
    close, installments and the nightly roster check."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_report = None
        self.league_jobs_tick.start()

    def cog_unload(self) -> None:
        self.league_jobs_tick.cancel()

    # ------------------------------------------------------------------ helpers

    async def _get_admin_channel(self) -> "discord.TextChannel | None":
        channel = self.bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ADMIN_LOG_CHANNEL_ID)
            except Exception as exc:
                print(f"Failed to resolve admin log channel {ADMIN_LOG_CHANNEL_ID}: {exc}")
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    def _all_star_break_start(self):
        """The ET date the All-Star break begins, for the forced graduations."""
        from datetime import date

        raw = (fbp_db.auction_calendar() or {}).get("all_star_break_start")
        return date.fromisoformat(raw) if raw else None

    # --------------------------------------------------------------------- loop

    @tasks.loop(minutes=TICK_MINUTES)
    async def league_jobs_tick(self) -> None:
        """Run whatever Spec 03 job is due. Safe to run on every tick: each job checks
        its own due-ness and every one of them is idempotent."""
        if not fbp_db.league_enabled():
            return

        now = datetime.now(tz=ET)
        try:
            from league import jobs
            from lineups.mlb_api import fetch_prospect_stats

            with fbp_db.connection() as conn:
                report = jobs.run_due_jobs(
                    conn,
                    now=now,
                    fetch_prospect_stats=fetch_prospect_stats,
                    all_star_break_start=self._all_star_break_start(),
                )
        except Exception as exc:
            # A failure here is the dispatcher itself (no connection, bad config), not a
            # job -- a failing job is recorded in job_runs with its backoff instead.
            print(f"League jobs tick failed: {exc}")
            traceback.print_exc()
            return

        await self._report(report)

    @league_jobs_tick.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _report(self, report: dict) -> None:
        """Post what ran, once per distinct outcome, to the admin channel.

        Jobs that were not due are left out, and an unchanged report is not re-posted,
        so a quiet night produces no messages at all.
        """
        lines = []
        for name, res in report.items():
            if not res.get("ran"):
                continue
            if "error" in res:
                lines.append(
                    f"⚠️ `{name}` failed: {res['error']} "
                    f"(failure #{res['consecutive_failures']}, "
                    f"next attempt in {res['retry_in_seconds']}s)"
                )
            else:
                lines.append(f"✅ `{name}`: {res['message']}")
        if not lines:
            return

        body = "\n".join(lines)
        if body == self._last_report:
            return
        self._last_report = body

        channel = await self._get_admin_channel()
        if channel:
            await channel.send(f"**League jobs**\n{body}")
        else:
            print(f"League jobs:\n{body}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueJobs(bot))
