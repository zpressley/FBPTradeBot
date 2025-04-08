# bot.py

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load env vars from local .env (optional for local dev)
load_dotenv()

# Set up Discord token
TOKEN = os.getenv("DISCORD_TOKEN")

# Write Google credentials from env (Render secret)
google_creds = os.getenv("GOOGLE_CREDS_JSON")
if google_creds:
    with open("google_creds.json", "w") as f:
        f.write(google_creds)

# Write Yahoo token from env
yahoo_token = os.getenv("YAHOO_TOKEN_JSON")
if yahoo_token:
    with open("token.json", "w") as f:
        f.write(yahoo_token)

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Only sync slash commands manually or during dev
@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online.")

# Load commands when the bot is ready
@bot.event
async def setup_hook():
    await bot.load_extension("commands.trade")
    await bot.load_extension("commands.roster")
    await bot.load_extension("commands.player")
    await bot.load_extension("commands.standings")
    # Add more cogs here as needed

if __name__ == "__main__":
    bot.run(TOKEN)



