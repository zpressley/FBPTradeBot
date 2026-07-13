"""Receive client-side (browser) JS errors from fbp-hub and print them to
the bot's stdout, so they show up in Railway's log stream alongside backend
errors instead of only being visible in a manager's own browser console.

Added July 2026 specifically because backend errors were being silently
swallowed (see the bare `except:` cleanup elsewhere in this repo) and there
was *no* visibility at all into frontend JS errors — managers would just see
something break with no way for Zach to know it happened, let alone why.

This intentionally does NOT require a fully-authenticated manager session:
errors that happen before/during login (e.g. auth.js failures) are exactly
the kind of thing we want to see, so this only checks the same BOT_API_KEY
every other endpoint uses, and even then prefers to log-and-continue over
silently dropping a report.
"""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/log", tags=["client-log"])

API_KEY = os.getenv("BOT_API_KEY", "")

# Simple in-memory throttle so a browser stuck in an error loop (e.g. a JS
# exception thrown on every animation frame) can't flood Railway's log
# volume. Keyed on (page, message-prefix); resets naturally as old hits
# age out of the rolling window. This is per-process/in-memory only —
# fine for a single Railway instance, and worst case under-throttles
# slightly after a redeploy, which is an acceptable trade-off.
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_PER_WINDOW = 20
_recent_hits: Dict[Tuple[str, str], Deque[float]] = {}


def _rate_limited(key: Tuple[str, str]) -> bool:
    now = time.time()
    hits = _recent_hits.setdefault(key, deque())
    while hits and now - hits[0] > _RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= _RATE_LIMIT_MAX_PER_WINDOW:
        return True
    hits.append(now)
    return False


class ClientErrorPayload(BaseModel):
    message: str = Field(..., max_length=2000)
    source: Optional[str] = Field(None, max_length=500)  # page URL
    lineno: Optional[int] = None
    colno: Optional[int] = None
    stack: Optional[str] = Field(None, max_length=4000)
    kind: Optional[str] = Field(None, max_length=50)  # onerror | unhandledrejection | console.error
    userAgent: Optional[str] = Field(None, max_length=300)
    team: Optional[str] = Field(None, max_length=20)
    timestamp: Optional[str] = Field(None, max_length=50)


@router.post("/client-error")
async def log_client_error(
    payload: ClientErrorPayload,
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    # Soft auth check: if BOT_API_KEY is configured, require it — but a
    # missing/bad key still gets a printed line (just marked rejected)
    # rather than being dropped with zero trace, since the whole point of
    # this endpoint is visibility.
    if API_KEY and x_api_key != API_KEY:
        client_host = request.client.host if request.client else "?"
        print(f"⚠️ /api/log/client-error: rejected bad/missing X-API-Key from {client_host}")
        return {"logged": False, "reason": "unauthorized"}

    key = (payload.source or "?", payload.message[:200])
    if _rate_limited(key):
        return {"logged": False, "reason": "rate_limited"}

    lines = [
        "🖥️  CLIENT ERROR "
        f"[{payload.kind or 'unknown'}] "
        f"team={payload.team or '-'} "
        f"page={payload.source or '-'} "
        f"line={payload.lineno if payload.lineno is not None else '-'}"
        f":{payload.colno if payload.colno is not None else '-'}",
        f"    message: {payload.message}",
    ]
    if payload.stack:
        lines.append(f"    stack: {payload.stack}")
    if payload.userAgent:
        lines.append(f"    ua: {payload.userAgent}")
    if payload.timestamp:
        lines.append(f"    client_time: {payload.timestamp}")

    print("\n".join(lines))

    return {"logged": True}
