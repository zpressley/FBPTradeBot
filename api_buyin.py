"""
Buy-In API for Keeper Draft Pick Purchases
Add to health.py:
    from api_buyin import router as buyin_router, set_buyin_bot_reference
    app.include_router(buyin_router)

Endpoints:
- POST /api/buyin/purchase: Purchase a keeper draft buy-in
- POST /api/buyin/refund: Refund a keeper draft buy-in (admin only)
"""

import os
import json
import asyncio
import subprocess
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from buyin.buyin_service import BUY_IN_COSTS, apply_keeper_buyin_purchase, apply_keeper_buyin_refund, get_wallet_balance
from team_utils import normalize_team_abbr

router = APIRouter(prefix="/api/buyin", tags=["buyin"])

API_KEY = os.getenv("BOT_API_KEY", "")

# Transaction channel for buy-in announcements
TRANSACTION_LOG_CHANNEL_ID = 1089979265619083444


def verify_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


_bot_ref = None


def set_buyin_bot_reference(bot):
    """Called from health.py after bot starts to give us access to Discord."""
    global _bot_ref
    _bot_ref = bot


def _schedule_on_bot_loop(coro, label: str) -> None:
    """Run Discord IO on the bot's event loop.

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


class BuyinPurchasePayload(BaseModel):
    team: str
    round: int
    cost: int
    purchased_by: str
    pick: Optional[int] = None  # Specify which pick when team has multiple in same round


class BuyinRefundPayload(BaseModel):
    team: str
    round: int
    admin_user: str


def load_json_file(filepath: str):
    """Load a JSON file."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load {filepath}: {str(e)}")


def save_json_file(filepath: str, data):
    """Save data to a JSON file."""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save {filepath}: {str(e)}")


def git_commit_and_push(files: list, message: str):
    """Commit and push files to git.
    
    CRITICAL: Raises exception on failure!
    """
    try:
        # Configure git if in Render environment (one-time setup)
        github_token = os.getenv("GITHUB_TOKEN")
        if github_token:
            # Check if remote needs authentication setup
            result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
            current_url = result.stdout.strip()
            
            # If URL doesn't have token, update it
            if "@github.com" not in current_url and github_token:
                # Extract repo path from URL
                if "github.com" in current_url:
                    repo_path = current_url.split("github.com/")[-1].replace(".git", "")
                    auth_url = f"https://{github_token}@github.com/{repo_path}.git"
                    subprocess.run(["git", "remote", "set-url", "origin", auth_url], check=True, capture_output=True, text=True)
                    print("✅ Git remote configured with authentication")
        
        for f in files:
            subprocess.run(["git", "add", f], check=True, capture_output=True, text=True)
        
        subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True, text=True)
        print(f"✅ Git commit: {message}")
        
        # Get current branch name
        result = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True)
        current_branch = result.stdout.strip() or "main"
        
        subprocess.run(["git", "push", "origin", current_branch], check=True, capture_output=True, text=True)
        print(f"✅ Git push: {message}")
        
    except subprocess.CalledProcessError as e:
        error_msg = f"Git operation failed: {e.stderr if e.stderr else str(e)}"
        print(f"❌ {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    except Exception as e:
        error_msg = f"Unexpected git error: {str(e)}"
        print(f"❌ {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


def validate_admin(admin_user: str, managers_data: dict) -> bool:
    """Check if user has admin role."""
    admin_team = normalize_team_abbr(admin_user, managers_data=managers_data)
    team_data = (managers_data.get("teams") or {}).get(admin_team)
    if not isinstance(team_data, dict):
        return False
    return team_data.get("role") == "admin"





async def post_to_discord(team: str, round: int, cost: int, action: str = "purchased"):
    """Post buy-in transaction to Discord channel."""
    if not _bot_ref:
        print("⚠️ Bot reference not set, skipping Discord notification")
        return
    
    try:
        channel = _bot_ref.get_channel(TRANSACTION_LOG_CHANNEL_ID)
        if not channel:
            channel = await _bot_ref.fetch_channel(TRANSACTION_LOG_CHANNEL_ID)
        
        if not channel:
            print(f"⚠️ Transaction channel {TRANSACTION_LOG_CHANNEL_ID} not found")
            return
        
        # Create embed
        import discord
        
        if action == "purchased":
            color = 0xEF3E42  # Red
            title = "🎯 Draft Pick Buy-In Purchased"
            description = f"**{team}** purchased Round {round} buy-in"
        else:
            color = 0xFFB612  # Yellow
            title = "↩️ Draft Pick Buy-In Refunded"
            description = f"**{team}** Round {round} buy-in refunded"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Team", value=team, inline=True)
        embed.add_field(name="Round", value=str(round), inline=True)
        embed.add_field(name="Amount", value=f"${cost}", inline=True)
        
        await channel.send(embed=embed)
        print(f"✅ Posted {action} notification to Discord")
        
    except Exception as e:
        print(f"❌ Error posting to Discord: {e}")


@router.post("/purchase")
async def purchase_buyin(payload: BuyinPurchasePayload, authorized: bool = Depends(verify_key)):
    try:
        print(
            "📥 BUYIN_PURCHASE",
            {
                "team": payload.team,
                "round": payload.round,
                "cost": payload.cost,
                "purchased_by": payload.purchased_by,
            },
        )
    except Exception:
        pass
    """Purchase a keeper draft buy-in.
    
    Updates:
    - draft_order_2026.json: Mark pick as purchased
    - wizbucks.json: Deduct from WizBucks wallet (single-wallet principle)
    - wizbucks_transactions.json: Log transaction
    """
    
    # Validate round
    if payload.round not in BUY_IN_COSTS:
        raise HTTPException(status_code=400, detail=f"Invalid round {payload.round}. Only rounds 1-3 require buy-ins.")
    
    # Validate cost matches expected
    expected_cost = BUY_IN_COSTS[payload.round]
    if payload.cost != expected_cost:
        raise HTTPException(status_code=400, detail=f"Invalid cost. Round {payload.round} buy-in is ${expected_cost}")
    
    # Load data files
    draft_order = load_json_file("data/draft_order_2026.json")
    wizbucks_data = load_json_file("data/wizbucks.json")  # CRITICAL: Wallet source of truth
    managers_data = load_json_file("config/managers.json")

    team = normalize_team_abbr(payload.team, managers_data=managers_data)
    
    # Load or create wizbucks transactions
    try:
        transactions = load_json_file("data/wizbucks_transactions.json")
    except Exception:
        transactions = []

    if not isinstance(transactions, list):
        transactions = []

    # Apply purchase (mutates draft_order, wizbucks_data, transactions)
    try:
        result = apply_keeper_buyin_purchase(
            team=team,
            round=payload.round,
            pick=payload.pick,  # Pass pick parameter (None if not specified)
            draft_order=draft_order,
            wizbucks_data=wizbucks_data,  # CRITICAL: Pass wallet data, not managers_data
            managers_data=managers_data,
            ledger=transactions,
            purchased_by=payload.purchased_by,
            source="buyin_api",
            trade_id=None,
        )
    except ValueError as exc:
        try:
            print(
                "❌ BUYIN_PURCHASE_FAILED",
                {
                    "team": payload.team,
                    "round": payload.round,
                    "error": str(exc),
                },
            )
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc))

    # Save all files (CRITICAL: Save wallet!)
    try:
        save_json_file("data/draft_order_2026.json", draft_order)
        save_json_file("data/wizbucks.json", wizbucks_data)  # CRITICAL: Save wallet
        save_json_file("data/wizbucks_transactions.json", transactions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save changes: {str(e)}")
    
    # Commit and push to git
    try:
        commit_msg = f"Keeper draft buy-in: {team} Round {payload.round} (${result.cost})"
        git_commit_and_push(
            ["data/draft_order_2026.json", "data/wizbucks.json", "data/wizbucks_transactions.json"],
            commit_msg
        )
    except Exception as e:
        # Git failure is critical - changes are saved locally but not pushed
        print(f"❌ Git commit/push failed: {str(e)}")
        raise

    # Post to Discord (do not block the API response; run on bot loop)
    try:
        _schedule_on_bot_loop(post_to_discord(team, payload.round, result.cost, "purchased"), "buyin_post_to_discord")
    except Exception as exc:
        print(f"⚠️ Failed to schedule buy-in Discord notification: {exc}")

    try:
        print(
            "✅ BUYIN_PURCHASE_OK",
            {
                "team": team,
                "round": payload.round,
                "cost": result.cost,
                "new_balance": result.wallet_balance_after,
            },
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Round {payload.round} buy-in purchased for ${result.cost}",
        "transaction": result.ledger_entry,
        "new_balance": result.wallet_balance_after,
    }


@router.post("/refund")
async def refund_buyin(payload: BuyinRefundPayload, authorized: bool = Depends(verify_key)):
    try:
        print(
            "📥 BUYIN_REFUND",
            {
                "team": payload.team,
                "round": payload.round,
                "admin_user": payload.admin_user,
            },
        )
    except Exception:
        pass
    """Refund a keeper draft buy-in (admin only).
    
    Reverses the purchase:
    - draft_order_2026.json: Mark pick as not purchased
    - wizbucks.json: Refund to WizBucks wallet (single-wallet principle)
    - wizbucks_transactions.json: Log refund transaction
    """
    
    # Load data files
    draft_order = load_json_file("data/draft_order_2026.json")
    wizbucks_data = load_json_file("data/wizbucks.json")  # CRITICAL: Wallet source of truth
    managers_data = load_json_file("config/managers.json")

    team = normalize_team_abbr(payload.team, managers_data=managers_data)
    admin_team = normalize_team_abbr(payload.admin_user, managers_data=managers_data)

    # Validate admin permission
    if not validate_admin(admin_team, managers_data):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Load transactions
    try:
        transactions = load_json_file("data/wizbucks_transactions.json")
    except Exception:
        transactions = []

    if not isinstance(transactions, list):
        transactions = []

    # Apply refund (mutates draft_order, wizbucks_data, transactions)
    try:
        result = apply_keeper_buyin_refund(
            team=team,
            round=payload.round,
            pick=None,
            draft_order=draft_order,
            wizbucks_data=wizbucks_data,  # CRITICAL: Pass wallet data, not managers_data
            managers_data=managers_data,
            ledger=transactions,
            refunded_by=admin_team,
            source="buyin_api",
        )
    except ValueError as exc:
        try:
            print(
                "❌ BUYIN_REFUND_FAILED",
                {
                    "team": payload.team,
                    "round": payload.round,
                    "error": str(exc),
                },
            )
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc))

    # Save all files (CRITICAL: Save wallet!)
    try:
        save_json_file("data/draft_order_2026.json", draft_order)
        save_json_file("data/wizbucks.json", wizbucks_data)  # CRITICAL: Save wallet
        save_json_file("data/wizbucks_transactions.json", transactions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save changes: {str(e)}")
    
    # Commit and push to git
    try:
        commit_msg = f"Keeper draft buy-in refund: {team} Round {payload.round} (${result.amount})"
        git_commit_and_push(
            ["data/draft_order_2026.json", "data/wizbucks.json", "data/wizbucks_transactions.json"],
            commit_msg
        )
    except Exception as e:
        # Git failure is critical - changes are saved locally but not pushed
        print(f"❌ Git commit/push failed: {str(e)}")
        raise

    # Post to Discord (do not block the API response; run on bot loop)
    try:
        _schedule_on_bot_loop(post_to_discord(team, payload.round, result.amount, "refunded"), "buyin_refund_post_to_discord")
    except Exception as exc:
        print(f"⚠️ Failed to schedule buy-in refund Discord notification: {exc}")

    try:
        print(
            "✅ BUYIN_REFUND_OK",
            {
                "team": team,
                "round": payload.round,
                "amount": result.amount,
                "new_balance": result.wallet_balance_after,
            },
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Round {payload.round} buy-in refunded for ${result.amount}",
        "transaction": result.ledger_entry,
        "new_balance": result.wallet_balance_after,
    }
