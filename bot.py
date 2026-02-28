# bot.py - FBP Trade Bot with Draft System

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Write credentials from env
google_creds = os.getenv("GOOGLE_CREDS_JSON")
if google_creds:
    with open("google_creds.json", "w") as f:
        f.write(google_creds)

yahoo_token = os.getenv("YAHOO_TOKEN_JSON")
if yahoo_token:
    with open("token.json", "w") as f:
        f.write(yahoo_token)

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online.")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="FBP Drafts | DM me for help"
        )
    )

@bot.event
async def setup_hook():
    # Register persistent views so buttons survive bot restarts
    from commands.trade_logic import AdminReviewView
    bot.add_view(AdminReviewView())

    await bot.load_extension("commands.draft")
    await bot.load_extension("commands.board")
    await bot.load_extension("commands.trade")
    await bot.load_extension("commands.roster")
    await bot.load_extension("commands.player")
    await bot.load_extension("commands.standings")
    
    print("🔄 Syncing slash commands...")
    await bot.tree.sync()
    print("✅ Slash commands synced")

@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Manually sync slash commands (owner only)"""
    await bot.tree.sync()
    await ctx.send("✅ Commands synced!")

if __name__ == "__main__":
    bot.run(TOKEN)