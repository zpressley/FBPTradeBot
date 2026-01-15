# FBP Trade Bot - Render Deployment Guide

## Overview
This guide walks you through deploying the FBP Trade Bot to Render with 100% uptime.

The bot runs `health.py` which provides:
- ✅ Discord bot with all slash commands (trade, roster, player, standings, draft, board, auction)
- ✅ FastAPI server with health checks AND full API endpoints for website
- ✅ Auction Portal APIs (`/api/auction/*`)
- ✅ Prospect Draft APIs (`/api/draft/prospect/*`)  
- ✅ Draft Board APIs (`/api/draft/boards/*`)
- ✅ API key authentication for secure website integration
- ✅ Graceful shutdowns and error handling

**Architecture:**
- Discord bot runs in main thread
- FastAPI server runs in background daemon thread
- Both share the same process for efficiency

---

## Prerequisites

1. **GitHub Repository** with your bot code
2. **Render Account** (free tier works)
3. **Discord Bot Token** 
4. **Google Service Account Credentials** (JSON)
5. **Yahoo API Token** (JSON)

---

## Step 1: Prepare Your Repository

### Ensure these files exist:

```
fbp-trade-bot/
├── health.py              ← Main entry point (✅ updated)
├── requirements.txt       ← Python dependencies
├── render.yaml           ← Render configuration (✅ created)
├── bot.py                ← Alternative entry point (backup)
├── commands/             ← Discord commands
│   ├── trade.py
│   ├── roster.py
│   ├── player.py
│   └── standings.py
├── google_creds.json     ← Gitignored (set via env vars)
└── token.json            ← Gitignored (set via env vars)
```

### Update .gitignore:

```gitignore
# Credentials (NEVER commit these)
google_creds.json
token.json
.env

# Python
__pycache__/
*.pyc
*.pyo
venv/
.pytest_cache/

# Data
data/*.json
!data/.gitkeep
```

---

## Step 2: Deploy to Render

### Method A: Use render.yaml (Recommended)

1. **Push render.yaml to your repository**
   ```bash
   git add render.yaml
   git commit -m "Add Render configuration"
   git push
   ```

2. **Connect to Render**
   - Go to https://render.com
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Render will auto-detect `render.yaml`
   - Click "Apply"

### Method B: Manual Setup

1. **Create New Web Service**
   - Go to Render Dashboard
   - Click "New +" → "Web Service"
   - Connect your GitHub repository
   - Select branch (usually `main` or `master`)

2. **Configure Service**
   ```
   Name:           fbp-trade-bot
   Region:         Oregon (or nearest)
   Branch:         main
   Runtime:        Python 3
   Build Command:  pip install -r requirements.txt
   Start Command:  python health.py
   Plan:           Free (or Starter for production)
   ```

3. **Set Health Check Path**
   ```
   Health Check Path: /health
   ```

---

## Step 3: Configure Environment Variables

In Render Dashboard → Your Service → Environment:

### Required Variables:

```bash
# Discord Bot Token (from Discord Developer Portal)
DISCORD_TOKEN=your_discord_bot_token_here

# Bot API Key (for securing website API endpoints)
# Generate a strong random key: openssl rand -hex 32
BOT_API_KEY=your_secure_random_api_key_here

# Google Service Account (copy entire JSON as one line)
GOOGLE_CREDS_JSON={"type":"service_account","project_id":"..."}

# Yahoo API Token (copy entire JSON as one line)
YAHOO_TOKEN_JSON={"access_token":"...","refresh_token":"..."}

# Port (auto-set by Render, but can override)
PORT=8000
```

### How to Set Multi-Line JSON:

For Google Creds and Yahoo Token, you need to **minify the JSON** (remove all newlines):

**Before (multi-line):**
```json
{
  "type": "service_account",
  "project_id": "fbp-trade-tool",
  "private_key": "-----BEGIN PRIVATE KEY-----\n..."
}
```

**After (single line):**
```json
{"type":"service_account","project_id":"fbp-trade-tool","private_key":"-----BEGIN PRIVATE KEY-----\n..."}
```

**Tools to minify:**
- Online: https://codebeautify.org/jsonminifier
- Command line: `cat google_creds.json | jq -c`
- Python: `python -c "import json; print(json.dumps(json.load(open('google_creds.json'))))"`

---

## Step 4: Verify Deployment

### Monitor Logs

In Render Dashboard → Logs, you should see:

```
============================================================
🚀 FBP Trade Bot - Production Mode
============================================================
   Port: 8000
   Discord Token: ✅ Set
   Google Creds: ✅ Set
   Yahoo Token: ✅ Set
============================================================

✅ Google credentials written
✅ Yahoo token written
🌐 Starting FastAPI server on port 8000...
✅ FastAPI server thread started
🤖 Starting Discord bot (main thread)...
   ✅ Loaded: commands.trade
   ✅ Loaded: commands.roster
   ✅ Loaded: commands.player
   ✅ Loaded: commands.standings
🔄 Syncing slash commands...
✅ Slash commands synced
✅ Discord Bot is online as FBP Trade Bot#1234
   Connected to 1 guild(s)
```

### Test Health Check

Visit your Render URL: `https://fbp-trade-bot.onrender.com/health`

**Expected Response:**
```json
{
  "status": "ok",
  "discord_bot": {
    "connected": true,
    "user": "FBP Trade Bot#1234",
    "guilds": 1,
    "latency_ms": 45.23
  },
  "server": {
    "port": 8000,
    "pid": 12345
  }
}
```

### Test Discord Bot

In your Discord server:
```
/roster view:MLB
```

Should return your roster!

---

## Step 5: Keep Bot Running 24/7

### Render Free Tier Limitations

**Free tier spins down after 15 minutes of inactivity.**

### Solutions:

#### Option A: Upgrade to Starter Plan ($7/month)
- Never spins down
- 100% uptime guaranteed
- Recommended for production

#### Option B: Keep-Alive Service (Free Tier)
Use a service like UptimeRobot to ping your health check every 5 minutes:

1. Sign up at https://uptimerobot.com (free)
2. Add monitor:
   ```
   Monitor Type: HTTP(s)
   Friendly Name: FBP Trade Bot
   URL: https://fbp-trade-bot.onrender.com/health
   Monitoring Interval: 5 minutes
   ```
3. Bot will stay awake 24/7

---

## Troubleshooting

### Bot Not Connecting

**Check logs for:**
```
❌ DISCORD_TOKEN not set in environment
```

**Solution:** Set `DISCORD_TOKEN` in Render dashboard

---

### Import Errors

**Error:**
```
ModuleNotFoundError: No module named 'commands'
```

**Solution:** Ensure your repository structure matches:
```
fbp-trade-bot/
├── health.py
├── commands/
│   ├── __init__.py     ← Must exist!
│   ├── trade.py
│   ├── roster.py
│   ├── player.py
│   └── standings.py
```

Create `commands/__init__.py`:
```python
# commands/__init__.py
"""FBP Trade Bot Commands"""
```

---

### Commands Not Syncing

**Error:**
```
⚠️ Failed to sync commands: ...
```

**Solutions:**
1. Bot needs `applications.commands` scope in Discord Dev Portal
2. Bot needs admin/manage server permissions in your guild
3. Wait 1-2 minutes after deploy (Discord API can be slow)

---

### Health Check Failing

**Error in Render:**
```
Health check failed: Connection refused
```

**Solution:** Verify `PORT` environment variable:
```bash
PORT=8000  # Must match what health.py expects
```

---

## Deployment Checklist

- [ ] `render.yaml` pushed to repository
- [ ] Service created in Render dashboard
- [ ] `DISCORD_TOKEN` set in environment
- [ ] `GOOGLE_CREDS_JSON` set (minified JSON)
- [ ] `YAHOO_TOKEN_JSON` set (minified JSON)
- [ ] Health check path set to `/health`
- [ ] Logs show bot connected successfully
- [ ] Health check endpoint returns 200 OK
- [ ] Discord slash commands work in server
- [ ] (Optional) UptimeRobot monitor configured

---

## Monitoring

### Render Metrics

Monitor in dashboard:
- **CPU Usage**: Should be <20% idle
- **Memory**: Should be <200MB
- **Response Time**: Health checks <100ms
- **Uptime**: Should be 100% (Starter plan)

### Discord Presence

Bot should show as "Online" with status:
```
🟢 Watching FBP League | /help for commands
```

---

## Support

**Bot Issues:** Check Render logs first
**Discord API Issues:** https://discord.com/developers/docs
**Render Platform:** https://render.com/docs

---

## Next Steps

1. **Add More Commands**: Extend `commands/` directory
2. **Database Integration**: Add PostgreSQL addon in Render
3. **Scheduled Tasks**: Use Render Cron Jobs for daily data updates
4. **Monitoring**: Integrate with Sentry for error tracking

---

## Summary

Your bot is now:
- ✅ Running 24/7 on Render
- ✅ Health check enabled
- ✅ Discord commands synced
- ✅ Auto-deploy on git push
- ✅ Credentials loaded from environment

**Main URL:** `https://fbp-trade-bot.onrender.com`  
**Health Check:** `https://fbp-trade-bot.onrender.com/health`

🎉 **Deployment Complete!**
