from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Optional, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException

from trade.trade_models import (
    TradeAcceptPayload,
    TradeRejectPayload,
    TradeSubmitPayload,
    TradeWithdrawPayload,
)
from trade import trade_store


router = APIRouter(prefix="/api/trade", tags=["trade"])

API_KEY = os.getenv("BOT_API_KEY", "")

# Prefer an explicit guild selection in multi-guild deployments.
# This should be your primary FBP server ID.
DEFAULT_DISCORD_GUILD_ID = 875592505926758480

_bot_ref = None
_commit_fn = None

T = TypeVar("T")


def _schedule_on_bot_loop(coro, label: str) -> None:
    """Fire-and-forget a Discord coroutine on the bot's event loop.

    health.py runs FastAPI in a separate thread, so Discord operations must run
    on the bot loop (main thread).
    """
    if _bot_ref is None:
        return

    loop = getattr(_bot_ref, "loop", None)
    if not loop or not loop.is_running():
        return

    fut = asyncio.run_coroutine_threadsafe(coro, loop)

    def _done(f):
        try:
            f.result()
        except Exception as exc:
            print(f"⚠️ Discord task failed ({label}): {exc}")

    fut.add_done_callback(_done)


async def _await_on_bot_loop(coro, label: str, timeout_s: float = 20.0) -> T:
    """Await a Discord coroutine on the bot's event loop and return its result."""
    if _bot_ref is None:
        raise HTTPException(status_code=503, detail="Bot not ready")

    loop = getattr(_bot_ref, "loop", None)
    if not loop or not loop.is_running():
        raise HTTPException(status_code=503, detail="Bot loop not ready")

    try:
        if asyncio.get_running_loop() == loop:
            return await coro
    except RuntimeError:
        pass

    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Discord operation timed out: {label}")


def set_trade_bot_reference(bot):
    global _bot_ref
    _bot_ref = bot


def set_trade_commit_fn(fn):
    global _commit_fn
    _commit_fn = fn
    try:
        trade_store.set_commit_fn(fn)
    except Exception:
        pass


def _select_guild():
    """Pick the correct guild for trade threads.

    We avoid relying on bot.guilds[0], which is unstable if the bot is in
    multiple servers.
    """
    if _bot_ref is None:
        return None

    raw = os.getenv("DISCORD_GUILD_ID") or os.getenv("FBP_GUILD_ID")
    guild_id = None
    if raw:
        try:
            guild_id = int(raw)
        except Exception:
            guild_id = None

    if guild_id is None:
        guild_id = DEFAULT_DISCORD_GUILD_ID

    guild = _bot_ref.get_guild(int(guild_id))
    if guild:
        return guild

    # Fallback: last resort, use the first connected guild.
    return _bot_ref.guilds[0] if _bot_ref.guilds else None


def verify_key(x_api_key: Optional[str] = Header(None)) -> bool:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def require_manager_team(x_manager_team: Optional[str] = Header(None)) -> str:
    if not x_manager_team:
        raise HTTPException(status_code=401, detail="Missing X-Manager-Team")
    return str(x_manager_team).strip().upper()


async def _post_thread_note(trade: dict, content: str) -> None:
    if _bot_ref is None:
        return

    discord_meta = trade.get("discord") or {}
    thread_id = discord_meta.get("thread_id")
    if not thread_id:
        return

    async def _send() -> None:
        try:
            chan = _bot_ref.get_channel(int(thread_id))
            if not chan:
                try:
                    chan = await _bot_ref.fetch_channel(int(thread_id))
                except Exception:
                    chan = None
            if chan:
                await chan.send(content)
        except Exception as exc:
            print(f"⚠️ Failed posting trade note to thread: {exc}")

    _schedule_on_bot_loop(_send(), "post_thread_note")




@router.post("/submit")
async def submit_trade(
    payload: TradeSubmitPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    if _bot_ref is None or not _bot_ref.is_ready():
        raise HTTPException(status_code=503, detail="Bot not ready")

    # Create trade record (validates window + rosters + WB)
    trade = trade_store.create_trade(payload, actor_team=manager_team)

    # Create Discord thread for manager approvals
    try:
        from commands.trade_logic import create_trade_thread

        async def _create_thread():
            guild = _select_guild()
            if not guild:
                raise HTTPException(status_code=500, detail="Could not resolve Discord guild for trade threads")

            return await create_trade_thread(
                guild,
                {
                    "trade_id": trade["trade_id"],
                    "teams": trade["teams"],
                    "players": trade.get("receives") or {},
                    "initiator_team": trade.get("initiator_team"),
                    "source": "🌐 Website",
                },
            )

        thread = await _await_on_bot_loop(_create_thread(), "create_trade_thread")
        if not thread:
            raise HTTPException(
                status_code=500,
                detail="Failed to create Discord thread (pending-trades channel missing or bot lacks permissions)",
            )

        trade = trade_store.attach_discord_thread(trade["trade_id"], str(thread.id), thread.jump_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create trade thread: {exc}")

    return {
        "success": True,
        "trade": trade,
    }


@router.get("/queue")
async def get_queue(
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    return {"trades": trade_store.list_queue(manager_team)}


@router.get("/inbox")
async def get_inbox(
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    return {"trades": trade_store.list_inbox(manager_team)}


@router.get("/inbox/{trade_id}")
async def get_inbox_detail(
    trade_id: str,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    trade = trade_store.get_trade(trade_id)
    teams = [t.upper() for t in (trade.get("teams") or [])]
    if manager_team.upper() not in teams:
        raise HTTPException(status_code=403, detail="Not part of this trade")
    if trade.get("initiator_team", "").upper() == manager_team.upper():
        raise HTTPException(status_code=403, detail="Use queue to view your sent trade")
    return trade


@router.post("/accept")
async def accept_trade(
    payload: TradeAcceptPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    trade, all_accepted = trade_store.accept_trade(payload.trade_id, manager_team)

    # Post a note to the Discord thread
    await _post_thread_note(trade, f"✅ **{manager_team}** accepted via website")

    if all_accepted:
        # Ping admin review in Discord (must run on bot loop)
        try:
            from commands.trade_logic import send_to_admin_review

            async def _send_admin_review() -> None:
                guild = _select_guild()
                if not guild:
                    return
                await send_to_admin_review(
                    guild,
                    {
                        "trade_id": trade.get("trade_id"),
                        "teams": trade.get("teams") or [],
                        "players": trade.get("receives") or {},
                        "initiator_team": trade.get("initiator_team"),
                        "source": "🌐 Website",
                    },
                )

            _schedule_on_bot_loop(_send_admin_review(), "send_to_admin_review")
        except Exception as exc:
            print(f"⚠️ Failed to schedule trade admin review: {exc}")

    return {"success": True, "status": trade.get("status"), "all_accepted": all_accepted}


@router.post("/reject")
async def reject_trade(
    payload: TradeRejectPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    trade = trade_store.reject_trade(payload.trade_id, manager_team, payload.reason)
    await _post_thread_note(trade, f"❌ **{manager_team}** rejected via website: {payload.reason}")
    return {"success": True, "status": trade.get("status")}


@router.post("/withdraw")
async def withdraw_trade(
    payload: TradeWithdrawPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    trade = trade_store.withdraw_trade(payload.trade_id, manager_team)
    await _post_thread_note(trade, f"🗑️ **{manager_team}** withdrew this trade via website")
    return {"success": True, "status": trade.get("status")}


@router.get("/history")
async def get_history(
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    return {"trades": trade_store.list_history(manager_team)}
