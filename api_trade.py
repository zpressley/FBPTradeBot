from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Optional, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from team_utils import normalize_team_abbr
from trade.trade_models import (
    TradeAcceptPayload,
    TradeRejectPayload,
    TradeSubmitPayload,
    TradeWithdrawPayload,
)
from trade import trade_store


def _log(event: str, data: dict) -> None:
    """Minimal structured logs for Render.

    Keep these high-signal: only call from state-mutating endpoints.
    """

    try:
        print(event, data)
    except Exception:
        try:
            print(event)
        except Exception:
            pass
TRADE_POLL_IDLE_SECONDS = int(os.getenv("TRADE_POLL_IDLE_SECONDS", "90"))
TRADE_POLL_SWEEP_SECONDS = int(os.getenv("TRADE_POLL_SWEEP_SECONDS", "15"))
_trade_poll_lock = threading.Lock()
_trade_poll_activity: dict[str, dict] = {}
_trade_poll_sweeper_started = False


def _touch_trade_poll(team: str, endpoint: str) -> None:
    """Mark poll activity and emit only lifecycle logs (start/end)."""
    now = time.time()
    team = (team or "").strip().upper()
    if not team:
        return

    with _trade_poll_lock:
        state = _trade_poll_activity.get(team)
        if not state or (now - float(state.get("last_seen_ts", 0))) > TRADE_POLL_IDLE_SECONDS:
            _trade_poll_activity[team] = {
                "started_ts": now,
                "last_seen_ts": now,
                "request_count": 1,
                "first_endpoint": endpoint,
            }
            _log(
                "🔔 TRADE_POLL_LIVE_START",
                {
                    "team": team,
                    "endpoint": endpoint,
                    "idle_timeout_s": TRADE_POLL_IDLE_SECONDS,
                },
            )
            return

        state["last_seen_ts"] = now
        state["request_count"] = int(state.get("request_count", 0)) + 1


def _trade_poll_sweeper_loop() -> None:
    """Emit end notifications after poll inactivity."""
    while True:
        time.sleep(max(5, TRADE_POLL_SWEEP_SECONDS))
        now = time.time()
        ended: list[dict] = []
        with _trade_poll_lock:
            for team, state in list(_trade_poll_activity.items()):
                last_seen = float(state.get("last_seen_ts", 0))
                idle_seconds = now - last_seen
                if idle_seconds <= TRADE_POLL_IDLE_SECONDS:
                    continue
                started = float(state.get("started_ts", last_seen))
                ended.append(
                    {
                        "team": team,
                        "request_count": int(state.get("request_count", 0)),
                        "active_seconds": int(max(0, last_seen - started)),
                        "idle_seconds": int(idle_seconds),
                        "first_endpoint": state.get("first_endpoint"),
                    }
                )
                _trade_poll_activity.pop(team, None)

        for item in ended:
            _log("🛑 TRADE_POLL_LIVE_END", item)


def _start_trade_poll_sweeper_once() -> None:
    global _trade_poll_sweeper_started
    with _trade_poll_lock:
        if _trade_poll_sweeper_started:
            return
        _trade_poll_sweeper_started = True
    thread = threading.Thread(
        target=_trade_poll_sweeper_loop,
        daemon=True,
        name="trade-poll-sweeper",
    )
    thread.start()
    _log(
        "ℹ️ TRADE_POLL_TRACKING_ENABLED",
        {
            "idle_timeout_s": TRADE_POLL_IDLE_SECONDS,
            "sweep_interval_s": TRADE_POLL_SWEEP_SECONDS,
        },
    )


router = APIRouter(prefix="/api/trade", tags=["trade"])

API_KEY = os.getenv("BOT_API_KEY", "")

# Prefer an explicit guild selection in multi-guild deployments.
# This should be your primary FBP server ID.
DEFAULT_DISCORD_GUILD_ID = 875592505926758480

_bot_ref = None
_commit_fn = None

T = TypeVar("T")

_start_trade_poll_sweeper_once()


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
    team = normalize_team_abbr(str(x_manager_team).strip())
    if not team:
        raise HTTPException(status_code=401, detail="Missing X-Manager-Team")
    return team


async def _dm_trade_parties(trade: dict, content: str) -> None:
    """DM all managers involved in a trade with an update message."""
    if _bot_ref is None:
        return

    async def _send() -> None:
        guild = _select_guild()
        if not guild:
            return
        try:
            from commands.trade_logic import dm_trade_parties

            trade_data = {
                "teams": trade.get("teams") or [],
            }
            await dm_trade_parties(guild, trade_data, content)
        except Exception as exc:
            print(f"⚠️ Failed DMing trade parties: {exc}")

    _schedule_on_bot_loop(_send(), "dm_trade_parties")




@router.post("/submit")
async def submit_trade(
    payload: TradeSubmitPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    _log(
        "📥 TRADE_SUBMIT",
        {
            "manager_team": manager_team,
            "teams": payload.teams,
            "transfer_count": len(payload.transfers or []),
            "transfer_types": sorted({getattr(t, "type", "?") for t in (payload.transfers or [])}),
        },
    )

    # Create trade record (validates window + rosters + WB)
    trade = trade_store.create_trade(payload, actor_team=manager_team)

    # Best-effort: DM partner managers (non-blocking if bot is down)
    try:
        if _bot_ref is not None and _bot_ref.is_ready():
            from commands.trade_logic import notify_trade_via_dm

            async def _send_dms():
                guild = _select_guild()
                if not guild:
                    return
                await notify_trade_via_dm(
                    guild,
                    {
                        "trade_id": trade["trade_id"],
                        "teams": trade["teams"],
                        "players": trade.get("receives") or {},
                        "initiator_team": trade.get("initiator_team"),
                        "source": "🌐 Website",
                    },
                )

            _schedule_on_bot_loop(_send_dms(), "notify_trade_via_dm")
            _log(
                "✅ TRADE_SUBMIT_OK",
                {
                    "trade_id": trade.get("trade_id"),
                    "teams": trade.get("teams"),
                },
            )
        else:
            _log("⚠️ TRADE_SUBMIT_NO_DISCORD", {"trade_id": trade.get("trade_id"), "reason": "Bot not connected"})
    except Exception as exc:
        # DM failure is non-fatal — trade is already created
        _log("⚠️ TRADE_SUBMIT_DM_FAILED", {"trade_id": trade.get("trade_id"), "error": str(exc)})

    return {
        "success": True,
        "trade": trade,
    }

@router.get("/queue")
async def get_queue(
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    _touch_trade_poll(manager_team, "queue")
    return {"trades": trade_store.list_queue(manager_team)}


@router.get("/inbox")
async def get_inbox(
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    _touch_trade_poll(manager_team, "inbox")
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
    _log(
        "📥 TRADE_ACCEPT",
        {
            "trade_id": payload.trade_id,
            "team": manager_team,
        },
    )

    try:
        trade, all_accepted = trade_store.accept_trade(payload.trade_id, manager_team)
    except HTTPException as exc:
        if exc.status_code == 409:
            _log(
                "⚠️ TRADE_ACCEPT_CONFLICT",
                {
                    "trade_id": payload.trade_id,
                    "team": manager_team,
                },
            )
            # Trade was auto-withdrawn due to a conflicting trade; best-effort notify the thread.
            try:
                trade = trade_store.get_trade(payload.trade_id)
                details = trade.get("withdraw_details") or []
                detail_lines = "\n".join(f"• {d}" for d in details[:6])
                msg = "⚠️ **Trade auto-withdrawn**: conflicting trade (assets no longer owned)."
                if detail_lines:
                    msg += "\n" + detail_lines
                await _dm_trade_parties(trade, msg)
            except Exception:
                pass
            raise

        _log(
            "❌ TRADE_ACCEPT_FAILED",
            {
                "trade_id": payload.trade_id,
                "team": manager_team,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )
        raise
    # DM all trade parties about the acceptance
    await _dm_trade_parties(trade, f"✅ **{manager_team}** accepted the trade via website")

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

    _log(
        "✅ TRADE_ACCEPT_OK",
        {
            "trade_id": payload.trade_id,
            "team": manager_team,
            "all_accepted": bool(all_accepted),
            "status": trade.get("status"),
        },
    )

    return {"success": True, "status": trade.get("status"), "all_accepted": all_accepted}


@router.post("/reject")
async def reject_trade(
    payload: TradeRejectPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    trade = trade_store.reject_trade(payload.trade_id, manager_team, payload.reason)
    await _dm_trade_parties(trade, f"❌ **{manager_team}** rejected the trade: {payload.reason}")
    return {"success": True, "status": trade.get("status")}


@router.post("/withdraw")
async def withdraw_trade(
    payload: TradeWithdrawPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    trade = trade_store.withdraw_trade(payload.trade_id, manager_team)
    await _dm_trade_parties(trade, f"🗑️ **{manager_team}** withdrew this trade")
    return {"success": True, "status": trade.get("status")}


@router.get("/history")
async def get_history(
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    return {"trades": trade_store.list_history(manager_team)}


# ---- Admin trade endpoints (website admin portal) ----

class TradeAdminApprovePayload(BaseModel):
    trade_id: str


class TradeAdminRejectPayload(BaseModel):
    trade_id: str
    reason: str


@router.post("/admin/approve")
async def admin_approve_trade(
    payload: TradeAdminApprovePayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    _log(
        "📥 TRADE_ADMIN_APPROVE",
        {"trade_id": payload.trade_id, "admin": manager_team},
    )

    try:
        trade = trade_store.admin_approve(payload.trade_id, manager_team)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Best-effort: post to #trades channel if bot is connected
    try:
        if _bot_ref is not None and _bot_ref.is_ready():
            from commands.trade_logic import post_approved_trade

            async def _post():
                guild = _select_guild()
                if guild:
                    await post_approved_trade(
                        guild,
                        {
                            "trade_id": trade.get("trade_id"),
                            "teams": trade.get("teams") or [],
                            "players": trade.get("receives") or {},
                            "initiator_team": trade.get("initiator_team"),
                            "source": "🌐 Admin Portal",
                        },
                    )

            _schedule_on_bot_loop(_post(), "admin_approve_post_trade")
    except Exception:
        pass

    _log(
        "✅ TRADE_ADMIN_APPROVE_OK",
        {"trade_id": payload.trade_id, "admin": manager_team, "status": trade.get("status")},
    )
    return {"success": True, "trade": trade}


@router.post("/admin/reject")
async def admin_reject_trade(
    payload: TradeAdminRejectPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    _log(
        "📥 TRADE_ADMIN_REJECT",
        {"trade_id": payload.trade_id, "admin": manager_team, "reason": payload.reason},
    )

    try:
        trade = trade_store.admin_reject(payload.trade_id, manager_team, payload.reason)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _log(
        "✅ TRADE_ADMIN_REJECT_OK",
        {"trade_id": payload.trade_id, "admin": manager_team},
    )
    return {"success": True, "trade": trade}
