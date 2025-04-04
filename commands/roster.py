# commands/roster.py

import discord
from discord.ext import commands
from discord import app_commands
import json
from commands.utils import DISCORD_ID_TO_TEAM

# Load data
with open("data/combined_players.json", "r") as f:
    combined_data = json.load(f)

with open("data/yahoo_players.json", "r") as f:
    yahoo_data = json.load(f)

TEAM_CHOICES = [
    app_commands.Choice(name="Hammers", value="HAM"),
    app_commands.Choice(name="Rick Vaughn", value="RV"),
    app_commands.Choice(name="Btwn2Jackies", value="B2J"),
    app_commands.Choice(name="Country Fried Lamb", value="CFL"),
    app_commands.Choice(name="Law-Abiding Citizens", value="LAW"),
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
    @app_commands.describe(
        team="Optional: Choose a team (defaults to your own)",
        view="Choose a roster type"
    )
    async def roster(self, interaction: discord.Interaction, view: app_commands.Choice[str], team: app_commands.Choice[str] = None):
        view_type = view.value

        if team:
            team_abbr = team.value
            team_name = team.name
        else:
            team_abbr = DISCORD_ID_TO_TEAM.get(interaction.user.id)
            team_name = team_abbr if team_abbr else "Your Team"

        if not team_abbr or team_abbr not in combined_data:
            await interaction.response.send_message("❌ Unable to determine your team. Please select one manually.", ephemeral=True)
            return

        combined = combined_data.get(team_abbr, [])
        yahoo_roster = yahoo_data.get(team_abbr, [])

        if view_type == "mlb":
            batters, pitchers = split_mlb(combined, yahoo_roster)
            msg = f"📋 **{team_name} Roster — MLB Only**\n\n"
            msg += "🔹 **Batters**\n" + "\n".join(batters) + "\n\n"
            msg += "🔹 **Pitchers**\n" + "\n".join(pitchers)

        elif view_type == "farm":
            purchased, farm, dev = split_farm(combined, yahoo_roster)
            msg = f"📋 **{team_name} Roster — Farm System**\n\n"
            if purchased:
                msg += "🔷 **Purchased**\n" + "\n".join(purchased) + "\n\n"
            if farm:
                msg += "🔷 **Farm**\n" + "\n".join(farm) + "\n\n"
            if dev:
                msg += "🔷 **Development**\n" + "\n".join(dev)

        elif view_type == "all":
            batters, pitchers = split_mlb(combined, yahoo_roster)
            purchased, farm, dev = split_farm(combined, yahoo_roster)

            msg = f"📋 **{team_name} Full Roster**\n\n"
            msg += "🔶 **MLB Players**\n"
            msg += "🔹 **Batters**\n" + "\n".join(batters) + "\n\n"
            msg += "🔹 **Pitchers**\n" + "\n".join(pitchers) + "\n\n"
            msg += "🟩 **Farm System**\n"
            if purchased:
                msg += "🔷 **Purchased**\n" + "\n".join(purchased) + "\n\n"
            if farm:
                msg += "🔷 **Farm**\n" + "\n".join(farm) + "\n\n"
            if dev:
                msg += "🔷 **Development**\n" + "\n".join(dev)
        else:
            msg = "❌ Unknown view type."

        await interaction.response.send_message(msg, ephemeral=True)

# ======== Helpers ========

def split_mlb(combined, yahoo_roster):
    yahoo_names = {p["name"].lower() for p in yahoo_roster}
    batters, pitchers = [], []

    for line in combined:
        name = line.split(" ", 1)[1].split(" [")[0].lower()
        if name in yahoo_names:
            pos = line.split(" ")[0]
            if pos in ["SP", "RP", "P", "RHP", "LHP"]:
                pitchers.append(line)
            else:
                batters.append(line)

    return batters, pitchers

def split_farm(combined, yahoo_roster):
    yahoo_names = {p["name"].lower() for p in yahoo_roster}
    purchased, farm, dev = [], [], []

    for line in combined:
        name = line.split(" ", 1)[1].split(" [")[0].lower()
        status = line.split(" - ")[-1].strip()
        if "[P]" in status and name not in yahoo_names:
            if "[PC]" in status:
                purchased.append(line)
            elif "[FC]" in status:
                farm.append(line)
            elif "[DC]" in status:
                dev.append(line)

    return purchased, farm, dev

async def setup(bot):
    await bot.add_cog(Roster(bot))

