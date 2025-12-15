# commands/service.py - Discord commands for service days tracking

import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime
from commands.utils import DISCORD_ID_TO_TEAM

STATS_FILE = "data/service_stats.json"

class ServiceDays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    def load_service_data(self):
        """Load service statistics data"""
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    @app_commands.command(name="service", description="Check service days for a specific player")
    @app_commands.describe(player_name="Name of the prospect to check")
    async def service(self, interaction: discord.Interaction, player_name: str):
        await interaction.response.defer(ephemeral=True)
        
        stats_data = self.load_service_data()
        
        # Try to find the player (case insensitive)
        found_player = None
        for name in stats_data.keys():
            if name.lower() == player_name.lower():
                found_player = name
                break
        
        if not found_player:
            # Try partial matching
            matches = [name for name in stats_data.keys() 
                      if player_name.lower() in name.lower()]
            
            if not matches:
                await interaction.followup.send(
                    f"❌ **Player not found:** `{player_name}`\n" +
                    "💡 Use `/prospects` to see all tracked prospects.",
                    ephemeral=True
                )
                return
            elif len(matches) == 1:
                found_player = matches[0]
            else:
                match_list = "\n".join(f"• {name}" for name in matches[:10])
                await interaction.followup.send(
                    f"🔍 **Multiple matches for:** `{player_name}`\n\n{match_list}",
                    ephemeral=True
                )
                return
        
        # Get player data
        data = stats_data[found_player]
        mlb = data["mlb_limits_status"]
        fbp = data["fbp_limits_status"]
        
        # Create detailed report
        embed = discord.Embed(
            title=f"📊 Service Days Report",
            description=f"**{found_player}** ({data.get('manager', 'Unknown')})",
            color=0x1f8b4c
        )
        
        # MLB Limits Section
        mlb_value = ""
        for stat_type, info in mlb.items():
            name = stat_type.replace('_', ' ').title()
            status = "🚨" if info["exceeded"] else "⚠️" if info["percentage"] >= 90 else "✅"
            mlb_value += f"{status} **{name}:** {info['current']}/{info['limit']} ({info['percentage']:.1f}%)\n"
        
        embed.add_field(
            name="🏟️ MLB Limits (Optional Graduation)",
            value=mlb_value,
            inline=False
        )
        
        # FBP Limits Section  
        fbp_value = ""
        for stat_type, info in fbp.items():
            name = stat_type.replace('_', ' ').title()
            status = "🚨" if info["exceeded"] else "⚠️" if info["percentage"] >= 90 else "✅"
            fbp_value += f"{status} **{name}:** {info['current']}/{info['limit']} ({info['percentage']:.1f}%)\n"
        
        embed.add_field(
            name="🎯 FBP Limits (Mandatory Graduation)",
            value=fbp_value,
            inline=False
        )
        
        # Status and alerts
        alerts = []
        for limits in [mlb, fbp]:
            for stat_type, info in limits.items():
                if info["exceeded"]:
                    alerts.append(f"🚨 {stat_type.replace('_', ' ').title()} limit exceeded")
                elif info["percentage"] >= 90:
                    alerts.append(f"⚠️ {stat_type.replace('_', ' ').title()} approaching limit")
        
        if alerts:
            embed.add_field(
                name="🚨 Alerts",
                value="\n".join(alerts),
                inline=False
            )
        else:
            embed.add_field(
                name="✅ Status",
                value="All service limits within safe ranges",
                inline=False
            )
        
        # Footer
        last_updated = data.get("last_updated", "")
        if last_updated:
            update_date = last_updated[:10]  # Just the date
            embed.set_footer(text=f"Last updated: {update_date} • MLB ID: {data.get('mlb_id', 'Unknown')}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @app_commands.command(name="prospects", description="View all prospects for a team")
    @app_commands.describe(team="Team abbreviation (leave blank for your team)")
    async def prospects(self, interaction: discord.Interaction, team: str = None):
        await interaction.response.defer(ephemeral=True)
        
        # Determine team
        if not team:
            team = DISCORD_ID_TO_TEAM.get(interaction.user.id)
            if not team:
                await interaction.followup.send(
                    "❌ Cannot determine your team. Please specify a team abbreviation.",
                    ephemeral=True
                )
                return
        else:
            team = team.upper()
        
        stats_data = self.load_service_data()
        
        # Get team prospects
        team_prospects = []
        for name, data in stats_data.items():
            if data.get("manager") == team:
                # Calculate alert level
                alert_level = 0
                for limits in [data["mlb_limits_status"], data["fbp_limits_status"]]:
                    for info in limits.values():
                        if info["exceeded"]:
                            alert_level = max(alert_level, 3)  # Critical
                        elif info["percentage"] >= 90:
                            alert_level = max(alert_level, 2)  # Warning
                        elif info["percentage"] >= 75:
                            alert_level = max(alert_level, 1)  # Caution
                
                team_prospects.append({
                    "name": name,
                    "data": data,
                    "alert_level": alert_level
                })
        
        if not team_prospects:
            await interaction.followup.send(
                f"📭 **No prospects found for team:** `{team}`",
                ephemeral=True
            )
            return
        
        # Sort by alert level, then by name
        team_prospects.sort(key=lambda x: (-x["alert_level"], x["name"]))
        
        # Create embed
        embed = discord.Embed(
            title=f"🌱 Prospects Service Status",
            description=f"**{team}** ({len(team_prospects)} prospects)",
            color=0x3498db
        )
        
        # Group by alert level
        critical = [p for p in team_prospects if p["alert_level"] == 3]
        warning = [p for p in team_prospects if p["alert_level"] == 2]
        caution = [p for p in team_prospects if p["alert_level"] == 1]
        safe = [p for p in team_prospects if p["alert_level"] == 0]
        
        def format_prospect_line(prospect):
            name = prospect["name"]
            data = prospect["data"]
            
            # Find highest percentage across all limits
            max_pct = 0
            for limits in [data["mlb_limits_status"], data["fbp_limits_status"]]:
                for info in limits.values():
                    max_pct = max(max_pct, info["percentage"])
            
            return f"• **{name}** ({max_pct:.0f}%)"
        
        if critical:
            critical_list = "\n".join(format_prospect_line(p) for p in critical[:10])
            embed.add_field(
                name="🚨 Critical (Limits Exceeded)",
                value=critical_list,
                inline=False
            )
        
        if warning:
            warning_list = "\n".join(format_prospect_line(p) for p in warning[:10])
            embed.add_field(
                name="⚠️ Warning (90%+ to Limit)",
                value=warning_list,
                inline=False
            )
        
        if caution:
            caution_list = "\n".join(format_prospect_line(p) for p in caution[:10])
            embed.add_field(
                name="🔶 Caution (75%+ to Limit)",
                value=caution_list,
                inline=False
            )
        
        if safe and len(team_prospects) <= 20:  # Only show safe if list is short
            safe_list = "\n".join(format_prospect_line(p) for p in safe[:10])
            embed.add_field(
                name="✅ Safe (Under 75%)",
                value=safe_list,
                inline=False
            )
        
        # Add summary
        summary = f"🚨 {len(critical)} Critical • ⚠️ {len(warning)} Warning • 🔶 {len(caution)} Caution • ✅ {len(safe)} Safe"
        embed.add_field(
            name="📊 Summary",
            value=summary,
            inline=False
        )
        
        embed.set_footer(text="Use /service <player_name> for detailed information")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @app_commands.command(name="alerts", description="Show all prospects with service day alerts")
    async def alerts(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        stats_data = self.load_service_data()
        
        # Find all prospects with alerts
        alerts = []
        for name, data in stats_data.items():
            player_alerts = []
            
            # Check MLB limits
            for stat_type, info in data["mlb_limits_status"].items():
                if info["exceeded"]:
                    player_alerts.append(f"🚨 MLB {stat_type.replace('_', ' ').title()} EXCEEDED")
                elif info["percentage"] >= 90:
                    player_alerts.append(f"⚠️ MLB {stat_type.replace('_', ' ').title()} at {info['percentage']:.0f}%")
            
            # Check FBP limits
            for stat_type, info in data["fbp_limits_status"].items():
                if info["exceeded"]:
                    player_alerts.append(f"🚨 FBP {stat_type.replace('_', ' ').title()} EXCEEDED")
                elif info["percentage"] >= 90:
                    player_alerts.append(f"⚠️ FBP {stat_type.replace('_', ' ').title()} at {info['percentage']:.0f}%")
            
            if player_alerts:
                alerts.append({
                    "name": name,
                    "manager": data.get("manager", "Unknown"),
                    "alerts": player_alerts,
                    "severity": 3 if any("EXCEEDED" in alert for alert in player_alerts) else 2
                })
        
        if not alerts:
            embed = discord.Embed(
                title="✅ No Service Day Alerts",
                description="All prospects are within safe service limits!",
                color=0x2ecc71
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Sort by severity, then by name
        alerts.sort(key=lambda x: (-x["severity"], x["name"]))
        
        embed = discord.Embed(
            title="🚨 Service Day Alerts",
            description=f"Found {len(alerts)} prospects with service day alerts",
            color=0xe74c3c
        )
        
        # Show alerts (limit to first 20 to avoid Discord limits)
        for alert in alerts[:20]:
            alert_text = "\n".join(alert["alerts"])
            embed.add_field(
                name=f"{alert['name']} ({alert['manager']})",
                value=alert_text,
                inline=True
            )
        
        if len(alerts) > 20:
            embed.set_footer(text=f"Showing first 20 of {len(alerts)} alerts")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ServiceDays(bot))