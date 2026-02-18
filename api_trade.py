from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

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

_bot_ref = None
_commit_fn = None


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

    try:
        guild_id = trade_store._load_json("config/season_dates.json", {}).get("guild_id")
        # guild_id is not persisted in season_dates; rely on bot cache instead.
        thread = None
        for g in _bot_ref.guilds:
            t = g.get_thread(int(thread_id))
            if t:
                thread = t
                break
        if thread:
            await thread.send(content)
    except Exception as exc:
        print(f"⚠️ Failed posting trade note to thread: {exc}")




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

        # Use the guild the bot is connected to
        if not _bot_ref.guilds:
            raise HTTPException(status_code=500, detail="Bot is not connected to any guild")

        guild = _bot_ref.guilds[0]
        thread = await create_trade_thread(
            guild,
            {
                "trade_id": trade["trade_id"],
                "teams": trade["teams"],
                "players": trade.get("receives") or {},
                "initiator_team": trade.get("initiator_team"),
                "source": "🌐 Website",
            },
        )
        if not thread:
            raise HTTPException(status_code=500, detail="Failed to create Discord thread")

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
        # Ping admin review in Discord
        try:
            from commands.trade_logic import send_to_admin_review

            if _bot_ref and _bot_ref.is_ready() and _bot_ref.guilds:
                await send_to_admin_review(
                    _bot_ref.guilds[0],
                    {
                        "trade_id": trade.get("trade_id"),
                        "teams": trade.get("teams") or [],
                        "players": trade.get("receives") or {},
                        "initiator_team": trade.get("initiator_team"),
                        "source": "🌐 Website",
                    },
                )
        except Exception as exc:
            print(f"⚠️ Failed to send trade to admin review: {exc}")

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
