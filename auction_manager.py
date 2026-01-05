"""Auction management logic for FBP Prospect Auction Portal.

This module centralizes all business rules for the in-season prospect
auction, so both Discord commands and the FastAPI API can share a
single source of truth.

This module now supports:
- Phase detection and schedule gating (including All-Star and playoffs).
- OB/CB bid placement and validation.
- Explicit Match/Forfeit decisions from OB managers.
- Weekly resolution to determine winners and apply WizBucks / roster
  updates (intended to run via a Sunday 2pm ET job).
"""

from __future__ import annotations

import dataclasses
import enum
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import zoneinfo

# Timezone for all league operations
ET = zoneinfo.ZoneInfo("America/New_York")


class AuctionPhase(str, enum.Enum):
    """High-level phase of the weekly auction.

    OFF_WEEK means that auctions are not active this week at all
    (pre-season, All-Star break, playoffs, etc.).
    """

    OFF_WEEK = "off_week"
    OB_WINDOW = "ob_window"
    CB_WINDOW = "cb_window"
    OB_FINAL = "ob_final"
    PROCESSING = "processing"


class BidType(str, enum.Enum):
    OB = "OB"  # Originating Bid
    CB = "CB"  # Challenge Bid


@dataclass
class Bid:
    team: str
    prospect_id: str
    amount: int
    bid_type: BidType
    timestamp: str  # ISO 8601 in UTC
    date: str  # calendar date in America/New_York (YYYY-MM-DD)


@dataclass
class MatchDecision:
    team: str
    prospect_id: str
    decision: str  # "match" or "forfeit"
    decided_at: str  # ISO 8601 in UTC
    source: str  # "discord" or "web"


@dataclass
class AuctionState:
    week_start: str  # Monday date YYYY-MM-DD in ET
    phase: str
    priority_order: List[str]
    bids: List[Dict[str, Any]]
    matches: List[Dict[str, Any]]
    schedule_meta: Dict[str, Any]
    last_updated: str


class AuctionManager:
    """Core interface for auction operations.

    All file IO and rule enforcement for the prospect auction should
    live here so that Discord commands and HTTP endpoints can simply
    call into these methods.
    """

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.auction_file = self.data_dir / "auction_current.json"
        self.players_file = self.data_dir / "combined_players.json"
        self.standings_file = self.data_dir / "standings.json"
        self.wizbucks_file = self.data_dir / "wizbucks.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_phase(self, now: Optional[datetime] = None) -> AuctionPhase:
        """Return the current auction phase for *this moment*.

        If auctions are not active this week, returns OFF_WEEK.
        """

        now = now or datetime.now(tz=ET)
        state = self._load_or_initialize_auction(now)

        if not self._is_week_active(now.date(), state["schedule_meta"]):
            return AuctionPhase.OFF_WEEK

        return self._phase_for_time(now)

    def place_bid(
        self,
        *,
        team: str,
        prospect_id: str,
        amount: int,
        bid_type: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Validate and place a bid.

        Returns a dict of the form:
        - {"success": True, "bid": {...}, "phase": "cb_window"}
        - {"success": False, "error": "..."}
        """

        now = now or datetime.now(tz=ET)
        state = self._load_or_initialize_auction(now)

        if not self._is_week_active(now.date(), state["schedule_meta"]):
            return {"success": False, "error": "No auction this week."}

        phase = self._phase_for_time(now)
        if phase == AuctionPhase.OFF_WEEK:
            return {"success": False, "error": "Auctions are not active right now."}

        if phase == AuctionPhase.PROCESSING:
            return {"success": False, "error": "Auction is in processing; bids are closed."}

        try:
            bid_type_enum = BidType(bid_type)
        except ValueError:
            return {"success": False, "error": "Invalid bid type. Use OB or CB."}

        # Load supporting data
        players = self._load_json(self.players_file) or []
        wizbucks = self._load_json(self.wizbucks_file) or {}

        # Basic validations
        normalized_team = team.upper()
        if not self._is_team_known(normalized_team, players):
            return {"success": False, "error": f"Unknown team: {normalized_team}"}

        if amount <= 0:
            return {"success": False, "error": "Bid amount must be positive."}

        prospect = self._find_prospect(players, prospect_id)
        if not prospect:
            return {"success": False, "error": "Prospect not found or not eligible."}

        if prospect.get("manager"):
            return {"success": False, "error": "Prospect already owned and not eligible for auction."}

        # Phase-specific rules
        if bid_type_enum is BidType.OB and phase is not AuctionPhase.OB_WINDOW:
            return {"success": False, "error": "Originating bids are only allowed Mon 3pm–Tue EOD (ET)."}

        if bid_type_enum is BidType.CB and phase is not AuctionPhase.CB_WINDOW:
            return {"success": False, "error": "Challenge bids are only allowed Wed–Fri 9pm (ET)."}

        if bid_type_enum is BidType.OB and amount < 10:
            return {"success": False, "error": "Originating bids must be at least $10 WB."}

        # Canonicalize state data
        bids: List[Dict[str, Any]] = state.setdefault("bids", [])

        # Enforce OB rules
        if bid_type_enum is BidType.OB:
            # 1 OB per team per week
            if any(b["team"] == normalized_team and b["type"] == "OB" for b in bids):
                return {"success": False, "error": "You have already placed an originating bid this week."}

            # Only one OB per prospect per week
            if any(
                b["prospect_id"] == prospect["id"] and b["type"] == "OB" for b in bids
            ):
                return {"success": False, "error": "This prospect already has an originating bid."}

        # Enforce CB rules
        if bid_type_enum is BidType.CB:
            # OB manager cannot CB their own prospect
            ob_team = self._get_ob_team_for_prospect(bids, prospect["id"])
            if not ob_team:
                return {"success": False, "error": "Challenge bids require an existing originating bid."}

            if ob_team == normalized_team:
                return {"success": False, "error": "Originating manager cannot place challenge bids on their own OB."}

            # Minimum raise of +$5
            current_high = self._get_current_high_bid_amount(bids, prospect["id"])
            if amount < current_high + 5:
                return {
                    "success": False,
                    "error": f"Challenge bids must be at least $5 above current high (${current_high}).",
                }

            # 1 CB per team per prospect per calendar day (ET)
            et_date_str = now.date().isoformat()
            if any(
                b["team"] == normalized_team
                and b["prospect_id"] == prospect["id"]
                and b["type"] == "CB"
                and b.get("date") == et_date_str
                for b in bids
            ):
                return {
                    "success": False,
                    "error": "You already have a challenge bid on this prospect today.",
                }

        # Check WizBucks balance (simple check: total - committed >= bid amount)
        # NOTE: committed calculation here is conservative and will be
        # refined alongside full Sunday resolution logic.
        total_balance = int(wizbucks.get(normalized_team, 0))
        committed = self._get_committed_wb_for_team(bids, normalized_team)
        available = total_balance - committed

        if amount > available:
            return {
                "success": False,
                "error": f"Insufficient WB. You have ${available} available (total ${total_balance}, committed ${committed}).",
            }

        # Construct bid payload
        utc_now = datetime.now(tz=timezone.utc)
        bid = Bid(
            team=normalized_team,
            prospect_id=str(prospect["id"]),
            amount=int(amount),
            bid_type=bid_type_enum,
            timestamp=utc_now.isoformat(),
            date=now.date().isoformat(),
        )

        bids.append(dataclasses.asdict(bid))
        state["last_updated"] = utc_now.isoformat()
        state["phase"] = self._phase_for_time(now).value

        self._save_auction_state(state)

        return {"success": True, "bid": dataclasses.asdict(bid), "phase": state["phase"]}

    def record_match(
        self,
        *,
        team: str,
        prospect_id: str,
        decision: str,
        source: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Record a Match or Forfeit decision from OB manager.

        Only the originating manager may record a decision for that
        prospect, and only during the OB_FINAL window. Decisions are
        final; a second attempt will be rejected.
        """

        now = now or datetime.now(tz=ET)
        state = self._load_or_initialize_auction(now)

        if not self._is_week_active(now.date(), state["schedule_meta"]):
            return {"success": False, "error": "No auction this week."}

        phase = self._phase_for_time(now)
        if phase is not AuctionPhase.OB_FINAL:
            return {"success": False, "error": "Match / Forfeit is only allowed on Saturday."}

        decision_norm = decision.lower()
        if decision_norm not in {"match", "forfeit"}:
            return {"success": False, "error": "Decision must be 'match' or 'forfeit'."}

        bids: List[Dict[str, Any]] = state.setdefault("bids", [])
        matches: List[Dict[str, Any]] = state.setdefault("matches", [])

        normalized_team = team.upper()
        prospect_id_str = str(prospect_id)

        # Verify this team is the OB manager for this prospect
        ob_team = self._get_ob_team_for_prospect(bids, prospect_id_str)
        if not ob_team:
            return {"success": False, "error": "No originating bid found for this prospect."}

        if ob_team != normalized_team:
            return {"success": False, "error": "Only the originating manager may record a decision."}

        # Ensure no existing decision
        if any(
            m["team"] == normalized_team and m["prospect_id"] == prospect_id_str for m in matches
        ):
            return {"success": False, "error": "You have already recorded a decision for this prospect."}

        utc_now = datetime.now(tz=timezone.utc)
        record = MatchDecision(
            team=normalized_team,
            prospect_id=prospect_id_str,
            decision=decision_norm,
            decided_at=utc_now.isoformat(),
            source=source,
        )

        matches.append(dataclasses.asdict(record))
        state["last_updated"] = utc_now.isoformat()
        self._save_auction_state(state)

        return {"success": True, "match": dataclasses.asdict(record)}

    # ------------------------------------------------------------------
    # Weekly resolution (Sunday processing)
    # ------------------------------------------------------------------

    def resolve_week(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Resolve the current week's auction and apply results.

        This is intended to be run once per week around Sunday 2pm ET
        by a GitHub Action or scheduled job. It determines winners for
        each prospect, enforces WizBucks affordability (dropping latest
        wins first when a team is over-committed), and applies roster /
        balance updates to combined_players.json and wizbucks.json.

        Returns a summary dict with winners keyed by prospect id.
        """

        now = now or datetime.now(tz=ET)
        state = self._load_or_initialize_auction(now)

        # If auctions are not active this week, nothing to do.
        if not self._is_week_active(now.date(), state["schedule_meta"]):
            return {"status": "inactive", "winners": {}}

        bids: List[Dict[str, Any]] = state.setdefault("bids", [])
        matches: List[Dict[str, Any]] = state.setdefault("matches", [])

        if not bids:
            return {"status": "no_bids", "winners": {}}

        # Load supporting data
        players = self._load_json(self.players_file) or []
        wizbucks = self._load_json(self.wizbucks_file) or {}

        # Build priority map from state.priority_order: earlier index = better (worse standing)
        priority_order: List[str] = state.get("priority_order") or []
        priority_index = {team: idx for idx, team in enumerate(priority_order)}

        # 1) Compute tentative winners ignoring affordability
        winners_by_prospect = self._compute_tentative_winners(
            bids=bids,
            matches=matches,
            priority_index=priority_index,
        )

        # 2) Enforce WizBucks affordability per team by dropping latest wins
        # and recomputing winners for affected prospects.
        winners_by_prospect = self._enforce_affordability(
            winners_by_prospect=winners_by_prospect,
            bids=bids,
            matches=matches,
            wizbucks=wizbucks,
            priority_index=priority_index,
        )

        # 3) Apply results to players (assign PC contract + manager) and wizbucks
        winners_summary: Dict[str, Any] = {}

        by_team_totals: Dict[str, int] = {}
        for prospect_id, win in winners_by_prospect.items():
            team = win["team"]
            amount = int(win["amount"])
            by_team_totals[team] = by_team_totals.get(team, 0) + amount

            # Update players list: assign manager + PC contract if still unowned
            for p in players:
                if str(p.get("id")) == prospect_id:
                    p["manager"] = team
                    # Do not overwrite an existing contract_type if present
                    if not p.get("contract_type"):
                        p["contract_type"] = "PC"
                    break

            winners_summary[prospect_id] = {
                "team": team,
                "amount": amount,
            }

        # Apply WizBucks debits
        for team, spent in by_team_totals.items():
            current = int(wizbucks.get(team, 0))
            wizbucks[team] = current - spent

        # Persist updated data files
        with self.players_file.open("w", encoding="utf-8") as f_players:
            json.dump(players, f_players, indent=2, sort_keys=True)

        with self.wizbucks_file.open("w", encoding="utf-8") as f_wb:
            json.dump(wizbucks, f_wb, indent=2, sort_keys=True)

        state["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
        state["phase"] = AuctionPhase.PROCESSING.value
        self._save_auction_state(state)

        return {"status": "resolved", "winners": winners_summary}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_or_initialize_auction(self, now: datetime) -> Dict[str, Any]:
        if self.auction_file.exists():
            data = self._load_json(self.auction_file) or {}
            if data.get("week_start"):
                return data

        # If no file or missing week_start, initialize for current week
        week_start = self._monday_for_date(now.date())
        schedule_meta = self._default_schedule_meta()
        priority_order = self._compute_priority_order()
        utc_now = datetime.now(tz=timezone.utc)

        state: Dict[str, Any] = {
            "week_start": week_start.isoformat(),
            "phase": self._phase_for_time(now).value,
            "priority_order": priority_order,
            "bids": [],
            "matches": [],
            "schedule_meta": schedule_meta,
            "last_updated": utc_now.isoformat(),
        }
        self._save_auction_state(state)
        return state

    def _save_auction_state(self, state: Dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.auction_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)

    @staticmethod
    def _load_json(path: Path) -> Any:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # Phase and schedule
    # ------------------------------------------------------------------

    def _default_schedule_meta(self) -> Dict[str, Any]:
        """Return default season schedule configuration.

        These dates should be updated each season.
        """

        # Placeholder hard-coded dates; adjust per-season.
        return {
            "season_start": "2026-04-01",  # first auction week start (Monday)
            "all_star_break_start": "2026-07-13",  # Monday of All-Star break
            "auction_restart": "2026-07-27",  # Monday auctions resume
            "playoffs_start": "2026-09-07",  # Monday of playoff week (auctions off)
        }

    def _is_week_active(self, today: date, schedule_meta: Dict[str, Any]) -> bool:
        season_start = date.fromisoformat(schedule_meta["season_start"])
        all_star_break = date.fromisoformat(schedule_meta["all_star_break_start"])
        restart = date.fromisoformat(schedule_meta["auction_restart"])
        playoffs = date.fromisoformat(schedule_meta["playoffs_start"])

        if today < season_start:
            return False
        if all_star_break <= today < restart:
            return False
        if today >= playoffs:
            return False
        return True

    def _phase_for_time(self, now: datetime) -> AuctionPhase:
        """Derive phase from current ET time.

        Assumes the week is active.
        """

        # Weekday: Monday=0 ... Sunday=6
        weekday = now.weekday()
        t = now.timetz()

        def et_time(h: int, m: int = 0) -> time:
            return time(hour=h, minute=m, tzinfo=ET)

        # OB: Mon 3pm – Tue 11:59pm
        if weekday == 0 and t >= et_time(15):
            return AuctionPhase.OB_WINDOW
        if weekday == 1:
            return AuctionPhase.OB_WINDOW

        # CB: Wed 12am – Fri 9pm
        if weekday == 2 or weekday == 3:
            return AuctionPhase.CB_WINDOW
        if weekday == 4 and t <= et_time(21):
            return AuctionPhase.CB_WINDOW

        # OB final: Sat 12am – Sat 10pm
        if weekday == 5 and t <= et_time(22):
            return AuctionPhase.OB_FINAL

        # Processing: Sunday
        if weekday == 6:
            return AuctionPhase.PROCESSING

        # Default: OFF_WEEK-style phase
        return AuctionPhase.OFF_WEEK

    @staticmethod
    def _monday_for_date(d: date) -> date:
        return d - timedelta(days=d.weekday())

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _compute_priority_order(self) -> List[str]:
        """Compute weekly priority order from standings.

        Returns a list of team abbreviations ordered from worst
        standings position to best (i.e., highest rank number first).
        If standings data is unavailable, falls back to an alphabetical
        list of manager abbreviations present in combined_players.json.
        """

        standings = self._load_json(self.standings_file) or {}
        order: List[str] = []

        try:
            entries = standings.get("standings") or []
            # Higher rank number = worse team = earlier in priority order.
            sorted_entries = sorted(entries, key=lambda s: s.get("rank", 0), reverse=True)
            order = [str(e.get("team")) for e in sorted_entries if e.get("team")]
        except Exception:
            order = []

        if order:
            return order

        # Fallback: derive from players file
        players = self._load_json(self.players_file) or []
        managers = sorted({p.get("manager") for p in players if p.get("manager")})
        return managers

    def _compute_tentative_winners(
        self,
        *,
        bids: List[Dict[str, Any]],
        matches: List[Dict[str, Any]],
        priority_index: Dict[str, int],
    ) -> Dict[str, Dict[str, Any]]:
        """Compute winners per prospect ignoring WizBucks affordability."""

        # Group bids by prospect
        by_prospect: Dict[str, List[Dict[str, Any]]] = {}
        for b in bids:
            pid = str(b["prospect_id"])
            by_prospect.setdefault(pid, []).append(b)

        # Index matches by (team, prospect)
        match_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for m in matches:
            key = (m["team"], str(m["prospect_id"]))
            match_map[key] = m

        winners: Dict[str, Dict[str, Any]] = {}

        for prospect_id, pbids in by_prospect.items():
            # Find OB and CBs
            ob_bid = next((b for b in pbids if b["type"] == "OB"), None)
            cb_bids = [b for b in pbids if b["type"] == "CB"]

            if not ob_bid and not cb_bids:
                continue

            ob_team = ob_bid["team"] if ob_bid else None

            # Determine best CB by amount, breaking ties via priority order
            best_cb = None
            if cb_bids:
                max_amt = max(int(b["amount"]) for b in cb_bids)
                top_cbs = [b for b in cb_bids if int(b["amount"]) == max_amt]

                def cb_sort_key(b: Dict[str, Any]) -> Tuple[int, str]:
                    team = b["team"]
                    return (priority_index.get(team, 999), team)

                top_cbs.sort(key=cb_sort_key)
                best_cb = top_cbs[0]

            # Case 1: no CBs => OB wins at flat $10 if present
            if ob_bid and not best_cb:
                winners[prospect_id] = {
                    "team": ob_team,
                    "amount": 10,
                    "timestamp": ob_bid["timestamp"],
                    "source": "OB_ONLY",
                }
                continue

            # Case 2: CBs exist
            if best_cb:
                cb_team = best_cb["team"]
                cb_amount = int(best_cb["amount"])

                # Check for OB match decision
                decision = None
                if ob_team:
                    key = (ob_team, prospect_id)
                    m = match_map.get(key)
                    if m:
                        decision = m.get("decision")

                if ob_team and decision == "match":
                    # OB manager matches final high CB amount
                    winners[prospect_id] = {
                        "team": ob_team,
                        "amount": cb_amount,
                        "timestamp": best_cb["timestamp"],
                        "source": "OB_MATCH",
                    }
                else:
                    # Either no match, explicit forfeit, or no OB at all
                    winners[prospect_id] = {
                        "team": cb_team,
                        "amount": cb_amount,
                        "timestamp": best_cb["timestamp"],
                        "source": "CB_WIN",
                    }

        return winners

    def _enforce_affordability(
        self,
        *,
        winners_by_prospect: Dict[str, Dict[str, Any]],
        bids: List[Dict[str, Any]],
        matches: List[Dict[str, Any]],
        wizbucks: Dict[str, Any],
        priority_index: Dict[str, int],
    ) -> Dict[str, Dict[str, Any]]:
        """Ensure each team can afford their wins by dropping latest wins first.

        This operates per team and recomputes winners for prospects that
        lose their previous winner. It is a heuristic approximation of
        the league's intent (latest commitments are dropped first) and
        assumes overspend scenarios are relatively rare.
        """

        # Build per-team winner lists
        winners_per_team: Dict[str, List[Dict[str, Any]]] = {}
        for prospect_id, win in winners_by_prospect.items():
            team = win["team"]
            amount = int(win["amount"])
            ts = win.get("timestamp") or ""
            winners_per_team.setdefault(team, []).append(
                {
                    "prospect_id": prospect_id,
                    "amount": amount,
                    "timestamp": ts,
                }
            )

        # Helper to recompute winner for a single prospect with one team removed
        def recompute_for_prospect(prospect_id: str, removed_team: str) -> Optional[Dict[str, Any]]:
            pbids = [b for b in bids if str(b["prospect_id"]) == prospect_id and b["team"] != removed_team]
            if not pbids:
                return None
            # Reuse tentative winner logic on a single prospect
            sub_winners = self._compute_tentative_winners(
                bids=pbids,
                matches=[m for m in matches if str(m["prospect_id"]) == prospect_id],
                priority_index=priority_index,
            )
            return sub_winners.get(prospect_id)

        # Per-team pass
        for team, wins in list(winners_per_team.items()):
            total_balance = int(wizbucks.get(team, 0))

            def current_spend() -> int:
                return sum(w["amount"] for w in winners_per_team.get(team, []))

            # Drop latest wins until within budget
            while current_spend() > total_balance and winners_per_team.get(team):
                # Find latest win for this team
                wins_sorted = sorted(
                    winners_per_team[team],
                    key=lambda w: w.get("timestamp", ""),
                    reverse=True,
                )
                latest = wins_sorted[0]
                pid = latest["prospect_id"]

                # Remove this win
                winners_per_team[team] = [w for w in winners_per_team[team] if w["prospect_id"] != pid]
                old_win = winners_by_prospect.get(pid)
                if old_win and old_win.get("team") == team:
                    del winners_by_prospect[pid]

                # Recompute winner for that prospect without this team
                new_win = recompute_for_prospect(pid, team)
                if new_win:
                    winners_by_prospect[pid] = new_win
                    new_team = new_win["team"]
                    winners_per_team.setdefault(new_team, []).append(
                        {
                            "prospect_id": pid,
                            "amount": int(new_win["amount"]),
                            "timestamp": new_win.get("timestamp") or "",
                        }
                    )

        return winners_by_prospect

    def _is_team_known(self, team: str, players: List[Dict[str, Any]]) -> bool:
        return any(p.get("manager") == team for p in players)

    def _find_prospect(self, players: List[Dict[str, Any]], prospect_id: str) -> Optional[Dict[str, Any]]:
        # First try by exact id string
        for p in players:
            if str(p.get("id")) == str(prospect_id) and p.get("player_type") == "Farm":
                return p
        # Fallback: try by name match for now
        for p in players:
            if (
                p.get("name") == prospect_id
                and p.get("player_type") == "Farm"
                and not p.get("manager")
            ):
                return p
        return None

    @staticmethod
    def _get_ob_team_for_prospect(bids: List[Dict[str, Any]], prospect_id: str) -> Optional[str]:
        for b in bids:
            if b["prospect_id"] == str(prospect_id) and b["type"] == "OB":
                return b["team"]
        return None

    @staticmethod
    def _get_current_high_bid_amount(bids: List[Dict[str, Any]], prospect_id: str) -> int:
        high = 0
        for b in bids:
            if b["prospect_id"] == str(prospect_id):
                amt = int(b["amount"])
                if amt > high:
                    high = amt
        return high

    @staticmethod
    def _get_committed_wb_for_team(bids: List[Dict[str, Any]], team: str) -> int:
        """Compute a conservative committed WB for a team.

        For now, this treats any bid where the team is currently the
        high bidder on a prospect as "committed". This will be
        tightened up alongside the full weekly resolution logic.
        """

        committed = 0
        by_prospect: Dict[str, Tuple[int, str]] = {}
        for b in bids:
            prospect_id = b["prospect_id"]
            amt = int(b["amount"])
            t = b["team"]
            cur_amt, cur_team = by_prospect.get(prospect_id, (0, ""))
            if amt > cur_amt or (amt == cur_amt and t == team):
                by_prospect[prospect_id] = (amt, t)

        for amt, t in by_prospect.values():
            if t == team:
                committed += amt

        return committed
