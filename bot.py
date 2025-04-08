# bot.py

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

import os

# Write Google credentials to a file if provided via env
google_creds = os.getenv("GOOGLE_CREDS_JSON")
if google_creds:
    with open("google_creds.json", "w") as f:
        f.write(google_creds)

# Write Yahoo token to a file if provided via env
yahoo_token = os.getenv("YAHOO_TOKEN_JSON")
if yahoo_token:
    with open("token.json", "w") as f:
        f.write(yahoo_token)


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} is online.")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

# Load trade command + core logic
async def main():
    await bot.load_extension("commands.trade")   # Main /trade command (text input)
    await bot.load_extension("commands.roster") # /roster command (text input)
    await bot.load_extension("commands.player") # /player command (text input)
    await bot.load_extension("commands.standings") # /standings command (text input)
    # Add any other cogs here if needed

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
    bot.run(TOKEN)




