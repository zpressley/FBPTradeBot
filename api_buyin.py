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
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter(prefix="/api/buyin", tags=["buyin"])

API_KEY = os.getenv("BOT_API_KEY", "")

# Transaction channel for buy-in announcements
TRANSACTION_LOG_CHANNEL_ID = 1089979265619083444

# Buy-in costs per round
BUY_IN_COSTS = {
    1: 55,
    2: 35,
    3: 10
}

def verify_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


_bot_ref = None


def set_buyin_bot_reference(bot):
    """Called from health.py after bot starts to give us access to Discord."""
    global _bot_ref
    _bot_ref = bot


class BuyinPurchasePayload(BaseModel):
    team: str
    round: int
    cost: int
    purchased_by: str


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


def validate_admin(admin_user: str, managers_data: dict) -> bool:
    """Check if user has admin role."""
    team_data = managers_data.get("teams", {}).get(admin_user)
    if not team_data:
        return False
    return team_data.get("role") == "admin"


def get_kap_balance(team: str, managers_data: dict) -> int:
    """Get team's current KAP balance."""
    team_data = managers_data.get("teams", {}).get(team)
    if not team_data:
        return 0
    return team_data.get("wizbucks", {}).get("2026", {}).get("allotments", {}).get("KAP", {}).get("total", 0)


def update_kap_balance(team: str, amount: int, managers_data: dict) -> dict:
    """Update team's KAP balance by the specified amount."""
    if team not in managers_data.get("teams", {}):
        raise HTTPException(status_code=404, detail=f"Team {team} not found")
    
    team_data = managers_data["teams"][team]
    wizbucks_2026 = team_data.get("wizbucks", {}).get("2026", {})
    kap_allotments = wizbucks_2026.get("allotments", {}).get("KAP", {})
    
    current_total = kap_allotments.get("total", 0)
    new_total = current_total + amount
    
    kap_allotments["total"] = new_total
    
    return managers_data




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
    """Purchase a keeper draft buy-in.
    
    Updates:
    - draft_order_2026.json: Mark pick as purchased
    - wizbucks_transactions.json: Log transaction
    - managers.json: Deduct from KAP balance
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
    managers_data = load_json_file("config/managers.json")
    
    # Check KAP balance
    current_balance = get_kap_balance(payload.team, managers_data)
    if current_balance < payload.cost:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient KAP balance. Need ${payload.cost}, have ${current_balance}"
        )
    
    # Find the pick in draft order
    pick = None
    pick_index = None
    for i, p in enumerate(draft_order):
        if (p.get("draft") == "keeper" and 
            p.get("round") == payload.round and 
            p.get("current_owner") == payload.team):
            pick = p
            pick_index = i
            break
    
    if not pick:
        raise HTTPException(
            status_code=404, 
            detail=f"No Round {payload.round} pick found for team {payload.team}"
        )
    
    # Check if already purchased
    if pick.get("buyin_purchased"):
        raise HTTPException(
            status_code=400, 
            detail=f"Round {payload.round} buy-in already purchased"
        )
    
    # Update pick
    pick["buyin_purchased"] = True
    pick["buyin_purchased_at"] = datetime.utcnow().isoformat()
    pick["buyin_purchased_by"] = payload.purchased_by
    draft_order[pick_index] = pick
    
    # Deduct from KAP balance
    managers_data = update_kap_balance(payload.team, -payload.cost, managers_data)
    
    # Load or create wizbucks transactions
    try:
        transactions = load_json_file("data/wizbucks_transactions.json")
    except:
        transactions = []
    
    # Add transaction entry
    transaction = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "type": "buyin_purchase",
        "team": payload.team,
        "amount": -payload.cost,
        "balance_after": current_balance - payload.cost,
        "description": f"Round {payload.round} keeper draft buy-in purchase",
        "metadata": {
            "round": payload.round,
            "draft_type": "keeper",
            "purchased_by": payload.purchased_by
        }
    }
    transactions.append(transaction)
    
    # Save all files
    try:
        save_json_file("data/draft_order_2026.json", draft_order)
        save_json_file("config/managers.json", managers_data)
        save_json_file("data/wizbucks_transactions.json", transactions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save changes: {str(e)}")
    
    # Post to Discord
    await post_to_discord(payload.team, payload.round, payload.cost, "purchased")
    
    return {
        "success": True,
        "message": f"Round {payload.round} buy-in purchased for ${payload.cost}",
        "transaction": transaction,
        "new_balance": current_balance - payload.cost
    }


@router.post("/refund")
async def refund_buyin(payload: BuyinRefundPayload, authorized: bool = Depends(verify_key)):
    """Refund a keeper draft buy-in (admin only).
    
    Reverses the purchase:
    - draft_order_2026.json: Mark pick as not purchased
    - wizbucks_transactions.json: Log refund transaction
    - managers.json: Restore KAP balance
    """
    
    # Load data files
    draft_order = load_json_file("data/draft_order_2026.json")
    managers_data = load_json_file("config/managers.json")
    
    # Validate admin permission
    if not validate_admin(payload.admin_user, managers_data):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Find the pick in draft order
    pick = None
    pick_index = None
    for i, p in enumerate(draft_order):
        if (p.get("draft") == "keeper" and 
            p.get("round") == payload.round and 
            p.get("current_owner") == payload.team):
            pick = p
            pick_index = i
            break
    
    if not pick:
        raise HTTPException(
            status_code=404, 
            detail=f"No Round {payload.round} pick found for team {payload.team}"
        )
    
    # Check if actually purchased
    if not pick.get("buyin_purchased"):
        raise HTTPException(
            status_code=400, 
            detail=f"Round {payload.round} buy-in has not been purchased"
        )
    
    # Get refund amount
    refund_amount = BUY_IN_COSTS.get(payload.round, 0)
    if not refund_amount:
        raise HTTPException(status_code=400, detail=f"Invalid round {payload.round}")
    
    # Update pick
    pick["buyin_purchased"] = False
    pick["buyin_purchased_at"] = None
    pick["buyin_purchased_by"] = None
    draft_order[pick_index] = pick
    
    # Get current balance before refund
    current_balance = get_kap_balance(payload.team, managers_data)
    
    # Restore KAP balance
    managers_data = update_kap_balance(payload.team, refund_amount, managers_data)
    
    # Load transactions
    try:
        transactions = load_json_file("data/wizbucks_transactions.json")
    except:
        transactions = []
    
    # Add refund transaction
    transaction = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "type": "buyin_refund",
        "team": payload.team,
        "amount": refund_amount,
        "balance_after": current_balance + refund_amount,
        "description": f"Round {payload.round} keeper draft buy-in refund (admin action)",
        "metadata": {
            "round": payload.round,
            "draft_type": "keeper",
            "refunded_by": payload.admin_user
        }
    }
    transactions.append(transaction)
    
    # Save all files
    try:
        save_json_file("data/draft_order_2026.json", draft_order)
        save_json_file("config/managers.json", managers_data)
        save_json_file("data/wizbucks_transactions.json", transactions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save changes: {str(e)}")
    
    # Post to Discord
    await post_to_discord(payload.team, payload.round, refund_amount, "refunded")
    
    return {
        "success": True,
        "message": f"Round {payload.round} buy-in refunded for ${refund_amount}",
        "transaction": transaction,
        "new_balance": current_balance + refund_amount
    }
