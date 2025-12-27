"""
Phase 4: Draft Bot Integration
Hooks database updates into draft pick confirmation flow
"""

import discord
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draft.database_channel_manager import DatabaseChannelManager


class DraftDatabaseIntegration:
    """
    Integration layer between draft bot and database tracker.
    
    Add this to your existing draft bot to enable live database updates.
    """
    
    def __init__(self, bot, database_channel_id: int):
        self.bot = bot
        self.db_manager = DatabaseChannelManager(bot, database_channel_id)
    
    async def on_pick_confirmed(
        self,
        player_name: str,
        round_num: int,
        pick_num: int,
        manager: str,
        interaction: discord.Interaction
    ):
        """
        Called when a draft pick is confirmed.
        
        Add this to your draft bot's pick confirmation handler.
        
        Args:
            player_name: Full player name that was picked
            round_num: Draft round number
            pick_num: Pick number within the round
            manager: Manager abbreviation (WIZ, B2J, etc)
            interaction: Discord interaction (for responses)
            
        Example integration:
            # In your draft bot, after pick is confirmed:
            await draft_db_integration.on_pick_confirmed(
                player_name=picked_player,
                round_num=current_round,
                pick_num=current_pick,
                manager=picking_manager,
                interaction=interaction
            )
        """
        
        # Queue the database update
        self.db_manager.update_pick(
            player_name=player_name,
            round_num=round_num,
            pick_num=pick_num,
            manager=manager
        )
        
        # Optional: Send confirmation that database is updating
        # (Only if you want to notify users)
        # await interaction.followup.send(
        #     f"📊 Database updating for {player_name}...",
        #     ephemeral=True
        # )
    
    def get_queue_status(self) -> dict:
        """Get current update queue status"""
        return self.db_manager.tracker.get_stats()


# ========== INTEGRATION EXAMPLE ==========

"""
HOW TO INTEGRATE WITH YOUR EXISTING DRAFT BOT:

1. Initialize in your draft bot setup:

    from draft_database_integration import DraftDatabaseIntegration
    
    # In your draft bot __init__ or setup
    DATABASE_CHANNEL_ID = 1450548156118077532  # Your database channel
    
    self.db_integration = DraftDatabaseIntegration(
        bot=self.bot,
        database_channel_id=DATABASE_CHANNEL_ID
    )


2. Call on pick confirmation:

    async def confirm_pick(self, interaction, player_name, round_num, pick_num, manager):
        '''Your existing pick confirmation logic'''
        
        # ... existing code ...
        # (update board, send confirmation, etc)
        
        # ADD THIS: Update database
        await self.db_integration.on_pick_confirmed(
            player_name=player_name,
            round_num=round_num,
            pick_num=pick_num,
            manager=manager,
            interaction=interaction
        )
        
        # ... rest of existing code ...


3. Optional status check:

    @app_commands.command(name="draft_status")
    async def draft_status(self, interaction):
        '''Show draft status including database queue'''
        
        # ... your existing status info ...
        
        # Add database queue info
        db_stats = self.db_integration.get_queue_status()
        
        msg += f"\n📊 Database Queue: {db_stats['queue_size']} pending"
        
        await interaction.response.send_message(msg, ephemeral=True)


4. That's it! Picks will now update the database automatically.
"""
