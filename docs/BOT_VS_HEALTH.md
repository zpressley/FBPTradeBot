# bot.py vs health.py - What's the Difference?

## TL;DR

**Use `bot.py` for:** Local development and testing  
**Use `health.py` for:** Production deployment on Railway

---

## Side-by-Side Comparison

| Feature | bot.py | health.py |
|---------|--------|-----------|
| **Primary Purpose** | Discord bot only | Discord bot + Web API server |
| **Deployment Target** | Local machine | Railway (production) |
| **Health Endpoints** | ❌ None | ✅ `/` and `/health` |
| **API Endpoints** | ❌ None | ✅ Auction/Draft/Board APIs |
| **Threading** | Single thread | Multi-threaded (bot + server) |
| **Railway Compatible** | ❌ No health checks | ✅ Full health monitoring |
| **Website Integration** | ❌ Not possible | ✅ Complete API suite |
| **Authentication** | ❌ None | ✅ API key protection |
| **Auto-deploy Ready** | ❌ No | ✅ Yes |
| **Credential Handling** | Reads from local files | Writes from env vars |

---

## bot.py - Local Development

**Purpose:** Quick local testing of Discord commands

**What it does:**
```python
# Simple setup
TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(...)

# Load credentials from local files
# (google_creds.json, token.json must exist)

# Load Discord commands
await bot.load_extension("commands.trade")
await bot.load_extension("commands.roster")
# ... etc

# Run bot
bot.run(TOKEN)
```

**Usage:**
```bash
# Set token in .env
echo "DISCORD_TOKEN=your_token" > .env

# Make sure credentials exist
ls google_creds.json token.json

# Run
python bot.py
```

**Good for:**
- ✅ Testing slash commands locally
- ✅ Quick iterations during development
- ✅ Debugging Discord interactions
- ✅ Simple setup (no extra complexity)

**NOT good for:**
- ❌ Production deployment (no health checks)
- ❌ Website integration (no API server)
- ❌ Railway deployment (needs health endpoint)
- ❌ Monitoring (can't check if alive)

---

## health.py - Production Deployment

**Purpose:** Full production bot with website API integration

**What it does:**
```python
# Load from environment (Railway environment variables)
TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("BOT_API_KEY")

# Write credentials from env vars
google_creds = os.getenv("GOOGLE_CREDS_JSON")
with open("google_creds.json", "w") as f:
    f.write(google_creds)

# Setup Discord bot
bot = commands.Bot(...)

# Setup FastAPI server
app = FastAPI()

# Health endpoints
@app.get("/")
def health(): ...

@app.get("/health")
def detailed_health(): ...

# API endpoints
@app.post("/api/auction/bid")
async def api_place_bid(...): ...

@app.get("/api/draft/boards/{team}")
async def get_draft_board(...): ...

# Run both in parallel
# - FastAPI in background thread
# - Discord bot in main thread
```

**Usage on Railway:**
```bash
# Start command
python health.py

# Environment variables (set in Railway dashboard)
DISCORD_TOKEN=...
BOT_API_KEY=...
GOOGLE_CREDS_JSON=...
YAHOO_TOKEN_JSON=...
```

**Good for:**
- ✅ Production deployment on Railway
- ✅ 24/7 operation with health monitoring
- ✅ Website integration via API
- ✅ Auction portal from website
- ✅ Draft board management
- ✅ Secure API access
- ✅ Auto-deploy on git push
- ✅ Zero-downtime updates

---

## Feature Breakdown

### Discord Commands (Both Files)

Both load the same Discord commands:
- `/trade` - Submit trades
- `/roster` - View rosters
- `/player` - Look up players
- `/standings` - View standings
- `/draft` - Draft management
- `/board` - Personal draft boards
- `/auction` - Auction portal

**Difference:** health.py loads them with better error handling

---

### API Endpoints (health.py Only)

#### Auction Portal
```http
GET  /api/auction/current           # Get auction state
POST /api/auction/bid               # Place bid from website
POST /api/auction/match             # OB manager decision
```

**Powers:** Weekly auction portal on website

#### Draft System
```http
GET  /api/draft/prospect/state      # Current draft state
POST /api/draft/prospect/validate   # Validate pick
```

**Powers:** Live draft board on website, pick validation

#### Personal Boards
```http
GET  /api/draft/boards/{team}       # Get board
POST /api/draft/boards/{team}       # Update board
```

**Powers:** Draft board editor on website

---

## When to Use Each

### Scenario 1: Testing New Command
**Use:** `bot.py`
```bash
# Quick local test
python bot.py
# Test command in Discord
/roster view:MLB
# Ctrl+C to stop
```

### Scenario 2: Production Deployment
**Use:** `health.py`
```bash
# Deploy to Railway
git push
# Railway auto-deploys
# Bot runs 24/7
```

### Scenario 3: Website Development
**Use:** `health.py`
```bash
# Must have API endpoints
# Website needs to call:
# - /api/auction/bid
# - /api/draft/boards/{team}
# Only health.py provides these
```

### Scenario 4: Debugging
**Use:** `bot.py` first, then `health.py`
```bash
# Test commands locally
python bot.py

# When working, deploy
# Set up health.py on Railway
```

---

## Migration Path

**If you currently use bot.py locally:**

1. Keep using `bot.py` for local testing ✅
2. Deploy `health.py` to Railway ✅
3. Configure environment variables in Railway
4. Keep local `google_creds.json` and `token.json` for bot.py
5. Use environment variables for health.py on Railway

**Both can coexist!** They're designed for different environments.

---

## Quick Troubleshooting

**Local bot.py fails:**
- Check: Do `google_creds.json` and `token.json` exist?
- Check: Is `DISCORD_TOKEN` in `.env`?

**Railway health.py fails:**
- Check: Are env vars set in the Railway dashboard?
- Check: Did you minify the JSON credentials?
- Check: Is `PORT` set correctly (usually auto-set)?

**API returns 500:**
- Check: Is `BOT_API_KEY` set?
- Check: Are required modules installed? (`auction_manager`, `draft/*`)

---

## Summary Table

| When I Want To... | Use This |
|-------------------|----------|
| Test commands locally | `bot.py` |
| Deploy to production | `health.py` |
| Enable website features | `health.py` |
| Debug quickly | `bot.py` |
| Run 24/7 | `health.py` on Railway |
| Integrate with web UI | `health.py` APIs |
| Just use Discord | Either works! |

---

## What health.py Gives You (That bot.py Doesn't)

1. **Health Monitoring** - Railway knows bot is alive
2. **API Server** - Website can interact with bot
3. **Auction Integration** - Web portal works
4. **Draft Board Editor** - Managers can use website
5. **Secure APIs** - API key protection
6. **Production Ready** - Proper error handling
7. **Auto-deploy** - Push to GitHub = auto-update

---

## Final Answer

**For Railway deployment:**

✅ `health.py` is already the configured start command (see `nixpacks.toml`)
✅ Set all environment variables in Railway's dashboard
✅ Railway redeploys automatically on every push to `main`

**Your bot.py can stay for local testing - both are useful!**
