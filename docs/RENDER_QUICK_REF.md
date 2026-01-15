# Render Deployment - Quick Reference Card

## 🚀 Quick Deploy (5 Minutes)

```bash
# 1. Push to GitHub
git add .
git commit -m "Ready for Render"
git push

# 2. Create service on Render
# - Go to render.com → New + → Web Service
# - Connect GitHub repo
# - Name: fbp-trade-bot
# - Start Command: python health.py
# - Plan: Free (or Starter for 100% uptime)

# 3. Set environment variables (Required!)
DISCORD_TOKEN=your_discord_token_here
BOT_API_KEY=$(openssl rand -hex 32)  # Generate secure key
GOOGLE_CREDS_JSON={"type":"service_account"...}  # Minified!
YAHOO_TOKEN_JSON={"access_token"...}  # Minified!

# 4. Deploy!
# Render auto-deploys on git push
```

---

## 📋 Environment Variables Template

Copy-paste these into Render dashboard:

```bash
# Required
DISCORD_TOKEN=
BOT_API_KEY=
GOOGLE_CREDS_JSON=
YAHOO_TOKEN_JSON=

# Optional (Render sets automatically)
PORT=8000
```

---

## 🔑 Generate BOT_API_KEY

```bash
# Linux/Mac
openssl rand -hex 32

# Or use Python
python3 -c "import secrets; print(secrets.token_hex(32))"

# Copy the output and use it for BOT_API_KEY
```

---

## 📝 Minify JSON Credentials

**Google Creds:**
```bash
cat google_creds.json | jq -c
# Or: python3 -c "import json; print(json.dumps(json.load(open('google_creds.json'))))"
```

**Yahoo Token:**
```bash
cat token.json | jq -c
# Or: python3 -c "import json; print(json.dumps(json.load(open('token.json'))))"
```

Copy the ENTIRE one-line output (no newlines!) into Render.

---

## ✅ Verify Deployment

**Check Logs:**
```
✅ Bot is online as FBP Trade Bot#1234
✅ FastAPI server thread started
```

**Test Health Endpoint:**
```bash
curl https://fbp-trade-bot.onrender.com/health
```

**Expected Response:**
```json
{
  "status": "ok",
  "discord_bot": {
    "connected": true,
    "user": "FBP Trade Bot#1234",
    "guilds": 1
  }
}
```

**Test Discord:**
```
/roster view:MLB
# Should return your roster!
```

---

## 🔧 Common Issues

### Bot Not Starting
**Error:** `❌ DISCORD_TOKEN not set in environment`  
**Fix:** Set `DISCORD_TOKEN` in Render dashboard

### API 401 Errors
**Error:** `Invalid API key`  
**Fix:** Set same `BOT_API_KEY` in both Render AND Cloudflare Worker

### Import Errors
**Error:** `ModuleNotFoundError: No module named 'commands'`  
**Fix:** Ensure `commands/__init__.py` exists:
```python
# commands/__init__.py
"""FBP Trade Bot Commands"""
```

### Health Check Failing
**Error:** `Health check failed`  
**Fix:** Set health check path to `/health` in Render dashboard

### Bot Sleeps After 15 Minutes (Free Tier)
**Solution 1:** Upgrade to Starter plan ($7/month) for 100% uptime  
**Solution 2:** Use UptimeRobot (free) to ping `/health` every 5 minutes

---

## 📊 UptimeRobot Setup (Free Tier Keep-Alive)

1. Sign up: https://uptimerobot.com
2. Add monitor:
   ```
   Type: HTTP(s)
   Name: FBP Trade Bot
   URL: https://fbp-trade-bot.onrender.com/health
   Interval: 5 minutes
   ```
3. Bot stays awake 24/7!

---

## 🎯 File Structure (What Render Needs)

```
fbp-trade-bot/
├── health.py              ← Start command (main file!)
├── requirements.txt       ← Python dependencies
├── render.yaml           ← Optional: auto-config
├── commands/             ← Discord commands
│   ├── __init__.py       ← Must exist!
│   ├── trade.py
│   ├── roster.py
│   ├── player.py
│   ├── standings.py
│   ├── draft.py
│   ├── board.py
│   └── auction.py
├── draft/                ← Draft system
│   ├── draft_manager.py
│   ├── prospect_database.py
│   ├── pick_validator.py
│   └── board_manager.py
├── auction_manager.py    ← Auction portal
├── data/                 ← JSON data files
│   ├── combined_players.json
│   ├── auction_current.json
│   ├── draft_state_*.json
│   └── manager_boards_*.json
└── .gitignore            ← NEVER commit credentials!
```

---

## 🔐 Security Checklist

- [ ] `DISCORD_TOKEN` - Never commit to git
- [ ] `BOT_API_KEY` - Strong random key (32+ chars)
- [ ] `google_creds.json` - In .gitignore
- [ ] `token.json` - In .gitignore
- [ ] `.env` file - In .gitignore
- [ ] Same `BOT_API_KEY` in Render and Cloudflare Worker

---

## 📡 API Endpoints Available

**Public (No Auth):**
- `GET /` - Basic health
- `GET /health` - Detailed health

**Protected (Need X-API-Key):**
- `GET /api/auction/current`
- `POST /api/auction/bid`
- `POST /api/auction/match`
- `GET /api/draft/prospect/state`
- `POST /api/draft/prospect/validate`
- `GET /api/draft/boards/{team}`
- `POST /api/draft/boards/{team}`

---

## 📚 Documentation

- **Full Guide:** `RENDER_DEPLOYMENT.md`
- **Architecture:** `HEALTH_ARCHITECTURE.md`
- **This Card:** `RENDER_QUICK_REF.md`

---

## 🆘 Need Help?

1. Check Render logs first
2. Test health endpoint
3. Verify environment variables
4. Check Discord bot permissions
5. Review full deployment guide

---

## ✨ Success Indicators

When everything works:
- ✅ Render shows "Live" status
- ✅ Health endpoint returns 200 OK
- ✅ Discord bot shows online (green dot)
- ✅ Slash commands work: `/roster`, `/trade`, `/draft`
- ✅ Website can fetch auction/draft data
- ✅ Logs show all cogs loaded
- ✅ No errors in Render logs

🎉 **You're deployed!**
