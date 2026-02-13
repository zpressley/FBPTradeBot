"""
Web Draft Pick Request API
Add to health.py:
    from api_draft_pick_request import router as draft_pick_router, set_bot_reference
    app.include_router(draft_pick_router)

Endpoints:
- POST /api/draft/prospect/pick-request: Shows confirmation in Discord (old flow)
- POST /api/draft/prospect/pick-confirm: Directly confirms pick from website (new flow)
- POST /api/draft/prospect/validate-pick: Validates pick and returns player data
"""

import os
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/draft", tags=["draft-pick-request"])

API_KEY = os.getenv("BOT_API_KEY", "")


def verify_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


class PickRequestPayload(BaseModel):
    team: str
    player_name: str


# We need a reference to the running Discord bot and draft cog.
# health.py sets these after bot is ready.
_bot_ref = None
_draft_cog_ref = None


def set_bot_reference(bot):
    """Called from health.py after bot starts to give us access to Discord."""
    global _bot_ref
    _bot_ref = bot


def get_draft_cog():
    """Get the DraftCommands cog from the running bot."""
    global _draft_cog_ref
    if _bot_ref is None:
        return None

    if _draft_cog_ref is None:
        _draft_cog_ref = _bot_ref.get_cog("DraftCommands")

    return _draft_cog_ref


@router.post("/prospect/pick-request")
async def request_pick(payload: PickRequestPayload, authorized: bool = Depends(verify_key)):
    """Receive a draft pick request from the website.

    The confirmation UI is posted in the draft channel, but the buttons are
    restricted to the manager who owns the pick.
    """
    cog = get_draft_cog()

    if cog is None or cog.draft_manager is None:
        raise HTTPException(status_code=503, detail="Draft is not active or bot is not ready")

    draft_manager = cog.draft_manager

    # Check draft is active
    if draft_manager.state.get("status") != "active":
        raise HTTPException(status_code=400, detail="Draft is not currently active")

    current_pick = draft_manager.get_current_pick()
    if not current_pick:
        raise HTTPException(status_code=400, detail="No current pick (draft may be complete)")

    # Check it's the right team's turn
    if current_pick["team"] != payload.team:
        raise HTTPException(
            status_code=400,
            detail=f"Not {payload.team}'s turn. {current_pick['team']} is on the clock.",
        )

    # Validate the pick
    if cog.pick_validator:
        valid, message, player_data = cog.pick_validator.validate_pick(
            payload.team, payload.player_name
        )
        if not valid:
            raise HTTPException(status_code=400, detail=message)
    else:
        player_data = {
            "name": payload.player_name,
            "position": "?",
            "team": "?",
            "rank": "?",
        }

    if not player_data:
        player_data = {
            "name": payload.player_name,
            "position": "?",
            "team": "?",
            "rank": "?",
        }

    from commands.utils import MANAGER_DISCORD_IDS

    user_id = MANAGER_DISCORD_IDS.get(payload.team)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail=f"No Discord user mapped for team {payload.team}",
        )

    try:
        user = await _bot_ref.fetch_user(user_id)

        # Draft channel must be known so we can post the confirmation UI
        # where everyone can see the pick being considered.
        if not getattr(cog, "DRAFT_CHANNEL_ID", None):
            raise HTTPException(status_code=503, detail="Draft channel not set")

        draft_channel = _bot_ref.get_channel(cog.DRAFT_CHANNEL_ID)
        if draft_channel is None:
            draft_channel = await _bot_ref.fetch_channel(cog.DRAFT_CHANNEL_ID)

        # Post the pick confirmation in the draft channel (buttons are manager-only).
        await cog.show_pick_confirmation(
            draft_channel,
            user,
            payload.team,
            payload.player_name,
            current_pick,
            is_dm=False,
            delivery="channel",
        )

        return {
            "success": True,
            "message": f"Confirmation posted in draft channel for {payload.team}",
            "player": player_data.get("name"),
            "round": current_pick["round"],
            "pick": current_pick["pick"],
        }

    except Exception as e:
        print(f"❌ Error sending pick request DM: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send Discord DM: {str(e)}",
        )


@router.post("/prospect/validate-pick")
async def validate_pick(payload: PickRequestPayload, authorized: bool = Depends(verify_key)):
    """Validate a draft pick without making it.

    Returns player data if valid, used by website to show confirmation modal.
    """
    cog = get_draft_cog()

    if cog is None or cog.draft_manager is None:
        raise HTTPException(status_code=503, detail="Draft is not active or bot is not ready")

    draft_manager = cog.draft_manager

    if draft_manager.state.get("status") != "active":
        raise HTTPException(status_code=400, detail="Draft is not currently active")

    current_pick = draft_manager.get_current_pick()
    if not current_pick:
        raise HTTPException(status_code=400, detail="No current pick (draft may be complete)")

    if current_pick["team"] != payload.team:
        raise HTTPException(
            status_code=400,
            detail=f"Not {payload.team}'s turn. {current_pick['team']} is on the clock.",
        )

    # Validate the pick
    if cog.pick_validator:
        valid, message, player_data = cog.pick_validator.validate_pick(
            payload.team, payload.player_name
        )
        if not valid:
            raise HTTPException(status_code=400, detail=message)
    else:
        player_data = None

    if not player_data:
        player_data = {
            "name": payload.player_name,
            "position": "?",
            "team": "?",
            "rank": "?",
        }

    return {
        "valid": True,
        "player": player_data,
        "pick_info": {
            "round": current_pick["round"],
            "pick": current_pick["pick"],
            "round_type": current_pick.get("round_type", "DC"),
            "team": current_pick["team"],
        },
    }


async def _announce_pick_async(cog, draft_channel, pick_record, player_data):
    """Helper to announce pick in Discord - runs on bot's event loop."""
    try:
        await cog.announce_pick(draft_channel, pick_record, player_data)
        if cog.draft_board_thread:
            await cog.update_draft_board()
    except Exception as e:
        print(f"❌ Error in Discord announcement: {e}")
        import traceback
        traceback.print_exc()


@router.post("/prospect/pick-confirm")
async def confirm_pick(payload: PickRequestPayload, authorized: bool = Depends(verify_key)):
    """Directly confirm a draft pick from the website.

    This bypasses the Discord button confirmation - the website shows its own
    confirmation modal and calls this endpoint when the user confirms.
    """
    cog = get_draft_cog()

    if cog is None or cog.draft_manager is None:
        raise HTTPException(status_code=503, detail="Draft is not active or bot is not ready")

    draft_manager = cog.draft_manager

    if draft_manager.state.get("status") != "active":
        raise HTTPException(status_code=400, detail="Draft is not currently active")

    current_pick = draft_manager.get_current_pick()
    if not current_pick:
        raise HTTPException(status_code=400, detail="No current pick (draft may be complete)")

    if current_pick["team"] != payload.team:
        raise HTTPException(
            status_code=400,
            detail=f"Not {payload.team}'s turn. {current_pick['team']} is on the clock.",
        )

    # Validate the pick
    if cog.pick_validator:
        valid, message, player_data = cog.pick_validator.validate_pick(
            payload.team, payload.player_name
        )
        if not valid:
            raise HTTPException(status_code=400, detail=message)
    else:
        player_data = None

    if not player_data:
        player_data = {
            "name": payload.player_name,
            "position": "?",
            "team": "?",
            "rank": "?",
        }

    try:
        # Cancel the current pick timer before making the pick
        if cog.pick_timer_task:
            cog.pick_timer_task.cancel()
            cog.pick_timer_task = None

        # Record the pick (this advances the draft)
        pick_record = draft_manager.make_pick(
            payload.team,
            player_data["name"],
            player_data,
        )

        # Schedule Discord announcement on the bot's event loop
        # This avoids "Timeout context manager should be used inside a task" errors
        # because Discord.py operations need to run in the bot's event loop context.
        if getattr(cog, "DRAFT_CHANNEL_ID", None) and _bot_ref:
            draft_channel = _bot_ref.get_channel(cog.DRAFT_CHANNEL_ID)
            if draft_channel:
                _bot_ref.loop.create_task(
                    _announce_pick_async(cog, draft_channel, pick_record, player_data)
                )

        return {
            "success": True,
            "message": f"Pick confirmed: {player_data['name']}",
            "pick_record": {
                "round": pick_record["round"],
                "pick": pick_record["pick"],
                "team": pick_record["team"],
                "player": pick_record["player"],
            },
        }

    except Exception as e:
        print(f"❌ Error confirming pick: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to confirm pick: {str(e)}",
        )
