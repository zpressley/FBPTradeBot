"""
In-Season Yahoo Roster Sync Processor

Replaces merge_players.py for the in-season daily pipeline. Instead of
blindly overwriting ownership from Yahoo, this diffs Yahoo rosters against
combined_players.json and only applies targeted, audited changes.

Modes:
  CLI (standalone):  Writes combined_players.json, player_log.json,
                     and queues Discord messages to data/roster_sync_messages.json.
  Importable:        health.py can call run_sync() and post messages in real-time.
"""

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Ensure project root importable
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pad.pad_processor import _append_player_log_entry, _load_json, _save_json

ET = ZoneInfo("US/Eastern")
SEASON = 2026

# Paths
YAHOO_FILE = "data/yahoo_players.json"
COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"
MANAGERS_FILE = "config/managers.json"
MESSAGES_FILE = "data/roster_sync_messages.json"
STATE_FILE = "data/roster_sync_state.json"
UPID_DB_FILE = "data/upid_database.json"
TRADES_FILE = "data/trades.json"

HUB_BASE = "https://www.pantheonleagues.com"
TRADE_GUARD_DAYS = 14


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hub_link(player_name: str, upid: str) -> str:
    """Return a markdown link to the player's FBP Hub profile."""
    if upid:
        return f"[{player_name}]({HUB_BASE}/player-profile.html?upid={upid})"
    return player_name


def _load_managers() -> Dict[str, Dict]:
    """Load config/managers.json and return the teams dict."""
    cfg = _load_json(MANAGERS_FILE) or {}
    return cfg.get("teams", {})


def _team_labels(teams_cfg: Dict, fbp_abbr: str) -> Tuple[str, str]:
    """Return (FBP abbreviation, full manager/franchise name)."""
    meta = teams_cfg.get(fbp_abbr, {})
    full_name = (meta.get("name") or fbp_abbr).strip()
    return fbp_abbr, full_name


# ---------------------------------------------------------------------------
# Player matching
# ---------------------------------------------------------------------------

def _build_combined_indexes(combined: List[Dict]) -> Dict[str, Dict]:
    """Build lookup indexes from combined_players for fast matching.

    Returns a dict with keys:
      "by_upid"     -> {upid: player_rec}
      "by_yahoo_id" -> {yahoo_id: player_rec}
      "by_mlb_id"   -> {str(mlb_id): player_rec}
      "by_name"     -> {lowercase_name: [player_rec, ...]}
    """
    by_upid: Dict[str, Dict] = {}
    by_yahoo_id: Dict[str, Dict] = {}
    by_mlb_id: Dict[str, Dict] = {}
    by_name: Dict[str, List[Dict]] = {}

    for p in combined:
        upid = str(p.get("upid") or "").strip()
        yahoo_id = str(p.get("yahoo_id") or "").strip()
        mlb_id = str(p.get("mlb_id") or "").strip()
        name = (p.get("name") or "").strip().lower()

        if upid:
            by_upid[upid] = p
        if yahoo_id:
            by_yahoo_id[yahoo_id] = p
        if mlb_id and mlb_id != "None":
            by_mlb_id[mlb_id] = p
        if name:
            by_name.setdefault(name, []).append(p)

    return {
        "by_upid": by_upid,
        "by_yahoo_id": by_yahoo_id,
        "by_mlb_id": by_mlb_id,
        "by_name": by_name,
    }


def _load_upid_name_index() -> Dict[str, List[str]]:
    """Load the UPID database name_index (lowercase name -> [upid, ...]).

    This index includes alt-name variants (e.g. 'aj minter' -> ['1994'])
    so we can match Yahoo names that differ from combined_players names.
    """
    db = _load_json(UPID_DB_FILE) or {}
    return db.get("name_index", {})


def _load_upid_by_upid() -> Dict[str, Dict]:
    """Load UPID metadata keyed by UPID."""
    db = _load_json(UPID_DB_FILE) or {}
    by_upid = db.get("by_upid")
    return by_upid if isinstance(by_upid, dict) else {}


def _load_trade_owner_lock_map(now: Optional[datetime] = None) -> Dict[str, str]:
    """Return a recent approved-trade ownership map: upid -> owner team."""
    now = now or datetime.now(tz=ET)

    trades_obj = _load_json(TRADES_FILE) or {}
    if isinstance(trades_obj, dict):
        trades = list(trades_obj.values())
    elif isinstance(trades_obj, list):
        trades = trades_obj
    else:
        trades = []

    records: List[Tuple[datetime, str, str]] = []
    for rec in trades:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("status") or "").strip().lower() != "approved":
            continue

        ts_raw = rec.get("processed_at") or rec.get("manager_approved_at") or rec.get("created_at")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue

        age_days = (now - ts).total_seconds() / 86400.0
        if age_days < 0 or age_days > TRADE_GUARD_DAYS:
            continue

        for t in rec.get("transfers") or []:
            if not isinstance(t, dict):
                continue
            if t.get("type") != "player":
                continue
            upid = str(t.get("upid") or "").strip()
            to_team = str(t.get("to_team") or "").strip().upper()
            if upid and to_team:
                records.append((ts, upid, to_team))

    records.sort(key=lambda x: x[0])  # latest transfer wins
    out: Dict[str, str] = {}
    for _, upid, to_team in records:
        out[upid] = to_team
    return out


def _match_yahoo_player(
    yahoo_player: Dict,
    idx: Dict[str, Dict],
    upid_name_index: Dict[str, List[str]],
) -> Optional[Dict]:
    """Match a Yahoo roster entry to a combined_players record.

    Priority:
      1. Yahoo ID
      2. UPID database name_index (handles alt-name variants)
      3. Exact name match in combined_players
      4. Name + MLB team disambiguation
    Yahoo players carry: name, position, team, yahoo_id.
    """
    yahoo_id = str(yahoo_player.get("yahoo_id") or "").strip()
    name = (yahoo_player.get("name") or "").strip()
    name_lower = name.lower()

    # 1. Yahoo ID (most reliable for Yahoo-sourced data)
    if yahoo_id and yahoo_id in idx["by_yahoo_id"]:
        return idx["by_yahoo_id"][yahoo_id]

    # 2. UPID database name_index (includes alt-name variants)
    upid_hits = upid_name_index.get(name_lower, [])
    # Deduplicate (the index can have repeated UPIDs)
    unique_upids = list(dict.fromkeys(upid_hits))
    if len(unique_upids) == 1 and unique_upids[0] in idx["by_upid"]:
        return idx["by_upid"][unique_upids[0]]
    if len(unique_upids) > 1:
        # Multiple UPIDs for same name — disambiguate by MLB team
        yahoo_team = (yahoo_player.get("team") or "").strip().upper()
        for uid in unique_upids:
            rec = idx["by_upid"].get(uid)
            if rec and (rec.get("team") or "").strip().upper() == yahoo_team:
                return rec

    # 3. Exact name match in combined_players (single result)
    candidates = idx["by_name"].get(name_lower, [])
    if len(candidates) == 1:
        return candidates[0]

    # 4. If multiple name matches, try to disambiguate by MLB team
    if len(candidates) > 1:
        yahoo_team = (yahoo_player.get("team") or "").strip().upper()
        for c in candidates:
            c_team = (c.get("team") or "").strip().upper()
            if c_team == yahoo_team:
                return c

    return None


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------

class RosterSyncResult:
    """Collects all changes and messages from a sync run."""

    def __init__(self):
        self.mlb_adds: List[Dict] = []       # {player, team, ...}
        self.mlb_drops: List[Dict] = []
        self.prospect_alerts: List[Dict] = []  # immediate alerts
        self.call_ups: List[Dict] = []         # batched at 9 AM
        self.send_downs: List[Dict] = []       # batched at 9 AM
        self.unmatched: List[Dict] = []        # Yahoo players we couldn't match
        self.trade_guard_skips: List[Dict] = []  # Yahoo changes suppressed by recent approved trades
        self.mutated_files: List[str] = []


def run_sync(
    dry_run: bool = False,
) -> RosterSyncResult:
    """Run the roster diff and apply changes.

    Args:
        dry_run: If True, compute diffs but don't write any files.

    Returns:
        RosterSyncResult with all changes and queued messages.
    """
    result = RosterSyncResult()

    yahoo_data = _load_json(YAHOO_FILE) or {}
    combined = _load_json(COMBINED_FILE) or []
    player_log = _load_json(PLAYER_LOG_FILE) or []
    teams_cfg = _load_managers()

    if not yahoo_data:
        print("⚠️ roster_sync: yahoo_players.json is empty — skipping sync")
        return result

    if not combined:
        print("⚠️ roster_sync: combined_players.json is empty — skipping sync")
        return result

    idx = _build_combined_indexes(combined)
    upid_name_index = _load_upid_name_index()
    upid_by_upid = _load_upid_by_upid()
    trade_owner_lock = _load_trade_owner_lock_map()

    # Build current ownership map
    current_mlb_roster: Dict[str, str] = {}  # upid -> fbp_team
    for p in combined:
        if (p.get("player_type") or "") != "MLB":
            continue
        manager = (p.get("manager") or "").strip()
        fbp = (p.get("FBP_Team") or "").strip()
        upid = str(p.get("upid") or "").strip()
        if manager and fbp and upid:
            current_mlb_roster[upid] = fbp

    # Build current Farm roster map: upid -> fbp_team for owned Farm players
    current_farm_roster: Dict[str, str] = {}
    for p in combined:
        if (p.get("player_type") or "") != "Farm":
            continue
        manager = (p.get("manager") or "").strip()
        fbp = (p.get("FBP_Team") or "").strip()
        upid = str(p.get("upid") or "").strip()
        if manager and fbp and upid:
            current_farm_roster[upid] = fbp

    # Track which combined-players UPIDs and yahoo_ids appear on Yahoo
    # rosters (per team). We need both because duplicate records may have
    # a UPID on one record and a yahoo_id on another (e.g. "Bobby Witt"
    # vs "Bobby Witt Jr.").
    yahoo_rostered_upids: Dict[str, set] = {t: set() for t in teams_cfg}
    yahoo_rostered_yids: Dict[str, set] = {t: set() for t in teams_cfg}

    # Load previous Yahoo Farm roster state (for send-down detection).
    # If no state file exists, this is the first run and we won't flag
    # send-downs.
    prev_state = _load_json(STATE_FILE) or {}
    prev_yahoo_farm: Dict[str, set] = {
        team: set(upids) for team, upids in prev_state.get("yahoo_farm_upids", {}).items()
    }
    is_first_run = not bool(prev_state)

    # ---------------------------------------------------------------
    # Phase 1: Walk Yahoo rosters and detect ADDS + prospect issues
    # ---------------------------------------------------------------
    for fbp_team, roster in yahoo_data.items():
        if fbp_team not in teams_cfg:
            print(f"⚠️ roster_sync: unknown FBP team '{fbp_team}' in yahoo data — skipping")
            continue

        abbr, full_name = _team_labels(teams_cfg, fbp_team)

        for yp in roster:
            rec = _match_yahoo_player(yp, idx, upid_name_index)

            if rec is None:
                # Try UPID-backed auto-create for Yahoo MLB players that are
                # not yet present in combined_players.
                name = (yp.get("name") or "").strip()
                upid_hits = upid_name_index.get(name.lower(), [])
                unique_upids = list(dict.fromkeys(upid_hits))
                created = None

                if len(unique_upids) == 1:
                    upid_from_name = unique_upids[0]
                    rec_existing = idx["by_upid"].get(upid_from_name)
                    if rec_existing is not None:
                        rec = rec_existing
                    else:
                        meta = upid_by_upid.get(upid_from_name, {})
                        created = {
                            "upid": upid_from_name,
                            "name": name or meta.get("name") or "Unknown",
                            "team": (yp.get("team") or meta.get("team") or "").strip(),
                            "position": (yp.get("position") or meta.get("pos") or "").strip(),
                            "player_type": "MLB",
                            "manager": full_name,
                            "FBP_Team": abbr,
                            "contract_type": "Keeper Contract",
                            "years_simple": "TC 1",
                            "yahoo_id": str(yp.get("yahoo_id") or "").strip(),
                            "status": "",
                        }
                        combined.append(created)
                        rec = created

                        # Keep indexes in sync for downstream matching/drop checks.
                        idx["by_upid"][upid_from_name] = created
                        created_yid = str(created.get("yahoo_id") or "").strip()
                        if created_yid:
                            idx["by_yahoo_id"][created_yid] = created
                        created_name = (created.get("name") or "").strip().lower()
                        if created_name:
                            idx["by_name"].setdefault(created_name, []).append(created)

                if rec is None:
                    result.unmatched.append({"yahoo_player": yp, "fbp_team": fbp_team})
                    continue
                elif created is not None:
                    result.mlb_adds.append({
                        "name": created.get("name", ""),
                        "upid": created.get("upid", ""),
                        "team": fbp_team,
                        "full_name": full_name,
                        "mlb_team": created.get("team", ""),
                        "position": created.get("position", ""),
                        "previous_owner": None,
                    })
                    _append_player_log_entry(
                        player_log, created,
                        season=SEASON,
                        source="yahoo_roster_sync",
                        update_type="Roster",
                        event="In Season Add",
                        admin="roster_sync",
                    )

            upid = str(rec.get("upid") or "").strip()
            player_type = (rec.get("player_type") or "").strip()
            name = (rec.get("name") or "").strip()

            # Track this player as on Yahoo for this team
            if upid:
                yahoo_rostered_upids.setdefault(fbp_team, set()).add(upid)
            rec_yid = str(rec.get("yahoo_id") or "").strip()
            if rec_yid:
                yahoo_rostered_yids.setdefault(fbp_team, set()).add(rec_yid)

            # --- MLB player logic ---
            if player_type == "MLB":
                current_owner = (rec.get("FBP_Team") or "").strip()
                locked_owner = trade_owner_lock.get(upid) if upid else None

                if current_owner == fbp_team:
                    # Already owned by same team — no change
                    continue

                # Guardrail: preserve recent approved-trade ownership while
                # Yahoo/admin roster state catches up.
                if locked_owner and current_owner and current_owner.upper() == locked_owner and fbp_team.upper() != locked_owner:
                    result.trade_guard_skips.append({
                        "name": name,
                        "upid": upid,
                        "yahoo_team": fbp_team,
                        "locked_owner": locked_owner,
                    })
                    continue

                # New add (or ownership change)
                rec["manager"] = full_name
                rec["FBP_Team"] = abbr

                # Auto-assign contract if none exists
                ct = (rec.get("contract_type") or "").strip()
                if not ct:
                    rec["contract_type"] = "Keeper Contract"
                    rec["years_simple"] = "TC 1"

                add_info = {
                    "name": name,
                    "upid": upid,
                    "team": fbp_team,
                    "full_name": full_name,
                    "mlb_team": rec.get("team", ""),
                    "position": rec.get("position", ""),
                    "previous_owner": current_owner or None,
                }
                result.mlb_adds.append(add_info)

                _append_player_log_entry(
                    player_log, rec,
                    season=SEASON,
                    source="yahoo_roster_sync",
                    update_type="Roster",
                    event="In Season Add",
                    admin="roster_sync",
                )
                continue

            # --- Farm/Prospect logic ---
            if player_type == "Farm":
                contract = (rec.get("contract_type") or "").strip()
                owner_fbp = (rec.get("FBP_Team") or "").strip()
                owner_name = (rec.get("manager") or "").strip()

                alert: Optional[Dict] = None

                if not owner_fbp and not owner_name:
                    # Case 2: Unowned prospect
                    alert = {
                        "severity": "error",
                        "emoji": "🚨",
                        "message": (
                            f"🚨 {full_name} rostered {_hub_link(name, upid)} "
                            f"who is unowned. Player must go through auction. Drop required."
                        ),
                        "event": "Unowned Prospect Alert",
                    }
                elif owner_fbp != fbp_team:
                    # Case 3: Owned by another manager
                    _, other_name = _team_labels(teams_cfg, owner_fbp)
                    alert = {
                        "severity": "shame",
                        "emoji": "🚫",
                        "message": (
                            f"🚫 {full_name} rostered {_hub_link(name, upid)} "
                            f"who is owned by {other_name}! "
                            f"{full_name} must drop {name} immediately!"
                        ),
                        "event": "Wrong Owner Prospect Alert",
                    }
                elif contract == "Development Cont.":
                    # Case 1: DC player needs purchase
                    alert = {
                        "severity": "warning",
                        "emoji": "⚠️",
                        "message": (
                            f"⚠️ {full_name} rostered {_hub_link(name, upid)} "
                            f"who has a Development Contract. "
                            f"Player must be purchased before rostering."
                        ),
                        "event": "DC Roster Alert",
                    }
                else:
                    # Case 4: Valid call-up (owned by same manager, not DC)
                    # Only flag as NEW call-up if the player wasn't already
                    # on Yahoo in the previous sync (same guard as send-downs).
                    was_on_yahoo = upid in prev_yahoo_farm.get(fbp_team, set())
                    if not is_first_run and was_on_yahoo:
                        # Already reported in a prior run — skip
                        continue
                    if not is_first_run:
                        result.call_ups.append({
                            "name": name,
                            "upid": upid,
                            "team": fbp_team,
                            "full_name": full_name,
                            "mlb_team": rec.get("team", ""),
                            "position": rec.get("position", ""),
                        })
                        _append_player_log_entry(
                            player_log, rec,
                            season=SEASON,
                            source="yahoo_roster_sync",
                            update_type="Roster",
                            event="Call Up",
                            admin="roster_sync",
                        )
                    continue

                # Log the alert
                if alert:
                    result.prospect_alerts.append(alert)
                    _append_player_log_entry(
                        player_log, rec,
                        season=SEASON,
                        source="yahoo_roster_sync",
                        update_type="Roster",
                        event=alert["event"],
                        admin="roster_sync",
                    )
                continue

    # ---------------------------------------------------------------
    # Phase 2: Detect DROPS (players in combined but not on Yahoo)
    # ---------------------------------------------------------------
    for p in combined:
        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue

        player_type = (p.get("player_type") or "").strip()
        fbp_team = (p.get("FBP_Team") or "").strip()
        manager = (p.get("manager") or "").strip()

        if not fbp_team or not manager:
            continue

        # Only process teams that appear in the Yahoo data (if a team
        # is missing from Yahoo entirely, we don't want to drop everyone)
        if fbp_team not in yahoo_data:
            continue

        yahoo_id = str(p.get("yahoo_id") or "").strip()
        on_yahoo = (
            upid in yahoo_rostered_upids.get(fbp_team, set())
            or yahoo_id in yahoo_rostered_yids.get(fbp_team, set())
        )

        if not on_yahoo:
            name = (p.get("name") or "").strip()
            _, full_name = _team_labels(teams_cfg, fbp_team)
            locked_owner = trade_owner_lock.get(upid)

            if player_type == "MLB":
                if locked_owner and fbp_team.upper() == locked_owner:
                    result.trade_guard_skips.append({
                        "name": name,
                        "upid": upid,
                        "yahoo_team": None,
                        "locked_owner": locked_owner,
                    })
                    continue
                # MLB drop
                p["manager"] = None
                p["FBP_Team"] = ""
                # Do NOT touch contract_type, years_simple, etc.

                result.mlb_drops.append({
                    "name": name,
                    "upid": upid,
                    "team": fbp_team,
                    "full_name": full_name,
                    "mlb_team": p.get("team", ""),
                    "position": p.get("position", ""),
                })

                _append_player_log_entry(
                    player_log, p,
                    season=SEASON,
                    source="yahoo_roster_sync",
                    update_type="Roster",
                    event="In Season Drop",
                    admin="roster_sync",
                )

            elif player_type == "Farm":
                # Only flag as send-down if the player was on Yahoo
                # in the previous sync. Without prior state (first run)
                # we skip to avoid false-flagging every non-called-up prospect.
                was_on_yahoo = upid in prev_yahoo_farm.get(fbp_team, set())
                if not is_first_run and was_on_yahoo:
                    result.send_downs.append({
                        "name": name,
                        "upid": upid,
                        "team": fbp_team,
                        "full_name": full_name,
                        "mlb_team": p.get("team", ""),
                        "position": p.get("position", ""),
                    })

                    _append_player_log_entry(
                        player_log, p,
                        season=SEASON,
                        source="yahoo_roster_sync",
                        update_type="Roster",
                        event="Send Down",
                        admin="roster_sync",
                    )

    # ---------------------------------------------------------------
    # Phase 3: Persist
    # ---------------------------------------------------------------
    # Build Yahoo Farm snapshot for next run's send-down detection
    yahoo_farm_snapshot: Dict[str, List[str]] = {}
    for team, upids in yahoo_rostered_upids.items():
        farm_upids = [
            u for u in upids
            if u in idx["by_upid"]
            and (idx["by_upid"][u].get("player_type") or "") == "Farm"
        ]
        if farm_upids:
            yahoo_farm_snapshot[team] = farm_upids

    if not dry_run:
        _save_json(COMBINED_FILE, combined)
        result.mutated_files.append(COMBINED_FILE)

        _save_json(PLAYER_LOG_FILE, player_log)
        result.mutated_files.append(PLAYER_LOG_FILE)

        # Save Yahoo Farm state for next run
        _save_json(STATE_FILE, {
            "updated_at": datetime.now(tz=ET).isoformat(),
            "yahoo_farm_upids": yahoo_farm_snapshot,
        })
        result.mutated_files.append(STATE_FILE)

        # Queue Discord messages for the pipeline runner / health.py
        _save_messages(result)
        result.mutated_files.append(MESSAGES_FILE)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print(f"📋 Roster Sync Complete:")
    print(f"   MLB adds:          {len(result.mlb_adds)}")
    print(f"   MLB drops:         {len(result.mlb_drops)}")
    print(f"   Prospect alerts:   {len(result.prospect_alerts)}")
    print(f"   Call ups:          {len(result.call_ups)}")
    print(f"   Send downs:        {len(result.send_downs)}")
    print(f"   Unmatched:         {len(result.unmatched)}")
    print(f"   Trade-guard skips: {len(result.trade_guard_skips)}")
    if result.unmatched:
        for u in result.unmatched[:5]:
            yp = u["yahoo_player"]
            print(f"      ⚠️ {yp.get('name')} (yahoo_id={yp.get('yahoo_id')}) on {u['fbp_team']}")

    return result


# ---------------------------------------------------------------------------
# Discord message queuing
# ---------------------------------------------------------------------------

def _save_messages(result: RosterSyncResult) -> None:
    """Write queued Discord messages to roster_sync_messages.json."""
    existing = _load_json(MESSAGES_FILE) or {}
    last_posted_date = existing.get("last_posted_date") if isinstance(existing, dict) else None
    messages: Dict[str, Any] = {
        "generated_at": datetime.now(tz=ET).isoformat(),
        "immediate": [],            # post right away (prospect alerts)
        "batched_prospect": [],     # post at 9 AM → Prospect Moves channel
        "batched_free_agency": [],  # post at 9 AM → Free Agency channel
    }
    if last_posted_date:
        messages["last_posted_date"] = last_posted_date

    # MLB adds → Free Agency channel
    for add in result.mlb_adds:
        messages["batched_free_agency"].append(
            f"\u2705 **{add['full_name']}** adds {add['name']} "
            f"{add['mlb_team']} {add['position']}"
        )

    # MLB drops → Free Agency channel
    for drop in result.mlb_drops:
        messages["batched_free_agency"].append(
            f"\U0001f5d1\ufe0f **{drop['full_name']}** drops {drop['name']} "
            f"{drop['mlb_team']} {drop['position']}"
        )

    # Prospect alerts (immediate) → Prospect Moves channel
    for alert in result.prospect_alerts:
        messages["immediate"].append(alert["message"])

    # Unmatched Yahoo players → immediate / Prospect Moves channel
    for item in result.unmatched[:25]:
        yp = item.get("yahoo_player") or {}
        team = item.get("fbp_team") or "UNKNOWN"
        messages["immediate"].append(
            f"\u26a0\ufe0f **Roster Sync Unmatched**: {team} has {yp.get('name','Unknown')} "
            f"({yp.get('position','N/A')} {yp.get('team','N/A')}) that could not be mapped in UPID/combined."
        )

    # Call-ups → Prospect Moves channel
    for cu in result.call_ups:
        messages["batched_prospect"].append(
            f"\u2b06\ufe0f **{cu['full_name']}** calls up {cu['name']} "
            f"{cu['mlb_team']} {cu['position']}"
        )

    # Send-downs → Prospect Moves channel
    for sd in result.send_downs:
        messages["batched_prospect"].append(
            f"\u2b07\ufe0f **{sd['full_name']}** sends down {sd['name']} "
            f"{sd['mlb_team']} {sd['position']}"
        )

    _save_json(MESSAGES_FILE, messages)
    bp = len(messages["batched_prospect"])
    bf = len(messages["batched_free_agency"])
    imm = len(messages["immediate"])
    print(f"   Messages queued:   {imm + bp + bf} ({imm} immediate, {bp} prospect, {bf} free agency)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("🔍 DRY RUN — no files will be written\n")
    result = run_sync(dry_run=dry)
