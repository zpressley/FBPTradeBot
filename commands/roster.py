import discord
from discord.ext import commands
from discord import app_commands
import json
from commands.utils import DISCORD_ID_TO_TEAM

with open("data/combined_players.json", "r") as f:
    combined_data = json.load(f)

TEAM_CHOICES = [
    app_commands.Choice(name="Hammers", value="HAM"),
    app_commands.Choice(name="Rick Vaughn", value="RV"),
    app_commands.Choice(name="Btwn2Jackies", value="B2J"),
    app_commands.Choice(name="Country Fried Lamb", value="CFL"),
    app_commands.Choice(name="The Damn Yankees", value="DMN"),
    app_commands.Choice(name="La Flama Blanca", value="LFB"),
    app_commands.Choice(name="Jepordizers!", value="JEP"),
    app_commands.Choice(name="The Bluke Blokes", value="TBB"),
    app_commands.Choice(name="Whiz Kids", value="WIZ"),
    app_commands.Choice(name="Andromedans", value="DRO"),
    app_commands.Choice(name="not much of a donkey", value="SAD"),
    app_commands.Choice(name="Weekend Warriors", value="WAR")
]

VIEW_CHOICES = [
    app_commands.Choice(name="MLB", value="mlb"),
    app_commands.Choice(name="Farm", value="farm"),
    app_commands.Choice(name="All", value="all"),
]

class Roster(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="roster", description="View the roster of a team")
    @app_commands.choices(team=TEAM_CHOICES, view=VIEW_CHOICES)
    async def roster(self, interaction: discord.Interaction, view: app_commands.Choice[str], team: app_commands.Choice[str] = None):
        view_type = view.value
        team_abbr = team.value if team else DISCORD_ID_TO_TEAM.get(interaction.user.id)

        if not team_abbr:
            await interaction.response.send_message("❌ Cannot determine team. Please select one.", ephemeral=True)
            return

        roster = [p for p in combined_data if p.get("manager") == team_abbr]

        def format_player(p):
            return f"{p['position']} {p['name']} [{p['team']}] [{p['years_simple'] or 'NA'}]"

        mlb = [p for p in roster if p.get("player_type") == "MLB"]
        farm = [p for p in roster if p.get("player_type") == "Farm"]

        batters = [format_player(p) for p in mlb if p["position"] not in ["SP", "RP", "P"]]
        pitchers = [format_player(p) for p in mlb if p["position"] in ["SP", "RP", "P"]]

        purchased = [format_player(p) for p in farm if p["contract_type"] == "PC"]
        farmed = [format_player(p) for p in farm if p["contract_type"] == "FC"]
        devs = [format_player(p) for p in farm if p["contract_type"] == "DC"]

        msg = f"📋 **{team_abbr} Roster**\n\n"

        if view_type in ["mlb", "all"]:
            msg += "🔶 **MLB Players**\n"
            msg += "🔹 **Batters**\n" + "\n".join(batters) + "\n\n"
            msg += "🔹 **Pitchers**\n" + "\n".join(pitchers) + "\n\n"

        if view_type in ["farm", "all"]:
            msg += "🟩 **Farm System**\n"
            if purchased:
                msg += "🔷 **Purchased**\n" + "\n".join(purchased) + "\n\n"
            if farmed:
                msg += "🔷 **Farm**\n" + "\n".join(farmed) + "\n\n"
            if devs:
                msg += "🔷 **Development**\n" + "\n".join(devs) + "\n\n"

        await interaction.response.send_message(msg.strip(), ephemeral=True)

async def setup(bot):
    await bot.add_cog(Roster(bot))

