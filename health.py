import os
import asyncio
import discord
from discord.ext import commands
from fastapi import FastAPI
import uvicorn

# ---- Discord Bot Setup ----
TOKEN = os.getenv("DISCORD_TOKEN")
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

@bot.event
async def setup_hook():
    await bot.load_extension("commands.trade")
    await bot.load_extension("commands.roster")
    await bot.load_extension("commands.player")
    await bot.load_extension("commands.standings")

# ---- FastAPI Web Server ----
app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok", "bot": str(bot.user)}

# ---- Orchestrate Both ----
async def start_all():
    await bot.start(TOKEN)

def run_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
    server = uvicorn.Server(config)
    return server.serve()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_all())
    loop.run_until_complete(run_server())
