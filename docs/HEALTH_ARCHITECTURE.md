# Health.py Architecture - Complete Solution

## What health.py Provides

Your `health.py` is the **single production entrypoint** that runs both:

1. **Discord Bot** (main thread)
   - All slash commands: `/trade`, `/roster`, `/player`, `/standings`, `/draft`, `/board`, `/auction`
   - Auto-syncs commands on startup
   - Handles all Discord interactions

2. **FastAPI Web Server** (background thread)
   - Health check endpoints for Railway monitoring
   - Full API suite for website integration
   - API key authentication for security

---

## API Endpoints Available

This list covers the original core APIs this doc was written around. `health.py` has since grown many more routers (`api_admin_bulk.py`, `api_buyin.py`, `api_trade.py`, `api_settings.py`, `api_client_log.py`, and more under `self_service/`) — check `health.py`'s `app.include_router(...)` calls for the full current surface.

### Health Checks (Public)
- `GET /` - Basic health status
- `GET /health` - Detailed health (bot status, latency, guilds)

### Auction Portal APIs (Authenticated)
- `GET /api/auction/current` - Get current auction state
- `POST /api/auction/bid` - Place OB or CB bid from website
- `POST /api/auction/match` - Record OB manager match/forfeit decision

### Prospect Draft APIs (Authenticated)
- `GET /api/draft/prospect/state` - Get current draft state
- `POST /api/draft/prospect/validate` - Validate a pick before confirming

### Draft Board APIs (Authenticated)
- `GET /api/draft/boards/{team}` - Get team's personal draft board
- `POST /api/draft/boards/{team}` - Update team's draft board

---

## Authentication Flow

All API endpoints (except `/` and `/health`) require:
```
Header: X-API-Key: your_bot_api_key
```

**Setup:**
1. Generate secure key: `openssl rand -hex 32`
2. Set in Railway: `BOT_API_KEY=abc123...`
3. Set in Cloudflare Worker: Same key
4. Worker includes key in all requests

**Security:**
- Website users never see the API key
- Only Cloudflare Worker has the key
- Worker validates user auth via Discord OAuth
- Worker then calls bot API with `X-API-Key` header

---

## Threading Architecture

```
Main Process
├── FastAPI Server (background daemon thread)
│   ├── Uvicorn server on port 8000
│   ├── Handles HTTP requests
│   └── Calls bot functions when needed
│
└── Discord Bot (main thread)
    ├── Event loop handles Discord gateway
    ├── Slash commands
    └── Shares data with FastAPI via imports
```

**Why This Works:**
- FastAPI runs in separate thread, won't block Discord
- Discord bot runs in main thread, proper signal handling
- Daemon thread ensures clean shutdown when bot exits
- Both share same Python process (memory efficient)

---

## Data Flow Examples

### Website User Places Auction Bid

1. User clicks "Place Bid" on website
2. Browser → Cloudflare Worker (Discord OAuth validates user)
3. Worker → Bot API: `POST /api/auction/bid` with `X-API-Key`
4. Bot validates key, processes bid, updates `data/auction_current.json`
5. Bot commits/pushes to GitHub (syncs to website)
6. Bot posts log to Discord auction channel
7. Response → Worker → User (success message)

### Discord User Uses Draft Command

1. User types `/draft pick Charlie Condon`
2. Discord → Bot gateway
3. Bot loads `DraftManager`, `ProspectDatabase`, `PickValidator`
4. Validates pick against FBP rules
        Records pick in `data/draft_state_prospect_2026.json`
6. Updates draft board in Discord channel
7. Commits/pushes to GitHub (website stays in sync)

### Website Fetches Draft Board

1. User visits `draft.html`
2. JavaScript → Cloudflare Worker
3. Worker → Bot API: `GET /api/draft/boards/WIZ` with `X-API-Key`
    4. Bot loads `BoardManager`, reads `data/manager_boards_2026.json`
5. Returns board data as JSON
6. Website renders board with drag-drop UI

---

## Environment Variables Required

```bash
# Discord
DISCORD_TOKEN=...           # Required: Bot token from Discord Dev Portal

# Security
BOT_API_KEY=...             # Required: Secure random key for API auth

# Credentials
GOOGLE_CREDS_JSON=...       # Required: Service account (minified JSON)
YAHOO_TOKEN_JSON=...        # Required: Yahoo API token (minified JSON)

# Server
PORT=8000                   # Optional: Railway sets this automatically
```

---

## Deployment Checklist

- [ ] `health.py` is your start command: `python health.py`
- [ ] All environment variables set in Railway
- [ ] Health check path set to `/health`
- [ ] Same `BOT_API_KEY` in both Railway and Cloudflare Worker
- [ ] Bot has proper Discord permissions (admin/manage server)
- [ ] GitHub repo connected for auto-deploy

---

## Monitoring

### Health Check Response (Good)
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

### Logs Should Show (Good)
```
============================================================
🚀 FBP Trade Bot - Production Mode (Full API)
============================================================
   Port: 8000
   Discord Token: ✅ Set
   API Key: ✅ Set
   Google Creds: ✅ Set
   Yahoo Token: ✅ Set
============================================================

✅ Google credentials written
✅ Yahoo token written
✅ FastAPI server thread started
🤖 Starting Discord bot (main thread)...
   ✅ Loaded: commands.trade
   ✅ Loaded: commands.roster
   ✅ Loaded: commands.player
   ✅ Loaded: commands.standings
   ✅ Loaded: commands.draft
   ✅ Loaded: commands.board
   ✅ Loaded: commands.auction
🔄 Syncing slash commands...
✅ Slash commands synced
✅ Bot is online as FBP Trade Bot#1234
   Connected to 1 guild(s)
```

---

## Troubleshooting

### "Bot not responding to commands"
- Check logs: Did slash commands sync?
- Check permissions: Does bot have `applications.commands` scope?
- Wait 2 minutes after deploy (Discord API can be slow)

### "API returns 401 Unauthorized"
- Verify `BOT_API_KEY` is set in Railway
- Verify Cloudflare Worker uses same key
- Check header format: `X-API-Key` (capital K)

### "Bot disconnects unexpectedly"
- Check Railway's deployment logs for crashes/restarts rather than assuming a free-tier sleep issue (this section was written for Render's free-tier spin-down behavior, which doesn't apply the same way on Railway)
- Remember every git push to `main` triggers a Railway redeploy — a burst of automated commits (hourly standings, daily pipeline) means frequent restarts by design, not a bug

### "Import errors on startup"
- Ensure all dependencies in `requirements.txt`
- Check file structure (commands folder with __init__.py)
- Verify all imported modules exist

---

## Key Differences from bot.py

| Feature | bot.py | health.py |
|---------|--------|-----------|
| Purpose | Discord only | Discord + API server |
| Deployment | Local testing | Production (Railway) |
| Health checks | None | `/` and `/health` |
| API endpoints | None | Full auction/draft/board APIs |
| Authentication | None | API key required |
| Threading | Single thread | Multi-threaded |
| Railway compatible | No | Yes (health checks) |

**Use bot.py for:** Local development, testing commands  
**Use health.py for:** Production deployment on Railway

---

## Summary

`health.py` is your complete production solution:
- ✅ One process, two threads (efficient)
- ✅ Discord bot + Web API server
- ✅ All slash commands loaded
- ✅ All website APIs available
- ✅ Secure authentication
- ✅ Health checks for monitoring
- ✅ Graceful shutdown handling
- ✅ Auto-deploy friendly

Just run `python health.py` and everything works! 🎉
