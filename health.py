import os
import asyncio
import discord
from discord.ext import commands
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel
import uvicorn

from auction_manager import AuctionManager

TEST_AUCTION_CHANNEL_ID = 1197200421639438537  # test channel for auction logs

# ---- Discord Bot Setup ----
TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("BOT_API_KEY")  # for FastAPI authentication

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

@bot.event
async def setup_hook():
    await bot.load_extension("commands.trade")
    await bot.load_extension("commands.roster")
    await bot.load_extension("commands.player")
    await bot.load_extension("commands.standings")
    # Auction commands (Prospect Auction Portal)
    try:
        await bot.load_extension("commands.auction")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"⚠️ Failed to load auction commands: {exc}")

# ---- FastAPI Web Server ----
app = FastAPI()


# Health check
@app.get("/")
def health():
    return {"status": "ok", "bot": str(bot.user)}


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


async def _send_auction_log_message(content: str) -> None:
    """Post an auction log message to the test channel, if available."""

    try:
        channel = bot.get_channel(TEST_AUCTION_CHANNEL_ID)
        if channel:
            await channel.send(content)
    except Exception as exc:  # pragma: no cover - logging only
        print(f"⚠️ Failed to send auction log message: {exc}")


def _commit_and_push(file_paths: list[str], message: str) -> None:
    """Best-effort helper to commit and push data updates.

    This assumes Git is configured on the Render instance with a remote
    that has push access. Failures are logged to stdout but do not
    raise, so API callers get a result even if sync fails.
    """

    try:
        import subprocess

        if file_paths:
            subprocess.run(["git", "add", *file_paths], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as exc:  # pragma: no cover - side-effect helper
        print(f"⚠️ Git commit/push failed: {exc}")


@app.get("/api/auction/current")
async def get_current_auction(authorized: bool = Depends(verify_api_key)):
    """Return the current auction state for API consumers.

    For now this simply returns the raw auction_current.json contents
    (initial version). The website will mostly consume the mirrored
    copy under fbp-hub/data/, but this endpoint allows the Worker or
    tools to introspect state directly.
    """

    manager = AuctionManager()
    # Use internal loader; it's fine for API surface.
    now = datetime.now(tz=ET)
    state = manager._load_or_initialize_auction(now)  # type: ignore[attr-defined]
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

    # Persist auction_current.json and push so website can sync data.
    if result.get("success"):
        _commit_and_push(["data/auction_current.json"],
                         f"Auction bid: {payload.bid_type} ${payload.amount} on {payload.prospect_id} by {payload.team}")

        # Fire-and-forget Discord log
        bid = result.get("bid", {})
        emoji = "📣" if bid.get("bid_type") == "OB" else "⚔️"
        content = (
            f"{emoji} **Auction Bid**\n"
            f"Team: `{bid.get('team', payload.team)}`\n"
            f"Prospect: `{bid.get('prospect_id', payload.prospect_id)}`\n"
            f"Amount: ${bid.get('amount', payload.amount)} WB ({bid.get('bid_type', payload.bid_type)})\n"
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

        # Fire-and-forget Discord log
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

# ---- Orchestrate Both ----
async def start_all():
    await bot.start(TOKEN)
def run_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
    server = uvicorn.Server(config)
    return server.serve()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_all())
    loop.run_until_complete(run_server())
