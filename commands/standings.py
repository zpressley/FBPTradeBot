# commands/standings.py

import discord
from discord.ext import commands
from discord import app_commands
import json

DATA_FILE = "data/standings.json"

class Standings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="standings", description="View latest saved Yahoo standings with win percentage")
    async def standings(self, interaction: discord.Interaction):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            await interaction.response.send_message("❌ Standings data not available yet.", ephemeral=True)
            return

        date = data.get("date", "Unknown")
        standings = data.get("standings", [])

        msg = f"📊 **Standings as of {date}**\n\n"
        for s in standings:
            msg += f"{s['rank']}. {s['team']} ({s['record']}) — {s['win_pct']:.3f}\n"

        await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Standings(bot))

