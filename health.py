import os
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import threading
import sys

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

# Load environment variables
load_dotenv()

ET = ZoneInfo("US/Eastern")
PROSPECT_DRAFT_SEASON = 2025

TEST_AUCTION_CHANNEL_ID = 1197200421639438537  # test channel for auction logs

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
    """Simple API key gate for Cloudflare Worker → bot API traffic."""

    if not API_KEY:
        # If not configured, treat as disabled rather than locking everything.
        raise HTTPException(status_code=500, detail="BOT_API_KEY not configured")

    if x_api_key != API_KEY:
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
    """Best-effort helper to commit and push data updates."""
    try:
        import subprocess
        if file_paths:
            subprocess.run(["git", "add", *file_paths], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as exc:
        print(f"⚠️ Git commit/push failed: {exc}")


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
