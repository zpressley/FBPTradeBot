import os
import asyncio
from datetime import datetime, date
from zoneinfo import ZoneInfo
import threading
import sys
import json

import discord
from discord.ext import commands
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

from auction_manager import AuctionManager
from draft.draft_manager import DraftManager
from draft.prospect_database import ProspectDatabase
from draft.pick_validator import PickValidator
from draft.board_manager import BoardManager
from pad.pad_processor import (
    PadSubmissionPayload,
    PadAlreadySubmittedError,
    PadResult,
    apply_pad_submission,
    announce_pad_submission_to_discord,
    load_managers_config,
)
from admin.admin_processor import (
    AdminPlayerUpdatePayload,
    AdminWBAdjustmentPayload,
    AdminDeletePlayerPayload,
    AdminMergePlayersPayload,
    apply_admin_player_update,
    apply_admin_wb_adjustment,
    apply_admin_delete_player,
    apply_admin_merge_players,
)

# Load environment variables
load_dotenv()


def configure_git() -> None:
    """Configure git identity and remote for Render deployments.

    This is a no-op locally if env vars are not set.
    """
    try:
        import subprocess

        email = os.getenv("GIT_USER_EMAIL", "bot@fbp.com")
        name = os.getenv("GIT_USER_NAME", "FBP Bot")

        subprocess.run(["git", "config", "--global", "user.email", email], check=True)
        subprocess.run(["git", "config", "--global", "user.name", name], check=True)

        token = os.getenv("GITHUB_TOKEN")
        if token:
            print("✅ Git token detected; git push will use token URL when available")
        else:
            print("⚠️ GITHUB_TOKEN not set - git push will use default remote auth (may fail)")
    except Exception as exc:
        print(f"❌ Git configuration error: {exc}")


# Configure git once on process start (safe no-op locally)
configure_git()

ET = ZoneInfo("US/Eastern")
PROSPECT_DRAFT_SEASON = 2026
SEASON_DATES_PATH = "config/season_dates.json"

# PAD (Prospect Allocation Day) config
PAD_SEASON = PROSPECT_DRAFT_SEASON
PAD_TEST_MODE = os.getenv("PAD_TEST_MODE", "false").lower() == "true"
PAD_TEST_CHANNEL_ID = int(os.getenv("PAD_TEST_CHANNEL_ID", "0"))  # test PAD announcements
PAD_LIVE_PAD_CHANNEL_ID = int(os.getenv("PAD_LIVE_PAD_CHANNEL_ID", "0"))  # live PAD announcements

TEST_AUCTION_CHANNEL_ID = 1197200421639438537  # test channel for auction logs

# Repo root used for git operations (Render default path, override via env)
REPO_ROOT = os.getenv("REPO_ROOT", "/opt/render/project/src")

# ---- Discord Bot Setup ----
TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("BOT_API_KEY")  # for FastAPI authentication
PORT = int(os.getenv("PORT", 8000))

if not TOKEN:
    print("❌ DISCORD_TOKEN not set in environment")
    sys.exit(1)

# Write credentials from environment (for Render deployment)
google_creds = os.getenv("GOOGLE_CREDS_JSON")
if google_creds:
    with open("google_creds.json", "w") as f:
        f.write(google_creds)
    print("✅ Google credentials written")

yahoo_token = os.getenv("YAHOO_TOKEN_JSON")
if yahoo_token:
    with open("token.json", "w") as f:
        f.write(yahoo_token)
    print("✅ Yahoo token written")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    print(f"   Connected to {len(bot.guilds)} guild(s)")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="FBP League | /help for commands"
        )
    )

@bot.event
async def setup_hook():
    extensions = [
        "commands.trade",
        "commands.roster",
        "commands.player",
        "commands.standings",
        "commands.draft",
        "commands.board"
    ]
    
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            print(f"   ✅ Loaded: {ext}")
        except Exception as e:
            print(f"   ⚠️ Failed to load {ext}: {e}")
    
    # Auction commands (optional)
    try:
        await bot.load_extension("commands.auction")
        print(f"   ✅ Loaded: commands.auction")
    except Exception as exc:
        print(f"   ⚠️ Failed to load auction commands: {exc}")
    
    print("🔄 Syncing slash commands...")
    await bot.tree.sync()
    print("✅ Slash commands synced")

# ---- FastAPI Web Server ----
app = FastAPI()


# Health check
@app.get("/")
def health():
    bot_status = "connected" if bot.is_ready() else "connecting"
    return {
        "status": "ok",
        "bot": str(bot.user) if bot.user else "Not connected",
        "bot_status": bot_status,
        "guilds": len(bot.guilds) if bot.is_ready() else 0
    }

@app.get("/health")
def detailed_health():
    """Detailed health check for monitoring"""
    return {
        "status": "ok",
        "discord_bot": {
            "connected": bot.is_ready(),
            "user": str(bot.user) if bot.user else None,
            "guilds": len(bot.guilds) if bot.is_ready() else 0,
            "latency_ms": round(bot.latency * 1000, 2) if bot.is_ready() else None
        },
        "server": {
            "port": PORT,
            "pid": os.getpid()
        }
    }


# ---- API auth helpers ----

def verify_api_key(x_api_key: str = Header(...)) -> bool:
    """Simple API key gate for Cloudflare Worker → bot API traffic.

    Logs minimal diagnostics for debugging, without exposing the key value.
    """

    if not API_KEY:
        print("❌ API request received but BOT_API_KEY is not configured in environment")
        raise HTTPException(status_code=500, detail="BOT_API_KEY not configured")

    if x_api_key != API_KEY:
        # Do NOT log the provided key; just note that it was invalid.
        print("❌ API request with invalid X-API-Key header")
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


class BidRequest(BaseModel):
    team: str
    prospect_id: str
    amount: int
    bid_type: str  # "OB" or "CB"


class MatchRequest(BaseModel):
    team: str
    prospect_id: str
    decision: str  # "match" or "forfeit"
    source: str = "web"  # or "discord"


class ProspectValidateRequest(BaseModel):
    team: str
    player: str


class BoardUpdateRequest(BaseModel):
    team: str
    board: list[str]


# ---- Draft schedule helpers ----

def load_season_dates() -> dict:
    """Load season date configuration from config/season_dates.json."""
    with open(SEASON_DATES_PATH, "r") as f:
        return json.load(f)


def get_draft_date(draft_type: str) -> date | None:
    """Return the scheduled calendar date for a given draft type.

    Draft type → date key mapping:
      - "keeper"   → season_dates["keeper_draft"]
      - "prospect" → season_dates["prospect_draft"]
    """
    data = load_season_dates()
    if draft_type == "keeper":
        key = "keeper_draft"
    elif draft_type == "prospect":
        key = "prospect_draft"
    else:
        return None

    raw = data.get(key)
    if not raw:
        return None
    # Dates are stored as YYYY-MM-DD; interpret in ET.
    return datetime.fromisoformat(raw).date()


def compute_draft_status(draft_type: str, state: dict) -> str:
    """Compute high-level draft status.

    Returns one of: 'pre_draft', 'draft_day', 'active_draft', 'post_draft'.

    Priority:
      1) If DraftManager.state.status is 'active' or 'paused' → 'active_draft'.
      2) Else compare today's date to the scheduled draft date.
    """
    raw_state = state.get("status", "not_started")
    today = datetime.now(tz=ET).date()
    draft_date = get_draft_date(draft_type)

    # Bot state has priority
    if raw_state in ("active", "paused"):
        return "active_draft"

    # Calendar-based states
    if draft_date:
        if today < draft_date:
            return "pre_draft"
        elif today == draft_date:
            return "draft_day"
        else:
            return "post_draft"

    # No configured date: treat completed as post_draft, otherwise pre_draft.
    if raw_state == "completed":
        return "post_draft"
    return "pre_draft"


def build_draft_payload(draft_type: str) -> dict:
    """Build unified draft payload for keeper or prospect draft.

    Important: the prospect draft currently uses a separate season
    constant (PROSPECT_DRAFT_SEASON) so that Discord + API both read
    from the same state file (e.g. data/draft_state_prospect_2025.json).
    """
    season_dates = load_season_dates()
    configured_season = season_dates.get("season_year")

    # Use the prospect-specific season for prospect drafts so that the
    # website sees the same state that the Discord bot is mutating.
    if draft_type == "prospect":
        season = PROSPECT_DRAFT_SEASON
    else:
        season = configured_season

    # DraftManager will choose the appropriate state file based on type/season.
    mgr = DraftManager(draft_type=draft_type, season=season)
    state = mgr.state
    order = mgr.draft_order
    current_pick = mgr.get_current_pick()

    status = compute_draft_status(draft_type, state)
    draft_date = get_draft_date(draft_type)

    total_rounds = max((p["round"] for p in order), default=0)
    draft_order_teams = [p["team"] for p in order]

    picks = []
    for rec in state.get("picks_made", []):
        picks.append(
            {
                "pick_number": rec.get("pick"),
                "round": rec.get("round"),
                "team": rec.get("team"),
                "player_name": rec.get("player", ""),
                "position": rec.get("position", ""),
                "mlb_team": rec.get("mlb_team", ""),
                "picked_at": rec.get("timestamp"),
            }
        )

    return {
        "draft_id": f"fbp_{draft_type}_draft_{season}",
        "draft_type": draft_type,
        "season": season,
        "status": status,  # pre_draft | draft_day | active_draft | post_draft
        "scheduled_date": draft_date.isoformat() if draft_date else None,
        "current_round": current_pick["round"] if current_pick else None,
        "current_pick": current_pick["pick"] if current_pick else None,
        "current_team": current_pick["team"] if current_pick else None,
        "total_rounds": total_rounds,
        "pick_clock_seconds": 240,  # keep in sync with Discord timer (4 minutes)
        "clock_started_at": state.get("timer_started_at"),
        "draft_order": draft_order_teams,
        "picks": picks,
    }


def _get_prospect_draft_components():
    """Factory for prospect-draft components (fresh per request)."""
    draft_manager = DraftManager(draft_type="prospect", season=PROSPECT_DRAFT_SEASON)
    db = ProspectDatabase(season=PROSPECT_DRAFT_SEASON, draft_type="prospect")
    validator = PickValidator(db, draft_manager)
    return draft_manager, db, validator


async def _send_auction_log_message(content: str) -> None:
    """Post an auction log message to the test channel, if available."""
    try:
        channel = bot.get_channel(TEST_AUCTION_CHANNEL_ID)
        if channel:
            await channel.send(content)
    except Exception as exc:
        print(f"⚠️ Failed to send auction log message: {exc}")


def _commit_and_push(file_paths: list[str], message: str) -> None:
    """Best-effort helper to commit and push data updates.

    Uses GITHUB_TOKEN if available to push directly to the GitHub repo,
    otherwise falls back to `git push` with the existing remote config.

    Notes:
      * We capture stdout/stderr so Render logs show *why* a push failed.
      * We redact the raw token from any error output before printing.
    """
    import subprocess

    repo_root = REPO_ROOT

    try:
        # Stage files (if any)
        if file_paths:
            subprocess.run(
                ["git", "add", *file_paths],
                check=True,
                cwd=repo_root,
                capture_output=True,
                text=True,
            )

        # Commit (this may fail with code 1 if there are no changes)
        subprocess.run(
            ["git", "commit", "-m", message],
            check=True,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

        # Prepare push command
        token = os.getenv("GITHUB_TOKEN")
        if token:
            # Default to the actual bot repo name; allow override via GITHUB_REPO.
            repo = os.getenv("GITHUB_REPO", "zpressley/FBPTradeBot")
            username = os.getenv("GITHUB_USER", "x-access-token")
            remote_url = f"https://{username}:{token}@github.com/{repo}.git"
            push_cmd = ["git", "push", remote_url, "HEAD:main"]
        else:
            push_cmd = ["git", "push"]

        # Push current HEAD
        subprocess.run(
            push_cmd,
            check=True,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        print("✅ Git commit and push succeeded")

    except subprocess.CalledProcessError as exc:
        # Provide detailed context without leaking the raw token.
        token = os.getenv("GITHUB_TOKEN") or ""
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        combined = (stdout + "\n" + stderr).strip()
        if token:
            combined = combined.replace(token, "***")
        print(f"⚠️ Git commit/push failed with code {exc.returncode}.")
        if combined:
            print(combined)

    except Exception as exc:
        # Fallback for non-subprocess exceptions.
        print(f"⚠️ Git commit/push failed with unexpected error: {exc}")


@app.get("/api/auction/current")
async def get_current_auction(authorized: bool = Depends(verify_api_key)):
    """Return the current auction state for API consumers."""
    manager = AuctionManager()
    now = datetime.now(tz=ET)
    state = manager._load_or_initialize_auction(now)
    return state


@app.post("/api/auction/bid")
async def api_place_bid(
    payload: BidRequest,
    authorized: bool = Depends(verify_api_key),
):
    """Place an OB or CB bid from the website via Cloudflare Worker."""
    manager = AuctionManager()
    result = manager.place_bid(
        team=payload.team,
        prospect_id=payload.prospect_id,
        amount=payload.amount,
        bid_type=payload.bid_type,
    )

    if result.get("success"):
        _commit_and_push(["data/auction_current.json"],
                         f"Auction bid: {payload.bid_type} ${payload.amount} on {payload.prospect_id} by {payload.team}")

        bid = result.get("bid", {})
        is_ob = bid.get("bid_type", payload.bid_type) == "OB"
        header = "📣 Originating Bid Posted" if is_ob else "⚔️ Challenging Bid Placed"
        content = (
            f"{header}\n\n"
            f"🏷️ Team: {bid.get('team', payload.team)}\n"
            f"💰 Bid: ${bid.get('amount', payload.amount)}\n"
            f"🧢 Player: {bid.get('prospect_id', payload.prospect_id)}\n\n"
            f"Source: Website Portal"
        )
        bot.loop.create_task(_send_auction_log_message(content))

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

    return result


@app.post("/api/auction/match")
async def api_record_match(
    payload: MatchRequest,
    authorized: bool = Depends(verify_api_key),
):
    """Record an explicit Match / Forfeit decision from OB manager."""
    manager = AuctionManager()
    result = manager.record_match(
        team=payload.team,
        prospect_id=payload.prospect_id,
        decision=payload.decision,
        source=payload.source,
    )
    
    if result.get("success"):
        _commit_and_push(["data/auction_current.json"],
                         f"Auction match: {payload.decision} on {payload.prospect_id} by {payload.team}")
    
        match = result.get("match", {})
        decision = match.get("decision", payload.decision)
        emoji = "✅" if decision == "match" else "🚫"
        content = (
            f"{emoji} **OB Decision**\n"
            f"Team: `{match.get('team', payload.team)}`\n"
            f"Prospect: `{match.get('prospect_id', payload.prospect_id)}`\n"
            f"Decision: `{decision}` (via Website Portal)"
        )
        bot.loop.create_task(_send_auction_log_message(content))
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    
    return result


# ---- Prospect Draft APIs ----

@app.get("/api/draft/prospect/state")
async def get_prospect_draft_state(
    authorized: bool = Depends(verify_api_key),
):
    """Expose current prospect-draft state for the web UI."""
    draft_manager, _, _ = _get_prospect_draft_components()
    state = draft_manager.state
    current_pick = draft_manager.get_current_pick()

    return {
        "current_pick": current_pick,
        "status": state.get("status"),
        "draft_type": state.get("draft_type"),
        "season": state.get("season"),
        "current_pick_index": state.get("current_pick_index"),
        "team_slots": state.get("team_slots"),
        "picks_made": state.get("picks_made"),
    }


@app.post("/api/draft/prospect/validate")
async def api_validate_prospect_pick(
    payload: ProspectValidateRequest,
    authorized: bool = Depends(verify_api_key),
):
    """Validate a prospective pick for the current prospect draft."""
    draft_manager, _, validator = _get_prospect_draft_components()
    summary = validator.get_validation_summary(payload.team, payload.player)
    return summary


# ---- PAD APIs ----


@app.post("/api/pad/submit")
async def api_pad_submit(
    payload: PadSubmissionPayload,
    authorized: bool = Depends(verify_api_key),
):
    """Apply a single PAD submission from the website.

    The Worker forwards website PAD submissions here with X-API-Key. We
    delegate to pad.pad_processor.apply_pad_submission, which handles
    validation, data updates, and draft-order rebuilds. This endpoint
    also schedules a Discord announcement task.
    """
    print(
        "📥 Incoming PAD submission",
        {
            "team": payload.team,
            "season": payload.season,
            "expected_season": PAD_SEASON,
            "test_mode": PAD_TEST_MODE,
        },
    )

    if payload.season != PAD_SEASON:
        print("❌ PAD season mismatch", {"payload_season": payload.season, "PAD_SEASON": PAD_SEASON})
        raise HTTPException(status_code=400, detail="Season mismatch for PAD")

    # Live-test escape hatch mirrors the behavior in pad_processor: allow
    # a specific team to exercise the full PAD flow (including Discord)
    # without mutating JSON files or committing to Git.
    live_test_team = os.getenv("PAD_LIVE_TEST_TEAM")
    is_live_test = (not PAD_TEST_MODE) and bool(live_test_team) and live_test_team.upper() == payload.team.upper()

    try:
        result = apply_pad_submission(payload, PAD_TEST_MODE)
    except PadAlreadySubmittedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        # Validation / data-shape problems we want to surface clearly.
        print("❌ PAD ValueError while processing submission:")
        try:
            print("   payload=", payload.model_dump())
        except Exception:
            print("   (failed to dump payload)")
        print(f"   error={e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        import traceback
        print("❌ PAD processing error (unexpected exception):")
        try:
            print("   payload=", payload.model_dump())
        except Exception:
            print("   (failed to dump payload)")
        print(f"   PAD_TEST_MODE={PAD_TEST_MODE}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="PAD processing error")

    # Auto-commit PAD data files back to GitHub so the
    # bot repo (and downstream FBP Hub sync) stay in sync.
    if PAD_TEST_MODE:
        # Test mode: commit to *_test.json variants so we can safely
        # exercise the full git commit/push flow without touching
        # live data files.
        core_files = [
            "data/combined_players_test.json",
            "data/wizbucks_test.json",
            "data/player_log_test.json",
        ]
        _commit_and_push(
            core_files,
            f"PAD TEST: {result.team} submission ({result.season})",
        )
    elif not is_live_test:
        core_files = [
            "data/combined_players.json",
            "data/wizbucks.json",
            "data/player_log.json",
            "data/pad_submissions_2026.json",
            "data/draft_order_2026.json",
            "data/wizbucks_transactions.json",
        ]
        _commit_and_push(
            core_files,
            f"PAD: {result.team} submission ({result.season})",
        )
    else:
        # Live-test team: do not commit any data changes; this path is
        # for exercising the Discord announcement only.
        pass

    # Fire-and-forget Discord announcement; channel selection happens
    # inside the announce helper based on PAD_TEST_MODE.
    try:
        bot.loop.create_task(announce_pad_submission_to_discord(result, bot))
    except RuntimeError:
        # In unit tests / non-bot contexts, bot.loop may not be running.
        pass

    return {
        "ok": True,
        "timestamp": result.timestamp,
        "team": result.team,
        "season": result.season,
        "wb_spent": result.wb_spent,
        "wb_remaining": result.wb_remaining,
    }


# ---- Admin APIs ----


@app.post("/api/admin/update-player")
async def api_admin_update_player(
    payload: AdminPlayerUpdatePayload,
    authorized: bool = Depends(verify_api_key),
):
    """Apply a single admin player update and persist to JSON.

    This endpoint is designed to be called by the Cloudflare Worker that
    fronts the FBP Hub admin portal. It mirrors the PAD pattern by routing
    all data mutation through a pure helper and then committing core data
    files back to Git.
    """
    try:
        result = apply_admin_player_update(payload, test_mode=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        print("❌ Admin update error:", e)
        raise HTTPException(status_code=500, detail="Admin update error")

    core_files = [
        "data/combined_players.json",
        "data/wizbucks.json",
        "data/player_log.json",
    ]
    try:
        _commit_and_push(
            core_files,
            f"Admin update: {result['player'].get('name', payload.upid)}",
        )
    except Exception as exc:
        # Commit/push failures should not hide the fact that the data files
        # were already updated on disk.
        print("⚠️ Admin update git commit/push failed:", exc)

    return result


@app.post("/api/admin/delete-player")
async def api_admin_delete_player(
    payload: AdminDeletePlayerPayload,
    authorized: bool = Depends(verify_api_key),
):
    """Delete a player from combined_players and log the deletion."""
    try:
        result = apply_admin_delete_player(payload, test_mode=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        print("❌ Admin delete player error:", e)
        raise HTTPException(status_code=500, detail="Admin delete player error")

    core_files = [
        "data/combined_players.json",
        "data/player_log.json",
    ]
    try:
        _commit_and_push(
            core_files,
            f"Admin delete: {result['player'].get('name', payload.upid)}",
        )
    except Exception as exc:
        print("⚠️ Admin delete git commit/push failed:", exc)

    return result


@app.post("/api/admin/merge-players")
async def api_admin_merge_players(
    payload: AdminMergePlayersPayload,
    authorized: bool = Depends(verify_api_key),
):
    """Merge two players: source fields fill target's missing values, source is deleted."""
    try:
        result = apply_admin_merge_players(payload, test_mode=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        print("❌ Admin merge players error:", e)
        raise HTTPException(status_code=500, detail="Admin merge players error")

    core_files = [
        "data/combined_players.json",
        "data/player_log.json",
    ]
    try:
        _commit_and_push(
            core_files,
            f"Admin merge: {result.get('source_upid')} -> {result.get('target_upid')}",
        )
    except Exception as exc:
        print("⚠️ Admin merge git commit/push failed:", exc)

    return result


@app.post("/api/admin/wizbucks-adjustment")
async def api_admin_wizbucks_adjustment(
    payload: AdminWBAdjustmentPayload,
    authorized: bool = Depends(verify_api_key),
):
    """Apply a manual WizBucks adjustment and persist to wizbucks data files."""
    try:
        result = apply_admin_wb_adjustment(payload, test_mode=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        print("❌ Admin WB adjustment error:", e)
        raise HTTPException(status_code=500, detail="Admin WB adjustment error")

    core_files = [
        "data/wizbucks.json",
        "data/wizbucks_transactions.json",
    ]
    try:
        _commit_and_push(
            core_files,
            f"Admin WB: {result['team']} {result['amount']} ({result['installment']})",
        )
    except Exception as exc:
        print("⚠️ Admin WB git commit/push failed:", exc)

    return result


@app.get("/api/admin/player-log")
async def api_admin_player_log(
    limit: int = 50,
    update_type: str | None = None,
    authorized: bool = Depends(verify_api_key),
):
    """Return recent player_log entries for the admin portal.

    Entries are ordered newest-first, optionally filtered by update_type and
    truncated by limit.
    """
    try:
        with open("data/player_log.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = []
    except FileNotFoundError:
        data = []

    # Newest first
    data = list(reversed(data))

    if update_type:
        data = [rec for rec in data if rec.get("update_type") == update_type]

    if limit and limit > 0:
        data = data[:limit]

    return data


@app.get("/api/admin/wizbucks-balances")
async def api_admin_wizbucks_balances(
    authorized: bool = Depends(verify_api_key),
):
    """Return current WizBucks balances keyed by FBP team abbreviation.

    Uses managers.json to map abbreviations to franchise display names used
    as keys in wizbucks.json.
    """
    try:
        with open("data/wizbucks.json", "r", encoding="utf-8") as f:
            wizbucks = json.load(f)
        if not isinstance(wizbucks, dict):
            wizbucks = {}
    except FileNotFoundError:
        wizbucks = {}

    managers_cfg = load_managers_config() or {}
    teams_meta = managers_cfg.get("teams") or {}

    balances: dict[str, int] = {}
    for abbr, meta in teams_meta.items():
        if not isinstance(meta, dict):
            continue
        name = meta.get("name")
        if not name:
            continue
        if name in wizbucks:
            try:
                balances[abbr] = int(wizbucks.get(name, 0))
            except Exception:
                continue

    return {"balances": balances}


# ---- PAD Discord test helper ----


@app.post("/api/admin/pad-retro-discord/{team}")
async def api_admin_pad_retro_discord(
    team: str,
    authorized: bool = Depends(verify_api_key),
):
    """Replay a real PAD submission to Discord for a given team.

    This constructs a PadResult from existing data files (pad_submissions,
    wizbucks, player_log) and sends it to the live PAD channel. It does NOT
    mutate any JSON files or perform any git operations.
    """

    team_abbr = team.upper()

    # Load PAD submissions for slots + metadata
    try:
        with open("data/pad_submissions_2026.json", "r", encoding="utf-8") as f:
            submissions = json.load(f)
        if not isinstance(submissions, dict):
            submissions = {}
    except FileNotFoundError:
        submissions = {}

    rec = submissions.get(team_abbr)
    if not rec:
        raise HTTPException(status_code=404, detail=f"No PAD submission found for team {team_abbr}")

    season = int(rec.get("season", PAD_SEASON))
    timestamp = rec.get("timestamp") or datetime.now(tz=ET).isoformat()
    dc_slots = int(rec.get("dc_slots", 0))
    bc_slots = int(rec.get("bc_slots", 0))
    wb_spent = int(rec.get("wb_total_spend", 0))

    # Derive WizBucks remaining from wizbucks.json + managers.json mapping.
    wb_remaining = 0
    try:
        with open("data/wizbucks.json", "r", encoding="utf-8") as f:
            wizbucks = json.load(f)
        if not isinstance(wizbucks, dict):
            wizbucks = {}
    except FileNotFoundError:
        wizbucks = {}

    managers_cfg = load_managers_config() or {}
    teams_meta = managers_cfg.get("teams") or {}
    meta = teams_meta.get(team_abbr) or {}
    franchise_name = meta.get("name")
    if franchise_name and isinstance(wizbucks, dict):
        try:
            wb_remaining = int(wizbucks.get(franchise_name, 0))
        except Exception:
            wb_remaining = 0

    # Use player_log.json to reconstruct which prospects were DC/PC/BC vs dropped.
    dc_players = []
    pc_players = []
    bc_players = []
    dropped = []

    owner_name = franchise_name or team_abbr

    try:
        with open("data/player_log.json", "r", encoding="utf-8") as f:
            pl_data = json.load(f)
        if not isinstance(pl_data, list):
            pl_data = []
    except FileNotFoundError:
        pl_data = []

    for rec in pl_data:
        try:
            if rec.get("season") != season:
                continue
            if rec.get("event") != "26 PAD":
                continue
        except Exception:
            continue

        if (rec.get("owner") or "").strip() != owner_name:
            continue

        update_type = rec.get("update_type")
        name = rec.get("player_name") or ""

        if update_type in ("Purchase", "Blue Chip"):
            contract = (rec.get("contract") or "").strip()
            target = None
            if contract == "Development Contract":
                target = dc_players
            elif contract == "Purchased Contract":
                target = pc_players
            elif contract == "Blue Chip Contract":
                target = bc_players
            if target is not None:
                target.append({"name": name})
        elif update_type == "Drop":
            dropped.append({"name": name})

    # Build PadResult and send via Discord helper.
    result = PadResult(
        season=season,
        team=team_abbr,
        timestamp=timestamp,
        wb_spent=wb_spent,
        wb_remaining=wb_remaining,
        dc_players=dc_players,
        pc_players=pc_players,
        bc_players=bc_players,
        dc_slots=dc_slots,
        bc_slots=bc_slots,
        dropped_prospects=dropped,
    )

    # Force PAD_TEST_MODE=false during the announcement so the live channel
    # path is exercised regardless of env.
    old_flag = os.getenv("PAD_TEST_MODE")
    os.environ["PAD_TEST_MODE"] = "false"

    try:
        bot.loop.create_task(announce_pad_submission_to_discord(result, bot))
        await asyncio.sleep(2)
    finally:
        if old_flag is None:
            os.environ.pop("PAD_TEST_MODE", None)
        else:
            os.environ["PAD_TEST_MODE"] = old_flag

    return {
        "ok": True,
        "team": team_abbr,
        "season": season,
        "timestamp": timestamp,
        "dc_slots": dc_slots,
        "bc_slots": bc_slots,
        "wb_spent": wb_spent,
        "wb_remaining": wb_remaining,
        "counts": {
            "dc_players": len(dc_players),
            "pc_players": len(pc_players),
            "bc_players": len(bc_players),
            "dropped_prospects": len(dropped),
        },
    }


@app.post("/api/admin/pad-test-discord")
async def api_admin_pad_test_discord(
    team: str = "WAR",
    authorized: bool = Depends(verify_api_key),
):
    """Send a synthetic PAD Discord announcement for debugging.

    This endpoint does NOT touch any JSON data files or git. It simply
    constructs a fake PadResult and calls announce_pad_submission_to_discord
    with PAD_TEST_MODE forced to false, so that live-channel routing and
    permissions can be validated safely.
    """

    # Minimal synthetic payload; this is only for visual/permission testing.
    now_iso = datetime.now(tz=ET).isoformat()
    result = PadResult(
        season=PAD_SEASON,
        team=team,
        timestamp=now_iso,
        wb_spent=0,
        wb_remaining=0,
        dc_players=[{"name": "Test DC Prospect"}],
        pc_players=[{"name": "Test PC Prospect"}],
        bc_players=[{"name": "Test BC Prospect"}],
        dc_slots=1,
        bc_slots=1,
        dropped_prospects=[{"name": "Test Dropped Prospect"}],
    )

    # Force PAD_TEST_MODE=false for the duration of this call so that the
    # live PAD channel code path is exercised regardless of current env.
    old_flag = os.getenv("PAD_TEST_MODE")
    os.environ["PAD_TEST_MODE"] = "false"

    try:
        # Schedule the Discord send on the bot's event loop just like the
        # main PAD submit endpoint, so we don't await across threads.
        bot.loop.create_task(announce_pad_submission_to_discord(result, bot))
        # Give the task a brief window to run before returning.
        await asyncio.sleep(2)
    finally:
        if old_flag is None:
            # Restore to unset state
            os.environ.pop("PAD_TEST_MODE", None)
        else:
            os.environ["PAD_TEST_MODE"] = old_flag

    return {
        "ok": True,
        "team": team,
        "season": PAD_SEASON,
        "timestamp": now_iso,
    }


# ---- Unified Draft API ----

@app.get("/api/draft/active")
async def get_active_draft(
    draft_type: str = "keeper",  # 'keeper' or 'prospect'
    authorized: bool = Depends(verify_api_key),
):
    """Unified draft endpoint for website (keeper or prospect).

    Example:
      /api/draft/active?draft_type=keeper
      /api/draft/active?draft_type=prospect
    """
    if draft_type not in ("keeper", "prospect"):
        raise HTTPException(status_code=400, detail="draft_type must be 'keeper' or 'prospect'")

    payload = build_draft_payload(draft_type)
    return payload


@app.get("/api/draft/config")
async def get_draft_config(
    authorized: bool = Depends(verify_api_key),
):
    """Return scheduled dates and season metadata for keeper/prospect drafts.

    This is a small helper for the website to display labels like
    "Keeper Draft: March 8, 2026" and "Prospect Draft: March 10, 2026".
    """
    data = load_season_dates()
    season = data.get("season_year")

    keeper_date = get_draft_date("keeper")
    prospect_date = get_draft_date("prospect")

    return {
        "season": season,
        "keeper": {
            "scheduled_date": keeper_date.isoformat() if keeper_date else None,
        },
        "prospect": {
            "scheduled_date": prospect_date.isoformat() if prospect_date else None,
        },
    }


# ---- Draft Board APIs ----

@app.get("/api/draft/boards/{team}")
async def get_draft_board(
    team: str,
    authorized: bool = Depends(verify_api_key),
):
    """Return the current personal draft board for a team."""
    manager = BoardManager(season=PROSPECT_DRAFT_SEASON)
    board = manager.get_board(team)
    return {
        "team": team,
        "board": board,
        "max_size": manager.MAX_BOARD_SIZE,
    }


@app.post("/api/draft/boards/{team}")
async def update_draft_board(
    team: str,
    payload: BoardUpdateRequest,
    authorized: bool = Depends(verify_api_key),
):
    """Replace a team's personal draft board with the provided list."""
    if payload.team != team:
        raise HTTPException(status_code=400, detail="Team in path and body must match")

    manager = BoardManager(season=PROSPECT_DRAFT_SEASON)

    if len(payload.board) > manager.MAX_BOARD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Board too large (max {manager.MAX_BOARD_SIZE})",
        )

    existing = manager.get_board(team)
    if existing:
        if set(existing) != set(payload.board):
            manager.boards[team] = payload.board[: manager.MAX_BOARD_SIZE]
            manager.save_boards()
        else:
            ok, msg = manager.reorder_board(team, payload.board)
            if not ok:
                raise HTTPException(status_code=400, detail=msg)
    else:
        manager.boards[team] = payload.board[: manager.MAX_BOARD_SIZE]
        manager.save_boards()

    return {
        "team": team,
        "board": manager.get_board(team),
        "max_size": manager.MAX_BOARD_SIZE,
    }


# ---- Orchestrate Both ----
async def start_bot():
    """Start Discord bot with error handling"""
    try:
        print(f"🤖 Starting Discord bot...")
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        print("⏸️ Received interrupt signal")
        await bot.close()
    except Exception as e:
        print(f"❌ Bot error: {e}")
        raise

def run_server():
    """Run FastAPI server (blocking)"""
    print(f"🌐 Starting FastAPI server on port {PORT}...")
    
    config = uvicorn.Config(
        app, 
        host="0.0.0.0", 
        port=PORT,
        log_level="info",
        access_log=True
    )
    
    server = uvicorn.Server(config)
    server.run()

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 FBP Trade Bot - Production Mode (Full API)")
    print("=" * 60)
    print(f"   Port: {PORT}")
    print(f"   Discord Token: {'✅ Set' if TOKEN else '❌ Missing'}")
    print(f"   API Key: {'✅ Set' if API_KEY else '⚠️ Not set (auth disabled)'}")
    print(f"   Google Creds: {'✅ Set' if google_creds else '⚠️ Not set'}")
    print(f"   Yahoo Token: {'✅ Set' if yahoo_token else '⚠️ Not set'}")
    print("=" * 60)
    print()
    
    # Start FastAPI server in background thread (daemon)
    server_thread = threading.Thread(target=run_server, daemon=True, name="FastAPI-Server")
    server_thread.start()
    print("✅ FastAPI server thread started")
    
    # Run Discord bot in main thread (blocks until shutdown)
    try:
        print("🤖 Starting Discord bot (main thread)...")
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
    finally:
        print("✅ Cleanup complete")
