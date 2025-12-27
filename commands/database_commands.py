"""
Phase 3: Discord Commands for Database Management
Slash commands for database setup, refresh, and status checking
"""

import discord
from discord import app_commands
from discord.ext import commands
import sys
import os
import json

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draft.database_channel_manager import DatabaseChannelManager


class DatabaseCommands(commands.Cog):
    """Discord commands for prospect database management"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db_manager = None
        
        # Load config for channel ID
        self.config = self._load_config()
        self.database_channel_id = self.config.get('database_channel_id')
        
        if self.database_channel_id:
            self.db_manager = DatabaseChannelManager(bot, self.database_channel_id)
    
    def _load_config(self) -> dict:
        """Load database config"""
        try:
            with open('data/database_config.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def _save_config(self):
        """Save database config"""
        with open('data/database_config.json', 'w') as f:
            json.dump(self.config, f, indent=2)
    
    @app_commands.command(name="db_setup", description="[ADMIN] Initialize prospect database channel")
    @app_commands.describe(channel="Database channel (leave empty to use current channel)")
    async def db_setup(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None
    ):
        """
        Setup the prospect database with ranked lists and tracking.
        
        This will:
        - Clear all existing messages
        - Post ranked prospect lists
        - Create position threads
        - Enable live pick updates
        """
        
        # Use specified channel or current channel
        target_channel = channel or interaction.channel
        
        await interaction.response.defer(ephemeral=True)
        
        # Initialize manager if needed
        if not self.db_manager or self.db_manager.channel_id != target_channel.id:
            self.db_manager = DatabaseChannelManager(self.bot, target_channel.id)
            self.config['database_channel_id'] = target_channel.id
            self._save_config()
        
        # Load prospect database
        try:
            from commands.lookup import all_players
            
            # Filter to prospects only
            class ProspectDB:
                @staticmethod
                def get_by_position(pos):
                    return [p for p in all_players if p.get('position') == pos and p.get('player_type') == 'Farm']
            
            prospect_db = ProspectDB()
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error loading prospect data: {e}",
                ephemeral=True
            )
            return
        
        # Run setup
        await interaction.followup.send(
            f"🔄 Starting database setup in {target_channel.mention}...\n"
            f"⏳ This will take 30-60 seconds",
            ephemeral=True
        )
        
        try:
            success = await self.db_manager.db_setup(interaction.guild, prospect_db)
            
            if success:
                stats = self.db_manager.tracker.get_stats()
                
                await interaction.followup.send(
                    f"✅ Database setup complete!\n"
                    f"📊 {stats['total_players']} prospects tracked\n"
                    f"📁 {stats['position_groups']} position groups created\n"
                    f"🎯 Ready for live pick updates",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ Database setup failed - check logs",
                    ephemeral=True
                )
                
        except Exception as e:
            await interaction.followup.send(
                f"❌ Setup error: {e}",
                ephemeral=True
            )
    
    @app_commands.command(name="db_status", description="Check database status")
    async def db_status(self, interaction: discord.Interaction):
        """Show current database and tracker status"""
        
        if not self.db_manager:
            await interaction.response.send_message(
                "❌ Database not initialized. Run `/db_setup` first.",
                ephemeral=True
            )
            return
        
        stats = self.db_manager.tracker.get_stats()
        
        msg = "📊 **Database Status**\n\n"
        msg += f"**Tracking:**\n"
        msg += f"  • Players tracked: {stats['total_players']}\n"
        msg += f"  • Position groups: {stats['position_groups']}\n"
        msg += f"\n**Update Queue:**\n"
        msg += f"  • Queued updates: {stats['queue_size']}\n"
        msg += f"  • Processing: {'Yes' if stats['processing'] else 'No'}\n"
        
        # Load metadata
        try:
            with open('data/database_tracker.json', 'r') as f:
                tracker_data = json.load(f)
                metadata = tracker_data.get('metadata', {})
                
                msg += f"\n**Last Setup:**\n"
                msg += f"  • {metadata.get('last_saved', 'Never')}\n"
        except:
            pass
        
        await interaction.response.send_message(msg, ephemeral=True)
    
    @app_commands.command(name="db_refresh", description="[ADMIN] Manually refresh database from combined_players.json")
    async def db_refresh(self, interaction: discord.Interaction):
        """
        Refresh the entire database (re-run setup).
        Use this when combined_players.json is updated.
        """
        
        if not self.db_manager:
            await interaction.response.send_message(
                "❌ Database not initialized. Run `/db_setup` first.",
                ephemeral=True
            )
            return
        
        # Just re-run setup
        await self.db_setup(interaction, channel=None)
    
    @app_commands.command(name="db_find", description="Find a player in the database")
    @app_commands.describe(player="Player name to search for")
    async def db_find(self, interaction: discord.Interaction, player: str):
        """Find where a player is in the database"""
        
        if not self.db_manager:
            await interaction.response.send_message(
                "❌ Database not initialized.",
                ephemeral=True
            )
            return
        
        location = self.db_manager.tracker.get_player_location(player)
        
        if not location:
            await interaction.response.send_message(
                f"❌ {player} not found in database.\n"
                f"💡 Run `/db_setup` to rebuild tracking.",
                ephemeral=True
            )
            return
        
        msg = f"📍 **{player}** Location\n\n"
        msg += f"**Position Group:** {location['position_group']}\n"
        msg += f"**Rank:** {location['rank']}\n"
        msg += f"**Main Message ID:** {location['main_message_id']}\n"
        msg += f"**Thread Message ID:** {location['thread_message_id']}\n"
        msg += f"**Status Line:** {location['status_line_index']}\n"
        
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(DatabaseCommands(bot))
