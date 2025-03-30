import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from commands.trade import handle_trade

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user} is online.')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} slash commands.')
    except Exception as e:
        print(f'Error syncing commands: {e}')

@bot.tree.command(name="trade", description="Submit a trade between 2 or 3 teams")
@app_commands.describe(
    team1_assets="Players/WB team 1 sends",
    team2="Name of team 2",
    team2_assets="Players/WB team 2 sends",
    team3="(Optional) Name of third team",
    team3_assets="(Optional) Players/WB team 3 sends"
)
async def trade(interaction: discord.Interaction, team1_assets: str, team2: str, team2_assets: str, team3: str = None, team3_assets: str = None):
    await handle_trade(interaction, team1_assets, team2, team2_assets, team3, team3_assets)

bot.run(TOKEN)
