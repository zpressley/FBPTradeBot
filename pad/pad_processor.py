"""PAD (Prospect Allocation Day) processing utilities.

This module will be responsible for applying PAD submissions coming from the
website to the bot's data files (combined_players, wizbucks, player_log,
prospect draft order, etc.). It is designed so that we can run the exact same
logic in both live mode and test mode.

Current state: skeleton implementation with data-path helpers and models. The
full application logic will be filled in as we implement the plan.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel
from zoneinfo import ZoneInfo

ET = ZoneInfo("US/Eastern")

# Path to managers config used for final_rank_2025 (prospect draft order)
# In production (Render), the bot repo is deployed alone, so we keep a
# copy of managers.json under this repo's config/ directory.
MANAGERS_CONFIG_PATH = Path("config/managers.json").resolve()


# ---------------------------------------------------------------------------
# Data path helpers
# ---------------------------------------------------------------------------


def _suffix(test_mode: bool) -> str:
    """Return "_test" suffix when PAD_TEST_MODE is enabled."""

    return "_test" if test_mode else ""


def get_combined_players_path(test_mode: bool) -> str:
    return f"data/combined_players{_suffix(test_mode)}.json"


def get_wizbucks_path(test_mode: bool) -> str:
    return f"data/wizbucks{_suffix(test_mode)}.json"


def get_player_log_path(test_mode: bool) -> str:
    return f"data/player_log{_suffix(test_mode)}.json"


def get_draft_order_path(test_mode: bool) -> str:
    return f"data/draft_order_2026{_suffix(test_mode)}.json"


def load_managers_config() -> Dict[str, Any]:
    """Load managers.json from the hub repo for final_rank_2025.

    This assumes the bot repo and hub repo live as siblings on disk. If the
    file cannot be found, we return an empty dict and draft-order rebuild
    becomes a no-op.
    """

    try:
        with MANAGERS_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ managers.json not found at {MANAGERS_CONFIG_PATH}; skipping draft order rebuild")
        return {}


def get_pad_submissions_path(test_mode: bool) -> str:
    return f"data/pad_submissions_2026{_suffix(test_mode)}.json"


def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PadPlayerRef(BaseModel):
    """Lightweight reference to a player from the PAD UI."""

    upid: str | None = None
    name: str


class PadSubmissionPayload(BaseModel):
    """Payload sent from the PAD web UI through the Worker to FastAPI.

    Note: FastAPI will also use this model for request validation.
    """

    season: int
    team: str
    dc_players: List[PadPlayerRef] = []
    pc_players: List[PadPlayerRef] = []
    bc_players: List[PadPlayerRef] = []
    dc_slots: int = 0
    bc_slots: int = 0
    total_spend: int
    total_available: int


@dataclass
class PadResult:
    """Summary of an applied PAD submission.

    This is returned to the API layer and used to construct Discord
    announcements and UI confirmations.
    """

    season: int
    team: str
    timestamp: str
    wb_spent: int
    dc_players: List[Dict[str, Any]]
    pc_players: List[Dict[str, Any]]
    bc_players: List[Dict[str, Any]]
    dc_slots: int
    bc_slots: int


class PadAlreadySubmittedError(Exception):
    """Raised when a team attempts to submit PAD more than once."""


# ---------------------------------------------------------------------------
# Player-log snapshot helper
# ---------------------------------------------------------------------------


def _ensure_list(obj: Any) -> list:
    if obj is None:
        return []
    return obj


def _append_player_log_entry(
    player_log: list,
    player_rec: dict,
    season: int,
    source: str,
    update_type: str,
    event: str,
    admin: str,
) -> None:
    """Append a snapshot entry to player_log using combined_players schema.

    We mirror the structure created by backfill_2025_rosters_to_player_log:
    copy all relevant fields from `player_rec` and only change
    update_type/event/source/admin/timestamp/season/id.
    """

    upid = str(player_rec.get("upid") or "").strip()
    ts = datetime.now(tz=ET).isoformat()
    id_parts = [str(season), ts, f"UPID_{upid or 'NA'}", update_type, source]
    entry_id = "-".join(id_parts)

    entry = {
        "id": entry_id,
        "season": season,
        "source": source,
        "admin": admin,
        "timestamp": ts,
        "upid": upid,
        "player_name": player_rec.get("name") or "",
        "team": player_rec.get("team") or "",
        "pos": player_rec.get("position") or "",
        "age": player_rec.get("age"),
        "level": str(player_rec.get("level") or ""),
        "team_rank": player_rec.get("team_rank"),
        "rank": player_rec.get("rank"),
        "eta": str(player_rec.get("eta") or ""),
        "player_type": player_rec.get("player_type") or "",
        "owner": player_rec.get("manager") or player_rec.get("owner") or "",
        "contract": player_rec.get("contract_type") or player_rec.get("contract") or "",
        "status": player_rec.get("status") or "",
        "years": player_rec.get("years_simple") or player_rec.get("years") or "",
        "update_type": update_type,
        "event": event,
    }

    player_log.append(entry)


# ---------------------------------------------------------------------------
# Draft order rebuild
# ---------------------------------------------------------------------------


def rebuild_draft_order_from_pad(submissions: Dict[str, Any], test_mode: bool) -> None:
    """Rebuild data/draft_order_2026*.json from final_rank_2025 + PAD slots.

    This is an intentionally simple implementation to get us live: BC/DC
    slots determine the number of picks per team in BC rounds (1–2) and DC
    rounds (3+), but we do not yet attempt to map slots to precise pick
    numbers beyond preserving team order by reverse 2025 standings.
    """

    cfg = load_managers_config()
    teams_cfg = cfg.get("teams") or {}
    if not teams_cfg:
        print("⚠️ PAD: managers.json missing or empty; draft order rebuild skipped")
        return

    # Build base order by reverse final_rank_2025 (worst rank -> earliest pick)
    ordered_teams: List[Tuple[int, str]] = []
    for abbr, meta in teams_cfg.items():
        rank = meta.get("final_rank_2025")
        if isinstance(rank, int):
            ordered_teams.append((rank, abbr))
    if not ordered_teams:
        return

    # Sort by rank descending so worst rank (12) comes first.
    ordered_teams.sort(key=lambda x: x[0], reverse=True)
    team_order = [abbr for _, abbr in ordered_teams]

    # Aggregate PAD slots.
    dc_slots_by_team: Dict[str, int] = {}
    bc_slots_by_team: Dict[str, int] = {}
    for team, rec in submissions.items():
        dc_slots_by_team[team] = int(rec.get("dc_slots", 0))
        bc_slots_by_team[team] = int(rec.get("bc_slots", 0))

    # Draft structure: BC rounds (1–2) + 15 DC rounds (3–17).
    rounds: List[Dict[str, Any]] = []

    # Rounds 1–2: BC
    for rnd in (1, 2):
        for idx, team in enumerate(team_order, start=1):
            slots = bc_slots_by_team.get(team, 0)
            # One pick per BC slot in this round.
            for s in range(slots):
                rounds.append({
                    "round": rnd,
                    "pick": len([r for r in rounds if r["round"] == rnd]) + 1,
                    "team": team,
                    "round_type": "fypd",
                })

    # Rounds 3–17: DC (15 rounds)
    for offset in range(15):
        rnd = 3 + offset
        for idx, team in enumerate(team_order, start=1):
            slots = dc_slots_by_team.get(team, 0)
            # Distribute DC slots one per round until exhausted.
            # For example, 3 DC slots -> extra picks in rounds 3,4,5.
            if offset < slots:
                rounds.append({
                    "round": rnd,
                    "pick": len([r for r in rounds if r["round"] == rnd]) + 1,
                    "team": team,
                    "round_type": "dc",
                })

    if not rounds:
        # No PAD slots purchased yet; leave existing file intact.
        return

    draft_order_path = get_draft_order_path(test_mode)
    _save_json(draft_order_path, rounds)


# ---------------------------------------------------------------------------
# Core entry point
# ---------------------------------------------------------------------------


def apply_pad_submission(payload: PadSubmissionPayload, test_mode: bool) -> PadResult:
    """Apply a single PAD submission to the appropriate data files.

    Responsibilities:
      * Enforce one-submission-per-team rule
      * Update combined_players (DC/PC/BC contracts)
      * Debit WizBucks according to PAD spend
      * Append snapshot player_log entries (Purchase / Blue Chip / Drop)
      * Rebuild draft_order_2026 based on all submissions (TODO)
    """

    submissions_path = get_pad_submissions_path(test_mode)
    submissions: Dict[str, Any] = _load_json(submissions_path) or {}

    team = payload.team
    season = payload.season

    # In live mode we strictly enforce one submission per team. In test
    # mode we allow repeated submissions so commissioners can iterate on
    # PAD behavior without having to manually edit JSON on the server.
    if team in submissions and not test_mode:
        raise PadAlreadySubmittedError(f"PAD already submitted for team {team}")

    # Load main data files
    combined_path = get_combined_players_path(test_mode)
    wizbucks_path = get_wizbucks_path(test_mode)
    player_log_path = get_player_log_path(test_mode)

    combined_players: list = _ensure_list(_load_json(combined_path))
    wizbucks: Dict[str, int] = _load_json(wizbucks_path) or {}
    player_log: list = _ensure_list(_load_json(player_log_path))

    # Build quick index for name lookup (case-insensitive) scoped by team.
    by_name: Dict[Tuple[str, str], dict] = {}
    for p in combined_players:
        name = str(p.get("name") or "").strip().lower()
        mlb_team = str(p.get("team") or "").strip()
        if not name:
            continue
        by_name[(name, mlb_team)] = p

    def _find_player(pref: PadPlayerRef) -> dict | None:
        """Best-effort lookup by UPID, then name+team (if available)."""
        upid = str(pref.upid or "").strip()
        if upid:
            for p in combined_players:
                if str(p.get("upid") or "").strip() == upid:
                    return p
        # Name-only fallback: we do not have MLB team from PAD UI yet, so
        # we match by name only. This assumes names are unique within
        # the prospect pool, which holds for our current league data.
        name_lower = pref.name.strip().lower()
        if not name_lower:
            return None
        matches = [p for p in combined_players if str(p.get("name") or "").strip().lower() == name_lower]
        if not matches:
            print(f"⚠️ PAD: could not find player for submission ref: name='{pref.name}', upid='{pref.upid}'")
            return None
        if len(matches) > 1:
            print(
                "⚠️ PAD: multiple players matched by name; using first:",
                {"name": pref.name, "upid": pref.upid, "count": len(matches)},
            )
        return matches[0]

    # Helper to normalize status string so trailing code is 'P' while
    # preserving any numeric prefix "[7]" etc.
    def _ensure_prospect_status(status: str | None) -> str:
        s = (status or "").strip()
        if not s:
            return "P"
        # If status already looks like "[n] X", replace trailing code with P.
        if s.startswith("[") and "]" in s:
            idx = s.find("]")
            prefix = s[: idx + 1]
            return f"{prefix} P"
        return "P"

    # Determine WB starting balance for this team (franchise name from managers.json
    # is used as key in wizbucks.json). For now we assume team name key exists; if
    # missing we treat balance as 0 and still log deltas.
    # NOTE: PAD UI already guards against overspend; backend will trust values
    # here and just apply deltas.

    # Map from FBP team abbreviation to franchise display name used as
    # the key in wizbucks.json. Prefer managers.json when available,
    # and fall back to a case-insensitive scan of wizbucks keys.
    managers_cfg = load_managers_config() or {}
    teams_meta = managers_cfg.get("teams") or {}

    def _resolve_franchise_name(team_abbr: str) -> str:
        meta = teams_meta.get(team_abbr)
        if meta and isinstance(meta, dict):
            name = meta.get("name")
            if name and name in wizbucks:
                return name
        # Fallback: case-insensitive search through wizbucks keys
        upper_abbr = team_abbr.upper()
        for name in wizbucks.keys():
            if upper_abbr in name.upper() or name.upper().startswith(upper_abbr):
                return name
        return team_abbr

    franchise_name = _resolve_franchise_name(team)
    if franchise_name not in wizbucks:
        print(
            "⚠️ PAD: franchise_name not found in wizbucks; defaulting balance to 0",
            {"team": team, "franchise_name": franchise_name, "keys": list(wizbucks.keys())},
        )
    wb_balance = int(wizbucks.get(franchise_name, 0))

    # Apply contract changes and track affected players for summary & logging.
    dc_players: List[Dict[str, Any]] = []
    pc_players: List[Dict[str, Any]] = []
    bc_players: List[Dict[str, Any]] = []

    def _apply_contract(pref: PadPlayerRef, label: str) -> None:
        p = _find_player(pref)
        if not p:
            # _find_player already logged a warning.
            return
        # Only touch prospects
        if (p.get("player_type") or "").strip() != "Farm":
            print(
                "⚠️ PAD: skipping non-prospect in submission",
                {"name": p.get("name"), "upid": p.get("upid"), "player_type": p.get("player_type")},
            )
            return
        p["manager"] = franchise_name
        p["FBP_Team"] = team
        if label == "DC":
            p["contract_type"] = "Development Contract"
            p["years_simple"] = "DC"
        elif label == "PC":
            p["contract_type"] = "Purchased Contract"
            p["years_simple"] = "PC"
        elif label == "BC":
            p["contract_type"] = "Blue Chip Contract"
            p["years_simple"] = "BC"
        p["status"] = _ensure_prospect_status(str(p.get("status") or ""))

        snapshot = {
            "upid": p.get("upid"),
            "name": p.get("name"),
            "team": p.get("team"),
        }
        if label == "DC":
            dc_players.append(snapshot)
            _append_player_log_entry(
                player_log,
                p,
                season=season,
                source="2026_PAD",
                update_type="Purchase",
                event="26 PAD",
                admin="pad_submission",
            )
        elif label == "PC":
            pc_players.append(snapshot)
            _append_player_log_entry(
                player_log,
                p,
                season=season,
                source="2026_PAD",
                update_type="Purchase",
                event="26 PAD",
                admin="pad_submission",
            )
        elif label == "BC":
            bc_players.append(snapshot)
            _append_player_log_entry(
                player_log,
                p,
                season=season,
                source="2026_PAD",
                update_type="Blue Chip",
                event="26 PAD",
                admin="pad_submission",
            )

    for pref in payload.dc_players:
        _apply_contract(pref, "DC")
    for pref in payload.pc_players:
        _apply_contract(pref, "PC")
    for pref in payload.bc_players:
        _apply_contract(pref, "BC")

    # Apply WB spending as a single delta. PAD UI computed total_spend; we
    # treat it as authoritative and subtract it from the manager's balance.
    wb_spent = int(payload.total_spend)
    wizbucks[franchise_name] = wb_balance - wb_spent

    # Record submission metadata (slots + WB) for future draft-order rebuild.
    now = datetime.now(tz=ET).isoformat()
    submissions = submissions or {}
    submissions[team] = {
        "season": season,
        "team": team,
        "timestamp": now,
        "dc_slots": payload.dc_slots,
        "bc_slots": payload.bc_slots,
        "wb_total_spend": wb_spent,
    }

    _save_json(submissions_path, submissions)
    _save_json(combined_path, combined_players)
    _save_json(wizbucks_path, wizbucks)
    _save_json(player_log_path, player_log)

    # Rebuild draft_order_2026 (or its _test variant) from all PAD
    # submissions and 2025 final standings.
    rebuild_draft_order_from_pad(submissions, test_mode)

    return PadResult(
        season=season,
        team=team,
        timestamp=now,
        wb_spent=wb_spent,
        dc_players=dc_players,
        pc_players=pc_players,
        bc_players=bc_players,
        dc_slots=payload.dc_slots,
        bc_slots=payload.bc_slots,
    )


# ---------------------------------------------------------------------------
# Discord announcement hook (to be called from FastAPI layer)
# ---------------------------------------------------------------------------

async def announce_pad_submission_to_discord(result: PadResult, bot) -> None:
    """Send a PAD submission summary embed to the appropriate Discord channel.

    Channel selection is controlled via environment variables and PAD_TEST_MODE
    (imported via health.py). To avoid circular imports, we read the channel
    IDs from environment here as well.
    """

    import os  # local import to avoid side effects at module import time

    test_mode = os.getenv("PAD_TEST_MODE", "false").lower() == "true"
    test_channel_id = int(os.getenv("PAD_TEST_CHANNEL_ID", "0"))
    live_channel_id = int(os.getenv("PAD_LIVE_PAD_CHANNEL_ID", "0"))
    channel_id = test_channel_id if test_mode else live_channel_id

    print(
        "🔔 PAD Discord announce debug",
        {
            "test_mode": test_mode,
            "test_channel_id": test_channel_id,
            "live_channel_id": live_channel_id,
            "chosen_channel_id": channel_id,
        },
    )

    if not channel_id:
        print("⚠️ PAD announce: no channel id configured; skipping Discord message")
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        print("⚠️ PAD announce: bot.get_channel returned None; check channel ID and bot guilds")
        return

    import discord

    title = f"{result.team} – PAD Submission ({result.season})"

    embed = discord.Embed(
        title=title,
        color=discord.Color.blue(),
    )

    if result.bc_players:
        lines = [f"- {p.get('name')}" for p in result.bc_players]
        embed.add_field(
            name="Blue Chip Prospects",
            value="\n".join(lines),
            inline=False,
        )

    if result.pc_players:
        lines = [f"- {p.get('name')}" for p in result.pc_players]
        embed.add_field(
            name="Purchased Players (PC)",
            value="\n".join(lines),
            inline=False,
        )

    if result.dc_players:
        lines = [f"- {p.get('name')}" for p in result.dc_players]
        embed.add_field(
            name="Development Contracts (DC)",
            value="\n".join(lines),
            inline=False,
        )

    # Slots summary
    slots_lines = []
    if result.bc_slots:
        slots_lines.append(f"BC Slots: {result.bc_slots}")
    if result.dc_slots:
        slots_lines.append(f"DC Slots: {result.dc_slots}")
    if slots_lines:
        embed.add_field(
            name="Round Buy-Ins",
            value="\n".join(slots_lines),
            inline=False,
        )

    embed.set_footer(text=f"WB Spent: ${result.wb_spent} | Submitted at {result.timestamp}")

    try:
        await channel.send(embed=embed)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"⚠️ Failed to send PAD announcement: {exc}")
