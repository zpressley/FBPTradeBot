import discord
from discord.ext import commands
from discord import app_commands
import json
from commands.utils import DISCORD_ID_TO_TEAM


def load_combined_data():
    try:
        with open("data/combined_players.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def player_belongs_to_team(player, team_abbr):
    team_norm = str(team_abbr or "").upper()
    fbp_team = str(player.get("FBP_Team") or "").upper()
    manager = str(player.get("manager") or "").upper()
    return fbp_team == team_norm or manager == team_norm


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
    app_commands.Choice(name="Weekend Warriors", value="WAR"),
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

        combined_data = load_combined_data()
        roster = [p for p in combined_data if player_belongs_to_team(p, team_abbr)]
        if not roster:
            await interaction.response.send_message(f"❌ No players found for team `{team_abbr}`.", ephemeral=True)
            return

        def format_player(p):
            position = p.get("position", "?")
            name = p.get("name", "Unknown")
            team = p.get("team", "FA")
            years = p.get("years_simple") or p.get("contract_type") or "NA"
            return f"{position} {name} [{team}] [{years}]"

        def contract_key(player):
            return str(player.get("contract_type") or "").strip().upper()

        mlb = [p for p in roster if p.get("player_type") == "MLB"]
        farm = [p for p in roster if p.get("player_type") == "Farm"]

        batters = [format_player(p) for p in mlb if p.get("position") not in ["SP", "RP", "P"]]
        pitchers = [format_player(p) for p in mlb if p.get("position") in ["SP", "RP", "P"]]

        purchased = [format_player(p) for p in farm if contract_key(p) in {"PC", "PURCHASED CONTRACT"}]
        farmed = [format_player(p) for p in farm if contract_key(p) in {"FC", "FARM CONTRACT"}]
        devs = [format_player(p) for p in farm if contract_key(p) in {"DC", "DEVELOPMENT CONTRACT"}]

        msg = f"📋 **{team_abbr} Roster**\n\n"

        if view_type in ["mlb", "all"]:
            msg += "🔶 **MLB Players**\n"
            msg += "🔹 **Batters**\n" + ("\n".join(batters) if batters else "_None_") + "\n\n"
            msg += "🔹 **Pitchers**\n" + ("\n".join(pitchers) if pitchers else "_None_") + "\n\n"

        if view_type in ["farm", "all"]:
            msg += "🟩 **Farm System**\n"
            if purchased:
                msg += "🔷 **Purchased**\n" + "\n".join(purchased) + "\n\n"
            if farmed:
                msg += "🔷 **Farm**\n" + "\n".join(farmed) + "\n\n"
            if devs:
                msg += "🔷 **Development**\n" + "\n".join(devs) + "\n\n"
            if not purchased and not farmed and not devs:
                msg += "_No farm players found._\n\n"

        await interaction.response.send_message(msg.strip(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Roster(bot))