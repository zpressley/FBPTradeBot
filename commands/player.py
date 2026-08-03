# commands/player.py

import discord
from discord.ext import commands
from discord import app_commands
from commands.lookup import fuzzy_lookup_all
from commands.utils import mlb_headshot_url

class PlayerLookup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="player", description="Look up a player across all teams")
    @app_commands.describe(name="Player name to look up")
    async def player(self, interaction: discord.Interaction, name: str):
        name = name.strip()
        matches_90 = fuzzy_lookup_all(name, threshold=0.9)
        matches_80 = fuzzy_lookup_all(name, threshold=0.8)

        if matches_90 and len(matches_90) == 1:
            match = matches_90[0]
            embed = discord.Embed(
                title=f"🔍 Result for: {name}",
                description=f"**{match['formatted']}**\n🔹 Owned by: `{match['manager']}`",
                color=discord.Color.red()
            )
            thumb = mlb_headshot_url(match.get('mlb_id'))
            if thumb:
                embed.set_thumbnail(url=thumb)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if matches_90 and len(matches_90) > 1:
            msg = f"🔍 Multiple possible matches for `{name}` (90%+):\n\n"
            for m in matches_90:
                msg += f"• **{m['formatted']}** (Team: `{m['manager']}`)\n"
            await interaction.response.send_message(msg, ephemeral=True)
            return

        if matches_80:
            match = matches_80[0]
            description = f"**{match['formatted']}**\n🔹 Owned by: `{match['manager']}`"
            if match['name'].lower() != name.lower():
                description += f"\n⚠️ Did you mean: **{match['formatted']}**?"
            embed = discord.Embed(
                title=f"🔍 Closest match to: {name}",
                description=description,
                color=discord.Color.red()
            )
            thumb = mlb_headshot_url(match.get('mlb_id'))
            if thumb:
                embed.set_thumbnail(url=thumb)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Nothing close found
        close = fuzzy_lookup_all(name, threshold=0.7)
        msg = f"❌ No match found for `{name}`.\n\n"
        if close:
            msg += "🔎 Possible matches:\n"
            for m in close:
                msg += f"• **{m['formatted']}** (Team: `{m['manager']}`)\n"
        else:
            msg += "💡 No similar names found in the system. Try using `/roster`."

        await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot):
    await bot.add_cog(PlayerLookup(bot))
