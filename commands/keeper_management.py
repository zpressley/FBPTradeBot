# commands/keeper_management.py
"""
Discord bot commands for offseason keeper management
Integrates with BotKeeperManager
"""

import discord
from discord.ext import commands
from discord import app_commands
from data.bot_keeper_manager import BotKeeperManager
from data_source_manager import DataSourceManager

class KeeperManagement(commands.Cog):
    """
    Commands for managing keepers during offseason
    Only available when bot keeper data is authoritative
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.keeper_mgr = BotKeeperManager()
        self.source_mgr = DataSourceManager()
    
    async def is_offseason(self, interaction: discord.Interaction) -> bool:
        """Check if we're in offseason mode"""
        if not self.source_mgr.should_use_bot_rosters():
            await interaction.response.send_message(
                "❌ Keeper management is only available during offseason!\n"
                f"Current phase: {self.source_mgr.get_phase_description()}\n"
                "Use Yahoo Fantasy to manage rosters during the season.",
                ephemeral=True
            )
            return False
        return True
    
    @app_commands.command(name="keepers", description="View your keeper roster (offseason)")
    async def view_keepers(self, interaction: discord.Interaction):
        """View your current keeper roster during offseason"""
        if not await self.is_offseason(interaction):
            return
        
        # Get manager team from Discord ID
        manager = self.get_manager_from_discord_id(interaction.user.id)
        
        if not manager:
            await interaction.response.send_message(
                "❌ Could not identify your team.",
                ephemeral=True
            )
            return
        
        # Get roster summary
        summary = self.keeper_mgr.get_team_summary(manager)
        
        if not summary or summary["total_keepers"] == 0:
            await interaction.response.send_message(
                f"📋 **{manager} Keeper Roster**\n\n"
                "No keepers currently. This happens at start of offseason.\n"
                "Run `/initialize-keepers` if this is unexpected.",
                ephemeral=True
            )
            return
        
        # Build roster display
        keepers = self.keeper_mgr.keepers.get(manager, [])
        
        msg = f"📋 **{manager} Keeper Roster**\n\n"
        msg += f"**Summary:**\n"
        msg += f"• Total Keepers: {summary['total_keepers']}/26\n"
        msg += f"• Total Salary: ${summary['total_salary']}\n"
        msg += f"• Contracts: {summary['tc_count']} TC, {summary['vc_count']} VC, {summary['fc_count']} FC\n\n"
        
        # Group by position
        batters = [k for k in keepers if k.get("position") not in ["SP", "RP", "P"]]
        pitchers = [k for k in keepers if k.get("position") in ["SP", "RP", "P"]]
        
        if batters:
            msg += "**Batters:**\n"
            for keeper in batters:
                contract = keeper.get("contract_type", "?")
                salary = keeper.get("salary", 0)
                il = " 🏥" if keeper.get("il_tag") else ""
                msg += f"• {keeper['name']} - {contract} (${salary}){il}\n"
            msg += "\n"
        
        if pitchers:
            msg += "**Pitchers:**\n"
            for keeper in pitchers:
                contract = keeper.get("contract_type", "?")
                salary = keeper.get("salary", 0)
                il = " 🏥" if keeper.get("il_tag") else ""
                msg += f"• {keeper['name']} - {contract} (${salary}){il}\n"
        
        await interaction.response.send_message(msg, ephemeral=True)
    
    @app_commands.command(name="keeper-status", description="Check keeper system status")
    async def keeper_status(self, interaction: discord.Interaction):
        """Show current keeper system status"""
        # Get current phase
        phase_desc = self.source_mgr.get_phase_description()
        use_bot = self.source_mgr.should_use_bot_rosters()
        
        msg = "📊 **FBP Keeper System Status**\n\n"
        msg += f"**Current Phase:** {phase_desc}\n"
        msg += f"**Roster Source:** {'Bot Keeper Data' if use_bot else 'Yahoo Fantasy'}\n\n"
        
        if use_bot:
            msg += "✅ Keeper management commands are ACTIVE\n"
            msg += "• Use `/keepers` to view your roster\n"
            msg += "• Trades update bot keeper data\n"
            msg += "• Changes won't sync to Yahoo until Week 1\n"
        else:
            msg += "ℹ️ Keeper management commands are INACTIVE\n"
            msg += "• Use Yahoo Fantasy to manage rosters\n"
            msg += "• Season is live, daily updates running\n"
        
        msg += f"\n**Data Sources:**\n"
        msg += f"• Rosters: {self.source_mgr.get_roster_source().value}\n"
        msg += f"• Prospects: {self.source_mgr.get_prospect_source().value}\n"
        msg += f"• Stats: {self.source_mgr.get_stats_source().value}\n"
        
        await interaction.response.send_message(msg, ephemeral=True)
    
    @app_commands.command(name="initialize-keepers", description="[ADMIN] Initialize keeper tracking from Yahoo")
    async def initialize_keepers(self, interaction: discord.Interaction):
        """Initialize keeper tracking from Yahoo rosters (admin only)"""
        # Check admin permission
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ This command requires administrator permissions.",
                ephemeral=True
            )
            return
        
        if not await self.is_offseason(interaction):
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Initialize from Yahoo
            keepers = self.keeper_mgr.initialize_from_yahoo()
            
            total_keepers = sum(len(players) for players in keepers.values())
            
            msg = f"✅ **Keeper tracking initialized!**\n\n"
            msg += f"Loaded {total_keepers} keepers across {len(keepers)} teams.\n\n"
            msg += "**Next steps:**\n"
            msg += "1. Verify keeper counts with `/keepers`\n"
            msg += "2. Process any offseason trades\n"
            msg += "3. All changes will be tracked in bot data\n"
            
            await interaction.followup.send(msg, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to initialize keepers: {str(e)}",
                ephemeral=True
            )
    
    def get_manager_from_discord_id(self, discord_id: int) -> str:
        """Map Discord user ID to team abbreviation"""
        # Import your existing mapping
        from commands.utils import DISCORD_ID_TO_TEAM
        return DISCORD_ID_TO_TEAM.get(discord_id)

async def setup(bot):
    await bot.add_cog(KeeperManagement(bot))
