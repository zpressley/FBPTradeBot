from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


class TradeTransferPlayer(BaseModel):
    type: Literal["player"] = "player"
    upid: str
    from_team: str
    to_team: str


class TradeTransferWizbucks(BaseModel):
    type: Literal["wizbucks"] = "wizbucks"
    amount: int
    from_team: str
    to_team: str


TradeTransfer = Union[TradeTransferPlayer, TradeTransferWizbucks]


class TradeSubmitPayload(BaseModel):
    teams: List[str] = Field(..., min_length=2, max_length=3)
    transfers: List[TradeTransfer] = Field(..., min_length=1)


class TradeAcceptPayload(BaseModel):
    trade_id: str


class TradeRejectPayload(BaseModel):
    trade_id: str
    reason: str = Field(..., min_length=1, max_length=300)


class TradeWithdrawPayload(BaseModel):
    trade_id: str


class TradeSummary(BaseModel):
    trade_id: str
    teams: List[str]
    initiator_team: str
    status: str
    created_at: str
    expires_at: str
    acceptances: List[str]
    discord_thread_url: Optional[str] = None
    # Pre-formatted per-team "receives" blocks (constitution/Discord-style)
    receives: dict[str, list[str]]


class TradeDetail(TradeSummary):
    transfers: List[TradeTransfer]
    rejection_reason: Optional[str] = None
    rejected_by: Optional[str] = None
    admin_decision_by: Optional[str] = None
    processed_at: Optional[str] = None


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
