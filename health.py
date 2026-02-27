import os
import asyncio
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
import threading
import sys
import json
from collections import deque
import time
import traceback
import re
import shutil

import discord
from discord.ext import commands
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import JSONResponse
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
from self_service.contract_purchase_processor import (
    ContractPurchasePayload,
    apply_contract_purchase,
)
from api_admin_bulk import router as admin_bulk_router, set_bulk_bot_reference, set_bulk_commit_fn
from api_draft_pool import router as draft_pool_router
from api_draft_pick_request import router as draft_pick_router, set_bot_reference
from api_buyin import router as buyin_router, set_buyin_bot_reference, set_buyin_commit_fn
from api_trade import router as trade_router, set_trade_bot_reference, set_trade_commit_fn
from api_settings import router as settings_router, set_settings_commit_fn
from api_notes import router as notes_router, set_notes_commit_fn
from data_lock import DATA_LOCK

# Load environment variables
load_dotenv()


def configure_git() -> None:
    """Configure git identity and remote for deployments.

    Safe to run everywhere. If git is not installed (or not on PATH), logs and
    continues.
    """
    if shutil.which("git") is None:
        print("⚠️ git not found on PATH — commit/push disabled until git is installed")
        return

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
            print("⚠️ GITHUB_TOKEN not set — git push will likely fail (set GITHUB_TOKEN in Railway/Render)")
    except Exception as exc:
        print(f"❌ Git configuration error: {exc}")


# Configure git once on process start (safe no-op locally)
configure_git()

# ---- Commit Queue for Batching ----
# This prevents concurrent git operations from colliding
_commit_queue = deque()
_commit_queue_lock = threading.Lock()
_commit_worker_thread = None
_commit_worker_running = False

def _start_commit_worker():
    """Start background thread that processes commit queue."""
    global _commit_worker_thread, _commit_worker_running
    if _commit_worker_thread is not None and _commit_worker_thread.is_alive():
        return
    
    _commit_worker_running = True
    _commit_worker_thread = threading.Thread(target=_commit_worker_loop, daemon=True)
    _commit_worker_thread.start()
    print("✅ Commit queue worker started")

def _commit_worker_loop():
    """Background worker that batches and commits changes every few seconds."""
    batch_interval = 3.0  # seconds
    
    while _commit_worker_running:
        time.sleep(batch_interval)
        
        # Collect pending commits
        pending = []
        with _commit_queue_lock:
            while _commit_queue:
                pending.append(_commit_queue.popleft())
        
        if not pending:
            continue
        
        # Batch all files and messages
        all_files = set()
        messages = []
        for files, msg in pending:
            all_files.update(files)
            messages.append(msg)
        
        # Create batched commit message
        if len(messages) == 1:
            commit_msg = messages[0]
        else:
            commit_msg = f"Batch commit: {len(messages)} operations\n\n" + "\n".join(f"- {m}" for m in messages[:10])
            if len(messages) > 10:
                commit_msg += f"\n... and {len(messages) - 10} more"
        
        # Execute the commit
        try:
            _execute_git_commit(list(all_files), commit_msg)
        except Exception as exc:
            print(f"⚠️ Batch commit failed: {exc}")

def _execute_git_commit(file_paths: list[str], message: str) -> None:
    """Execute a single git commit + push operation (called by worker thread)."""
    import subprocess

    if shutil.which("git") is None:
        raise RuntimeError("git not found on PATH")

    repo_root = REPO_ROOT
    if not repo_root or not os.path.isdir(repo_root):
        raise RuntimeError(f"REPO_ROOT does not exist: {repo_root}")

    def _ts() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _redact(s: str) -> str:
        token = os.getenv("GITHUB_TOKEN") or ""
        return s.replace(token, "***") if token and s else s

    def _log(msg: str, data: dict | None = None) -> None:
        try:
            if data is None:
                print(f"[{_ts()}] {msg}")
            else:
                print(f"[{_ts()}] {msg} {json.dumps(data, default=str)}")
        except Exception:
            print(f"[{_ts()}] {msg}")

    def _safe_cmd(cmd: list[str]) -> list[str]:
        # Avoid leaking GITHUB_TOKEN in logs (it can be embedded in remote URLs)
        return [_redact(str(part)) for part in (cmd or [])]

    def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        _log("GIT_CMD", {"cmd": _safe_cmd(cmd)})
        return subprocess.run(
            cmd,
            check=check,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

    token = os.getenv("GITHUB_TOKEN")
    remote_url = None
    if token:
        repo = os.getenv("GITHUB_REPO", "zpressley/FBPTradeBot")
        username = os.getenv("GITHUB_USER", "x-access-token")
        remote_url = f"https://{username}:{token}@github.com/{repo}.git"

    push_cmd = ["git", "push", remote_url, "HEAD:main"] if remote_url else ["git", "push"]
    fetch_remote = remote_url if remote_url else "origin"

    # De-dupe but preserve order
    seen = set()
    file_paths = [p for p in (file_paths or []) if p and not (p in seen or seen.add(p))]

    # Only stage files that exist (avoid git add pathspec errors)
    existing_paths = [p for p in file_paths if os.path.exists(os.path.join(repo_root, p))]
    missing_paths = [p for p in file_paths if p not in existing_paths]

    _log(
        "GIT_COMMIT_START",
        {
            "repo_root": repo_root,
            "message_head": (message or "").splitlines()[0] if message else "",
            "file_count": len(file_paths),
            "existing_files": existing_paths,
            "missing_files": missing_paths,
        },
    )

    # If the build system provided source without a .git directory (common on some
    # platforms), bootstrap a git repo so commit/push can work.
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        if not token:
            raise RuntimeError(
                "No .git directory found and GITHUB_TOKEN is not set — cannot bootstrap git repo"
            )

        init_remote = remote_url or f"https://github.com/{os.getenv('GITHUB_REPO', 'zpressley/FBPTradeBot')}.git"

        # Preserve pending file changes across init/reset
        saved_files: dict[str, bytes] = {}
        for p in existing_paths:
            try:
                full = os.path.join(repo_root, p)
                with open(full, "rb") as f:
                    saved_files[p] = f.read()
            except Exception:
                continue

        _log(
            "GIT_BOOTSTRAP",
            {
                "repo_root": repo_root,
                "has_git_dir": False,
                "remote": _redact(init_remote),
            },
        )

        _run(["git", "init"])
        _run(["git", "remote", "remove", "origin"], check=False)
        _run(["git", "remote", "add", "origin", init_remote])
        _run(["git", "fetch", "origin", "main"])
        _run(["git", "checkout", "-B", "main", "FETCH_HEAD"])

        # Restore pending file changes
        for rel, blob in saved_files.items():
            try:
                full = os.path.join(repo_root, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as f:
                    f.write(blob)
            except Exception:
                pass

    # Best-effort: ensure we are on main (avoid detached HEAD from prior failures)
    try:
        _run(["git", "checkout", "main"], check=False)
    except Exception:
        pass

    def _stage_and_commit() -> bool:
        """Return True if a commit was created."""
        if existing_paths:
            _run(["git", "add", *existing_paths])

        staged = _run(["git", "diff", "--cached", "--name-only"], check=False)
        staged_files = [ln.strip() for ln in (staged.stdout or "").splitlines() if ln.strip()]
        if not staged_files:
            _log("GIT_NOOP", {"reason": "nothing staged to commit"})
            return False

        try:
            _run(["git", "commit", "-m", message])
        except subprocess.CalledProcessError as exc:
            combined = _redact(((exc.stdout or "") + "\n" + (exc.stderr or "")).strip())
            if "nothing to commit" in combined.lower():
                _log("GIT_NOOP", {"reason": "nothing to commit after staging"})
                return False
            _log("GIT_COMMIT_FAILED", {"code": exc.returncode, "output": combined[-1500:]})
            raise

        return True

    def _push_with_retry(saved_files: dict[str, bytes] | None = None) -> None:
        try:
            _run(push_cmd)
            _log("GIT_PUSH_OK")
            return
        except subprocess.CalledProcessError as exc:
            combined = _redact(((exc.stdout or "") + "\n" + (exc.stderr or "")).strip())
            retryable = any(
                s in combined.lower()
                for s in [
                    "fetch first",
                    "non-fast-forward",
                    "updates were rejected",
                    "rejected",
                ]
            )

            if not retryable:
                _log("GIT_PUSH_FAILED", {"code": exc.returncode, "output": combined[-2000:]})
                raise

            _log("GIT_PUSH_RETRY", {"reason": "non-fast-forward", "output": combined[-1200:]})

            # Conflict-resistant retry:
            # - Fetch remote main
            # - Hard reset local main to FETCH_HEAD
            # - Re-apply ONLY the file contents we intended to commit
            # - Commit + push
            #
            # IMPORTANT: Hold DATA_LOCK during the reset+restore+commit so
            # that no API endpoint reads stale (post-reset, pre-restore)
            # data from disk.
            if saved_files is None:
                saved_files = {}
                for p in existing_paths:
                    try:
                        full = os.path.join(repo_root, p)
                        with open(full, "rb") as f:
                            saved_files[p] = f.read()
                    except Exception:
                        continue

            with DATA_LOCK:
                # Best-effort cleanup of any interrupted operations
                subprocess.run(["git", "rebase", "--abort"], cwd=repo_root, capture_output=True, text=True)
                subprocess.run(["git", "merge", "--abort"], cwd=repo_root, capture_output=True, text=True)
                subprocess.run(["git", "cherry-pick", "--abort"], cwd=repo_root, capture_output=True, text=True)
                subprocess.run(["git", "checkout", "main"], cwd=repo_root, capture_output=True, text=True)

                _run(["git", "fetch", fetch_remote, "main"])
                _run(["git", "reset", "--hard", "FETCH_HEAD"])

                # Restore saved file contents
                for rel, blob in (saved_files or {}).items():
                    try:
                        full = os.path.join(repo_root, rel)
                        os.makedirs(os.path.dirname(full), exist_ok=True)
                        with open(full, "wb") as f:
                            f.write(blob)
                    except Exception as wexc:
                        _log("GIT_RESTORE_FILE_FAILED", {"path": rel, "error": str(wexc)})

                # Re-stage + commit while still holding the lock
                if existing_paths:
                    _run(["git", "add", *existing_paths])

                staged = _run(["git", "diff", "--cached", "--name-only"], check=False)
                if not (staged.stdout or "").strip():
                    _log("GIT_NOOP", {"reason": "retry produced no staged changes"})
                    return

                _run(["git", "commit", "-m", message])

            # Push outside the lock (network I/O can be slow)
            _run(push_cmd)
            _log("GIT_PUSH_OK", {"after": "hard-reset-reapply"})

    try:
        did_commit = _stage_and_commit()
        if not did_commit:
            return

        _push_with_retry()
        _log("GIT_BATCH_COMMIT_PUSH_OK")

    except subprocess.CalledProcessError as exc:
        combined = _redact(((exc.stdout or "") + "\n" + (exc.stderr or "")).strip())
        _log("GIT_OP_FAILED", {"code": exc.returncode, "output": combined[-2000:]})

    except Exception as exc:
        _log("GIT_OP_FAILED", {"error": str(exc)})

ET = ZoneInfo("US/Eastern")
PROSPECT_DRAFT_SEASON = 2026
SEASON_DATES_PATH = "config/season_dates.json"

# Draft endpoints are polled frequently by the website. Mark responses as
# explicitly uncacheable so any intermediate proxies/CDNs don't serve stale
# draft state.
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _json_no_store(payload: dict) -> JSONResponse:
    return JSONResponse(content=payload, headers=_NO_STORE_HEADERS)

# PAD (Prospect Allocation Day) config
PAD_SEASON = PROSPECT_DRAFT_SEASON
PAD_TEST_MODE = os.getenv("PAD_TEST_MODE", "false").lower() == "true"
PAD_TEST_CHANNEL_ID = int(os.getenv("PAD_TEST_CHANNEL_ID", "0"))  # test PAD announcements
PAD_LIVE_PAD_CHANNEL_ID = int(os.getenv("PAD_LIVE_PAD_CHANNEL_ID", "0"))  # live PAD announcements

TEST_AUCTION_CHANNEL_ID = 1197200421639438537  # test channel for auction logs
ADMIN_LOG_CHANNEL_ID = 1079466810375688262  # channel for admin change notifications
TRANSACTION_LOG_CHANNEL_ID = 1089979265619083444  # channel for manager transactions
ADMIN_TASKS_CHANNEL_ID = 875594022033436683  # daily processing summary tasks

def _resolve_repo_root() -> str:
    """Resolve the repo root for git operations.

    Render historically used /opt/render/project/src. Railway/Nixpacks typically
    runs from the app directory (often /app). We prefer an explicit REPO_ROOT if
    provided; otherwise choose a reasonable existing directory.
    """
    env_root = os.getenv("REPO_ROOT")
    if env_root and os.path.isdir(env_root):
        return env_root

    candidates = [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
        "/app",
        "/opt/render/project/src",
    ]

    # Prefer a directory that actually has a .git folder.
    for c in candidates:
        if c and os.path.isdir(c) and os.path.isdir(os.path.join(c, ".git")):
            return c

    for c in candidates:
        if c and os.path.isdir(c):
            return c

    return os.getcwd()


# Repo root used for git operations (override via env)
REPO_ROOT = _resolve_repo_root()

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

    # Allow web pick request API to access the running bot instance
    try:
        set_bot_reference(bot)
    except Exception as exc:
        print(f"⚠️ Failed to set bot reference for draft pick API: {exc}")
    
    # Allow bulk admin API to send Discord notifications and commit/push
    try:
        set_bulk_bot_reference(bot)
        set_bulk_commit_fn(_commit_and_push)
    except Exception as exc:
        print(f"⚠️ Failed to set bot reference for bulk admin API: {exc}")
    
    # Allow buy-in API to send Discord notifications and commit/push
    try:
        set_buyin_bot_reference(bot)
        set_buyin_commit_fn(_commit_and_push)
    except Exception as exc:
        print(f"⚠️ Failed to set bot reference for buy-in API: {exc}")

    # Allow trade API to send Discord messages and commit/push trade persistence
    try:
        set_trade_bot_reference(bot)
        set_trade_commit_fn(_commit_and_push)
    except Exception as exc:
        print(f"⚠️ Failed to set bot reference for trade API: {exc}")

    # Allow settings API to commit/push team_colors.json updates
    try:
        set_settings_commit_fn(_commit_and_push)
    except Exception as exc:
        print(f"⚠️ Failed to set commit fn for settings API: {exc}")
    
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
    try:
        await asyncio.wait_for(bot.tree.sync(), timeout=10)
        print("✅ Slash commands synced")
    except asyncio.TimeoutError:
        print("⚠️ Slash command sync timed out (10s) — using previously synced commands")
    except Exception as exc:
        print(f"⚠️ Slash command sync failed: {exc} — using previously synced commands")

# ---- FastAPI Web Server ----
app = FastAPI()

# Include admin bulk operations router
app.include_router(admin_bulk_router)
# Prospect draft pool and web pick request routers
app.include_router(draft_pool_router)
app.include_router(draft_pick_router)
# Buy-in purchase/refund router
app.include_router(buyin_router)
# Trade portal router
app.include_router(trade_router)
# Settings router (team colors)
app.include_router(settings_router)
# Manager notes router
app.include_router(notes_router)


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


def _normalize_clock_started_at_to_et(raw: str | None) -> str | None:
    """Return a timezone-aware ISO timestamp in US/Eastern for the website.

    Draft state currently persists timer_started_at as a naive ISO timestamp.
    Browsers interpret naive ISO timestamps as *local time*, which causes the
    draft clock to appear "in the future" for users outside the bot's timezone.

    This function normalizes the API field only (no state mutation):
    - If raw has no tzinfo, assume it is UTC.
    - Convert to US/Eastern and return ISO with an explicit offset.
    """
    if not raw:
        return None

    try:
        s = str(raw).strip()
        # datetime.fromisoformat doesn't accept trailing 'Z'
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(ET).isoformat()
    except Exception:
        # Fall back to the raw value if parsing fails.
        return str(raw)


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
                "upid": rec.get("upid", ""),
                # 0-based overall index into draft_order (stable even if
                # per-round pick numbers reset each round).
                "pick_index": rec.get("pick_index"),
            }
        )

    # Clock duration can vary per pick (forklift mode). Persisted by the bot as
    # state.timer_duration_seconds.
    pick_clock_seconds = state.get("timer_duration_seconds") or 240

    # Forklift teams are stored separately so admins can toggle them without
    # mutating draft_state.
    forklift_teams = []
    try:
        forklift_path = f"data/forklift_mode_{draft_type}_{season}.json"
        if os.path.exists(forklift_path):
            with open(forklift_path, "r", encoding="utf-8") as f:
                forklift_state = json.load(f) or {}
            forklift_teams = list((forklift_state.get("forklift_teams") or []))
    except Exception:
        forklift_teams = []

    current_pick_index = state.get("current_pick_index")
    try:
        current_pick_index_int = int(current_pick_index) if current_pick_index is not None else None
    except Exception:
        current_pick_index_int = None

    current_pick_overall = (current_pick_index_int + 1) if (current_pick and current_pick_index_int is not None) else None

    return {
        "draft_id": f"fbp_{draft_type}_draft_{season}",
        "draft_type": draft_type,
        "season": season,
        "test_mode": bool(getattr(mgr, "test_mode", False)),
        "status": status,  # pre_draft | draft_day | active_draft | post_draft
        # Raw DraftManager.state.status so the website can distinguish paused vs active.
        "raw_status": state.get("status", "not_started"),
        "scheduled_date": draft_date.isoformat() if draft_date else None,
        "last_updated": state.get("last_updated"),
        # 0-based index of the current pick in the canonical draft_order list.
        "current_pick_index": current_pick_index_int,
        # 1-based overall pick number for display (stable even when per-round
        # pick numbers reset each round).
        "current_pick_overall": current_pick_overall,
        "current_round": current_pick["round"] if current_pick else None,
        "current_pick": current_pick["pick"] if current_pick else None,
        "current_team": current_pick["team"] if current_pick else None,
        "total_rounds": total_rounds,
        "pick_clock_seconds": int(pick_clock_seconds),
        "clock_started_at": _normalize_clock_started_at_to_et(state.get("timer_started_at")),
        "forklift_teams": forklift_teams,
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


async def _send_admin_log_message(content: str) -> None:
    """Post an admin change notification to the admin log channel."""
    try:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            await channel.send(content)
    except Exception as exc:
        print(f"⚠️ Failed to send admin log message: {exc}")


async def _send_transaction_log_message(content: str) -> None:
    """Post a manager transaction notification to the transactions channel."""
    try:
        channel = bot.get_channel(TRANSACTION_LOG_CHANNEL_ID)
        if channel:
            await channel.send(content)
    except Exception as exc:
        print(f"⚠️ Failed to send transaction log message: {exc}")


async def _send_admin_tasks_message(content: str) -> None:
    """Post a daily processing task summary to the admin tasks channel."""
    try:
        channel = bot.get_channel(ADMIN_TASKS_CHANNEL_ID)
        if channel:
            await channel.send(content)
    except Exception as exc:
        print(f"⚠️ Failed to send admin tasks message: {exc}")


def _commit_and_push(file_paths: list[str], message: str) -> None:
    """Queue a commit operation for batching.

    Instead of committing immediately, add to queue processed by background worker.
    This prevents concurrent git operations from colliding.
    """
    with _commit_queue_lock:
        _commit_queue.append((file_paths, message))

    # Render visibility: log enqueued operations so we can correlate later git failures.
    try:
        head = (message or "").splitlines()[0] if message else ""
        print(
            "🧾 Commit queued",
            {
                "message": head,
                "file_count": len(file_paths or []),
                "files": list(file_paths or [])[:10],
            },
        )
    except Exception:
        pass


# Set commit functions at module level so API write operations work even
# when the Discord bot hasn't connected (e.g. Cloudflare rate-limit).
# Bot references stay in on_ready() since they need the connected bot.
try:
    set_settings_commit_fn(_commit_and_push)
except Exception:
    pass

try:
    set_notes_commit_fn(_commit_and_push)
except Exception:
    pass

try:
    set_trade_commit_fn(_commit_and_push)
except Exception:
    pass

try:
    set_bulk_commit_fn(_commit_and_push)
except Exception:
    pass

try:
    set_buyin_commit_fn(_commit_and_push)
except Exception:
    pass


def _resolve_prospect_name(prospect_id: str) -> str:
    """Resolve a UPID to a player name for readable Discord messages."""
    try:
        with open("data/combined_players.json", "r", encoding="utf-8") as f:
            players = json.load(f)
        for p in players:
            if str(p.get("upid", "")) == str(prospect_id):
                return p.get("name", prospect_id)
    except Exception:
        pass
    return str(prospect_id)


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
        prospect_name = _resolve_prospect_name(bid.get("prospect_id", payload.prospect_id))
        header = "📣 Originating Bid Posted" if is_ob else "⚔️ Challenging Bid Placed"
        content = (
            f"{header}\n\n"
            f"🏷️ Team: {bid.get('team', payload.team)}\n"
            f"💰 Bid: ${bid.get('amount', payload.amount)}\n"
            f"🧢 Player: {prospect_name}\n\n"
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
        prospect_name = _resolve_prospect_name(match.get("prospect_id", payload.prospect_id))
        emoji = "✅" if decision == "match" else "🚫"
        content = (
            f"{emoji} **OB Decision**\n"
            f"Team: `{match.get('team', payload.team)}`\n"
            f"Prospect: {prospect_name}\n"
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


# ---- Manager Self-Service APIs ----


@app.post("/api/manager/contract-purchase")
async def api_manager_contract_purchase(
    payload: ContractPurchasePayload,
    authorized: bool = Depends(verify_api_key),
):
    """Manager self-service contract purchase (DC→PC/BC, PC→BC).

    This is the endpoint used by the dashboard "BUY CONTRACTS" modal.

    Side-effects:
      * Updates player contract_type (only)
      * Deducts WizBucks
      * Appends wizbucks_transactions ledger entry
      * Appends player_log snapshot
    """

    try:
        result = apply_contract_purchase(payload, test_mode=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        print("❌ Contract purchase error:", e)
        raise HTTPException(status_code=500, detail="Contract purchase error")

    core_files = [
        "data/combined_players.json",
        "data/wizbucks.json",
        "data/player_log.json",
        "data/wizbucks_transactions.json",
    ]

    player_name = (result.get("player") or {}).get("name") or payload.upid
    upgrade_cost = result.get("cost")
    commit_msg = f"Contract purchase: {player_name} ({payload.team})"

    # Commit/push is best-effort; it should not prevent the transaction
    # from being logged to Discord.
    try:
        _commit_and_push(core_files, commit_msg)
    except Exception as exc:
        print("⚠️ Contract purchase git commit/push failed:", exc)

    # Discord notification to the transactions channel in the requested format.
    try:
        player_rec = result.get("player") or {}
        player_team = player_rec.get("team") or ""
        player_pos = player_rec.get("position") or ""
        manager_name = player_rec.get("manager") or payload.team
        timestamp = datetime.now(tz=ET).strftime("%Y-%m-%d %I:%M %p ET")

        discord_msg = (
            f"**Contract Purchase: {player_rec.get('contract_type') or payload.new_contract_type}**\n"
            f"Player: {player_name} ({player_team} - {player_pos})\n"
            f"Manager: {manager_name}\n"
            f"{timestamp}"
        )
        bot.loop.create_task(_send_transaction_log_message(discord_msg))
    except Exception as exc:
        print("⚠️ Failed to send contract purchase Discord message:", exc)

    return {
        "player": result.get("player"),
        "wizbucks_balance": result.get("wizbucks_balance"),
        "cost": result.get("cost"),
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
    commit_msg = f"Admin update: {result['player'].get('name', payload.upid)}"
    try:
        _commit_and_push(core_files, commit_msg)

        # Send Discord notification with a mini "diff" so admins can audit changes.
        player_name = result['player'].get('name', 'Unknown')
        upid = result['player'].get('upid', payload.upid)

        def _fmt(v):
            if v is None or v == "":
                return "∅"
            s = str(v)
            return s if len(s) <= 60 else (s[:57] + "...")

        change_lines = []
        changes = result.get("changes") or {}
        # stable ordering so messages are predictable
        for field in sorted(changes.keys()):
            ch = changes.get(field) or {}
            before = _fmt(ch.get("from"))
            after = _fmt(ch.get("to"))
            if before == after:
                continue
            change_lines.append(f"- `{field}`: {before} → {after}")

        # WizBucks delta (if included as part of this update)
        wb_before = result.get("wizbucks_balance_before")
        wb_after = result.get("wizbucks_balance")
        if wb_before is not None and wb_after is not None and wb_before != wb_after:
            change_lines.append(f"- `wizbucks`: {wb_before} → {wb_after}")

        # Keep messages readable / under Discord limits.
        max_lines = 12
        more = 0
        if len(change_lines) > max_lines:
            more = len(change_lines) - max_lines
            change_lines = change_lines[:max_lines]

        changes_block = "\n".join(change_lines) if change_lines else "- (no changes detected)"
        if more:
            changes_block += f"\n- (+{more} more)"

        discord_msg = (
            f"📝 **Admin Update**\n\n"
            f"👤 Player: **{player_name}** (UPID {upid})\n"
            f"🔎 Changes:\n{changes_block}\n"
            f"💾 Source: Website Admin Portal"
        )
        bot.loop.create_task(_send_admin_log_message(discord_msg))

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
    commit_msg = f"Admin delete: {result['player'].get('name', payload.upid)}"
    try:
        _commit_and_push(core_files, commit_msg)
        # Send Discord notification
        player_name = result['player'].get('name', 'Unknown')
        upid = result.get('upid', payload.upid)
        discord_msg = (
            f"🗑️ **Admin Delete**\n\n"
            f"👤 Player: **{player_name}** (UPID {upid})\n"
            f"💾 Source: Website Admin Portal"
        )
        bot.loop.create_task(_send_admin_log_message(discord_msg))
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
    commit_msg = f"Admin merge: {result.get('source_upid')} -> {result.get('target_upid')}"
    try:
        _commit_and_push(core_files, commit_msg)
        # Send Discord notification
        source_name = result.get('source_name', 'Unknown')
        target_name = result.get('target_name', 'Unknown')
        source_upid = result.get('source_upid', '?')
        target_upid = result.get('target_upid', '?')
        discord_msg = f"🔀 **Admin Merge**\n\n📥 Kept: **{target_name}** (UPID {target_upid})\n🗑️ Deleted: **{source_name}** (UPID {source_upid})\n💾 Source: Website Admin Portal"
        bot.loop.create_task(_send_admin_log_message(discord_msg))
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
    commit_msg = f"Admin WB: {result['team']} {result['amount']} ({result['installment']})"
    try:
        _commit_and_push(core_files, commit_msg)
        # Send Discord notification
        team = result['team']
        amount = result['amount']
        installment = result['installment']
        new_balance = result.get('new_balance', 'N/A')
        sign = '+' if amount >= 0 else ''
        discord_msg = f"💰 **Admin WizBucks Adjustment**\n\n🏆 Team: **{team}**\n💸 Amount: **{sign}{amount}**\n📅 Installment: {installment}\n📊 New Balance: {new_balance}\n💾 Source: Website Admin Portal"
        bot.loop.create_task(_send_admin_log_message(discord_msg))
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


# ---- Daily Processing Summary ----

def _load_json_safe(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if data is not None else default
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _is_wb_item(item: str) -> bool:
    if not isinstance(item, str):
        return False
    s = item.lower()
    return "wb" in s


def _parse_wb_amount(item: str) -> int:
    import re
    try:
        m = re.search(r"(\d+)", item)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def _extract_name(raw: str) -> str:
    # Best-effort: strip leading position codes and trailing brackets
    try:
        name = raw
        # Remove leading positions like "OF ", "SP ", etc.
        if " " in name:
            parts = name.split(" ", 1)
            if len(parts[0]) <= 3 and parts[0].isalpha():
                name = parts[1]
        # Truncate at first bracket
        if "[" in name:
            name = name.split("[")[0].strip()
        return name.strip()
    except Exception:
        return raw


def _build_processing_summary_lines(effective_on: date) -> list[str]:
    today_iso = effective_on.isoformat()
    lines: list[str] = []
    header = f"🗓️ Processing Tasks for {today_iso} (6:00 AM ET)"
    lines.append(header)
    lines.append("")

    # Pending trades scheduled for today
    trades = _load_json_safe("data/pending_trades.json", default=[])
    todays = [t for t in trades if isinstance(t, dict) and t.get("effective_on") == today_iso and t.get("status") in ("approved_awaiting_processing", "scheduled")]

    if todays:
        lines.append("🔁 Trades to Apply:")
        for t in todays:
            teams = [str(x) for x in (t.get("teams") or [])]
            assets = t.get("assets") or {}
            trade_id = t.get("trade_id") or "(no id)"
            lines.append(f"• Trade {trade_id}: {' ↔ '.join(teams) if teams else ''}")
            # Player moves and WB transfers
            # Assume assets lists what each team RECEIVES
            # Player moves
            for team, received in (assets.items() if isinstance(assets, dict) else []):
                other = [tt for tt in teams if tt != team]
                recv_players = [it for it in received if isinstance(it, str) and not _is_wb_item(it)]
                for p in recv_players:
                    pname = _extract_name(p)
                    src = other[0] if len(other) == 1 else (other or ["?"])[0]
                    lines.append(f"   - Move {pname}: {src} → {team}")
            # WB ledger
            for team, received in (assets.items() if isinstance(assets, dict) else []):
                for it in received:
                    if isinstance(it, str) and _is_wb_item(it):
                        amt = _parse_wb_amount(it)
                        if amt > 0:
                            if len(teams) == 2:
                                counter = [tt for tt in teams if tt != team][0]
                                lines.append(f"   - WB: Credit {team} +${amt}, Debit {counter} -${amt}")
                            else:
                                lines.append(f"   - WB: Credit {team} +${amt} (counterparty debit)")
        lines.append("")
    else:
        lines.append("🔁 Trades to Apply: none")
        lines.append("")

    # Draft picks moved via trades (optional 'picks' array on trade)
    moved_picks = []
    for t in todays:
        for p in (t.get("picks") or []):
            moved_picks.append(p)
    if moved_picks:
        lines.append("📋 Draft Pick Transfers:")
        for p in moved_picks:
            try:
                draft = p.get("draft") or "keeper"
                rnd = p.get("round")
                pickn = p.get("pick")
                src = p.get("from_team") or p.get("original_team") or "?"
                dst = p.get("to_team") or "?"
                lines.append(f"• {draft.title()} R{rnd} P{pickn}: {src} → {dst}")
            except Exception:
                continue
        lines.append("")

    # Keeper draft overlay reminder (no base-file mutation)
    lines.append("🧩 Keeper Draft Overlay:")
    lines.append("• Do NOT modify data/draft_order_2026.json (prospect base).")
    lines.append("• Append keeper picks to data/keeper_draft_picks_2026.json and rebuild data/draft_order_2026_mock.json.")
    lines.append("")

    # Final admin checklist
    lines.append("✅ Admin Checklist:")
    lines.append("• Apply Yahoo roster moves for all player transfers above.")
    lines.append("• Record WizBucks ledger entries (credit/debit as listed).")
    lines.append("• Update draft picks if any transfers are listed.")
    lines.append("• Rebuild keeper mock draft order file after any KAP outcomes.")

    return lines


@app.post("/api/admin/daily-processing-summary")
async def api_admin_daily_processing_summary(
    effective_on: str | None = None,
    authorized: bool = Depends(verify_api_key),
):
    """Build and post a daily processing task summary to the admin tasks channel.

    - effective_on: YYYY-MM-DD in ET. Defaults to today (America/New_York).
    """
    try:
        if effective_on:
            target_date = datetime.fromisoformat(effective_on).date()
        else:
            target_date = datetime.now(tz=ET).date()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid effective_on date")

    lines = _build_processing_summary_lines(target_date)
    content = "\n".join(lines)

    # Post to Discord (fire-and-forget)
    try:
        bot.loop.create_task(_send_admin_tasks_message(content))
    except RuntimeError:
        # If loop isn't running (tests), skip posting
        pass

    return {
        "ok": True,
        "effective_on": target_date.isoformat(),
        "preview": content[:800]
    }


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


# ---- KAP Submission API ----

from kap.kap_processor import (
    process_kap_submission,
    announce_kap_submission_to_discord,
    KAPSubmission,
    KAPResult,
)

@app.post("/api/kap/submit")
async def api_kap_submit(
    submission: KAPSubmission,
    authorized: bool = Depends(verify_api_key),
):
    """
    Submit KAP (Keeper Assignment Period) selections.
    
    This endpoint:
    1. Updates player contracts in combined_players.json
    2. Creates player_log entries for each keeper
    3. Updates draft_order_2026.json with taxed picks
    4. Deducts WizBucks from wallet
    5. Logs transaction to wizbucks_transactions.json
    6. Posts to Discord transactions channel
    """
    print(f"📝 Processing KAP submission for {submission.team}...")
    
    try:
        # Process submission
        result = process_kap_submission(submission, test_mode=False)
        
        # Announce to Discord
        if bot:
            bot.loop.create_task(announce_kap_submission_to_discord(result, bot))
            await asyncio.sleep(2)
        
        return {
            "ok": True,
            "team": result.team,
            "season": result.season,
            "timestamp": result.timestamp,
            "keepers_selected": result.keepers_selected,
            "keeper_salary_cost": result.keeper_salary_cost,
            "rat_cost": result.rat_cost,
            "buyin_cost": result.buyin_cost,
            "total_taxable_spend": result.total_taxable_spend,
            "wb_spent": result.wb_spent,
            "wb_remaining": result.wb_remaining,
            "draft_picks_taxed": result.draft_picks_taxed
        }
        
    except Exception as exc:
        import traceback
        print(f"❌ KAP submission failed: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))


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

    try:
        payload = build_draft_payload(draft_type)
        return _json_no_store(payload)
    except HTTPException:
        raise
    except Exception as exc:
        import traceback

        print("❌ /api/draft/active failed", {"draft_type": draft_type, "error": str(exc)})
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to build draft payload")


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

    return _json_no_store(
        {
            "season": season,
            "keeper": {
                "scheduled_date": keeper_date.isoformat() if keeper_date else None,
            },
            "prospect": {
                "scheduled_date": prospect_date.isoformat() if prospect_date else None,
            },
        }
    )


# ---- Draft Board APIs ----

@app.get("/api/draft/boards/{team}")
async def get_draft_board(
    team: str,
    authorized: bool = Depends(verify_api_key),
):
    """Return the current personal draft board for a team.

    Board entries are Caesar-shifted UPIDs.  The response includes ``k``
    (the shift value) so the frontend can decode them.
    """
    manager = BoardManager(season=PROSPECT_DRAFT_SEASON)
    board = manager.get_board(team)  # encoded values
    return _json_no_store(
        {
            "team": team,
            "board": board,
            "max_size": manager.MAX_BOARD_SIZE,
            "k": manager.shift,
        }
    )


@app.post("/api/draft/boards/{team}")
async def update_draft_board(
    team: str,
    payload: BoardUpdateRequest,
    authorized: bool = Depends(verify_api_key),
):
    """Replace a team's personal draft board with the provided encoded list."""
    if payload.team != team:
        raise HTTPException(status_code=400, detail="Team in path and body must match")

    manager = BoardManager(season=PROSPECT_DRAFT_SEASON)

    if len(payload.board) > manager.MAX_BOARD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Board too large (max {manager.MAX_BOARD_SIZE})",
        )

    # payload.board contains encoded UPID strings from the frontend
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
        "k": manager.shift,
    }


# ---- Orchestrate Both ----

# Set DISCORD_DISABLED=1 to run API-only (no Discord connection attempts).
# Useful when Cloudflare has rate-limited the IP.
DISCORD_DISABLED = os.getenv("DISCORD_DISABLED", "").strip().lower() in ("1", "true", "yes")

# Discord reconnect delay (seconds). Default is 61 minutes.
# Can be overridden on Render via DISCORD_RETRY_SECONDS.
DISCORD_RETRY_SECONDS = int(os.getenv("DISCORD_RETRY_SECONDS", "3660"))

async def start_bot():
    """Start Discord bot with retry on rate limit.

    Never crashes on rate-limit — retries indefinitely so the API stays up.
    """
    if DISCORD_DISABLED:
        print("⚠️ DISCORD_DISABLED=1 — skipping Discord connection. API-only mode.")
        # Keep the coroutine alive so the process doesn't exit
        while True:
            await asyncio.sleep(3600)
        return

    attempt = 0

    while True:
        attempt += 1
        try:
            if attempt > 1:
                print(f"🤖 Starting Discord bot (attempt {attempt})...")
            else:
                print("🤖 Starting Discord bot...")

            await bot.start(TOKEN)
            return  # clean shutdown

        except KeyboardInterrupt:
            print("⏸️ Received interrupt signal")
            await bot.close()
            return

        except Exception as e:
            # Print as much as possible so Render logs show the real cause (429 vs other)
            status = getattr(e, "status", None)
            code = getattr(e, "code", None)
            text = getattr(e, "text", None)
            response = getattr(e, "response", None)
            headers = getattr(response, "headers", None)

            print(f"❌ Discord start error (attempt {attempt}): {type(e).__name__}: {e}")
            if status is not None or code is not None:
                print(f"   details: status={status} code={code}")

            # If this is a true Discord API rate limit response, these headers are the
            # canonical way to determine what kind of limit you're hitting.
            if headers is not None:
                header_keys = (
                    "X-RateLimit-Limit",
                    "X-RateLimit-Remaining",
                    "X-RateLimit-Reset",
                    "X-RateLimit-Reset-After",
                    "X-RateLimit-Scope",
                    "Retry-After",
                    "CF-RAY",
                )
                header_dump = {k: headers.get(k) for k in header_keys if headers.get(k) is not None}
                if header_dump:
                    print(f"   headers: {header_dump}")

            ray_id = None
            if text:
                # discord.py HTTPException often stores the response body here
                m = re.search(r"Ray ID:\s*([0-9a-fA-F]+)", text)
                if m:
                    ray_id = m.group(1)

                # Avoid double-printing giant HTML blobs unless needed
                if "Error 1015" in text or "cloudflare" in text.lower():
                    print("   note: Cloudflare 1015-style response detected (likely IP-based throttling)")
                    if ray_id:
                        print(f"   cloudflare_ray_id: {ray_id}")
                else:
                    print(f"   response: {text}")

            print(traceback.format_exc())

            is_rate_limit = (
                status == 429
                or ("429" in str(e))
                or ("rate" in str(e).lower())
            )

            if is_rate_limit:
                # Best-effort: close the underlying aiohttp session to avoid
                # "Unclosed client session" warnings when retrying.
                try:
                    await bot.http.close()
                except Exception as close_exc:
                    print(f"⚠️ Failed to close Discord HTTP session: {close_exc}")

                delay = max(60, DISCORD_RETRY_SECONDS)
                mins = delay // 60
                print(f"⚠️ Rate limited by Discord (attempt {attempt}). Retrying in {mins}min...")
                await asyncio.sleep(delay)
                continue

            # Non-rate-limit errors are fatal
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
    
    # Start commit queue worker
    _start_commit_worker()
    
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
