# Setting Up File Structure for health.py

## Current Issue

Your `health.py` tries to import from `commands/` directory, but currently your Python files are at root level:

```
fbp-trade-bot/
├── health.py
├── bot.py
├── trade.py          ← Should be commands/trade.py
├── roster.py         ← Should be commands/roster.py
├── player.py         ← Should be commands/player.py
├── standings.py      ← Should be commands/standings.py
└── ...
```

## Solution: Organize Into Proper Structure

### Step 1: Create Directory Structure

```bash
mkdir -p commands
mkdir -p draft
mkdir -p data
```

### Step 2: Move Command Files

```bash
# Move Discord command files
mv trade.py commands/
mv roster.py commands/
mv player.py commands/
mv standings.py commands/

# Create __init__.py files
touch commands/__init__.py
touch draft/__init__.py
```

### Step 3: Create commands/__init__.py

```python
# commands/__init__.py
"""FBP Trade Bot Discord Commands"""
```

### Step 4: Update Import Paths (If Needed)

Some command files may have imports like:
```python
from utils import MANAGER_DISCORD_IDS
```

If they're in `commands/` directory now, update to:
```python
from commands.utils import MANAGER_DISCORD_IDS
# OR move utils.py to commands/ too
```

---

## Option 1: Full Restructure (Recommended)

**Target Structure:**
```
fbp-trade-bot/
├── health.py                    ← Production entry point
├── bot.py                       ← Dev/testing entry point
│
├── commands/                    ← Discord slash commands
│   ├── __init__.py
│   ├── trade.py
│   ├── roster.py
│   ├── player.py
│   ├── standings.py
│   ├── draft.py                 ← If you have draft commands
│   ├── board.py                 ← If you have board commands
│   ├── auction.py               ← If you have auction commands
│   ├── trade_logic.py           ← Helper for trade commands
│   └── utils.py                 ← Shared utilities
│
├── draft/                       ← Draft system
│   ├── __init__.py
│   ├── draft_manager.py
│   ├── prospect_database.py
│   ├── pick_validator.py
│   ├── board_manager.py
│   └── database_tracker.py
│
├── data/                        ← Data files
│   ├── combined_players.json
│   ├── standings.json
│   ├── wizbucks.json
│   └── ...
│
├── auction_manager.py           ← Auction portal logic
├── google_sheets.py             ← Google Sheets helpers
├── lookup.py                    ← Player lookup helpers
├── token_manager.py             ← Yahoo token refresh
│
├── requirements.txt             ← Python dependencies
├── render.yaml                  ← Render config
├── .env                         ← Local env vars (gitignored)
└── .gitignore                   ← Never commit credentials!
```

**Migration script:**
```bash
#!/bin/bash
# migrate_structure.sh

# Create directories
mkdir -p commands draft data

# Move command files
mv trade.py commands/ 2>/dev/null
mv roster.py commands/ 2>/dev/null
mv player.py commands/ 2>/dev/null
mv standings.py commands/ 2>/dev/null
mv trade_logic.py commands/ 2>/dev/null
mv utils.py commands/ 2>/dev/null

# Create __init__ files
echo '"""FBP Trade Bot Discord Commands"""' > commands/__init__.py
echo '"""FBP Draft System"""' > draft/__init__.py

# Draft files (if they exist in root)
mv draft_manager.py draft/ 2>/dev/null
mv prospect_database.py draft/ 2>/dev/null
mv pick_validator.py draft/ 2>/dev/null
mv board_manager.py draft/ 2>/dev/null

echo "✅ Structure migrated!"
```

---

## Option 2: Minimal Fix (Quick Fix)

If you don't want to restructure everything, modify `health.py`:

### Change This Section:

```python
# BEFORE (expects commands/ directory)
@bot.event
async def setup_hook():
    extensions = [
        "commands.trade",
        "commands.roster",
        "commands.player",
        "commands.standings"
    ]
```

### To This:

```python
# AFTER (no directory structure)
@bot.event
async def setup_hook():
    # Just load the bot basics without command extensions
    # since your files are at root level
    print("✅ Bot loaded (commands at root level)")
    
    # Manually register commands if needed
    # Or just sync what's available
    await bot.tree.sync()
```

**This allows health.py to run, but you lose the slash commands unless you either:**
1. Restructure files into `commands/` folder (recommended)
2. Import and register commands manually at root level

---

## Option 3: Hybrid Approach

Keep files at root but create symlinks:

```bash
# Create commands directory
mkdir commands

# Symlink files
ln -s ../trade.py commands/trade.py
ln -s ../roster.py commands/roster.py
ln -s ../player.py commands/player.py
ln -s ../standings.py commands/standings.py

# Create __init__.py
touch commands/__init__.py
```

**Note:** Symlinks work locally but may not work on Render.

---

## Recommended Approach

**For immediate Render deployment:**

1. **Quick fix:** Modify health.py to not load command extensions
   - Just get bot online with health checks
   - Add commands back later after restructure

2. **Proper fix:** Restructure files into `commands/` folder
   - Takes 10 minutes
   - Future-proof
   - Matches health.py expectations

---

## Testing After Migration

### Local Test (bot.py)
```bash
python bot.py
# Should work same as before
```

### Production Test (health.py)
```bash
# Set env vars
export DISCORD_TOKEN=...
export BOT_API_KEY=$(openssl rand -hex 32)

# Run
python health.py

# Check health endpoint
curl http://localhost:8000/health
```

Expected output:
```
============================================================
🚀 FBP Trade Bot - Production Mode (Full API)
============================================================
   Port: 8000
   Discord Token: ✅ Set
   API Key: ✅ Set
============================================================

✅ FastAPI server thread started
🤖 Starting Discord bot (main thread)...
   ✅ Loaded: commands.trade
   ✅ Loaded: commands.roster
   ✅ Loaded: commands.player
   ✅ Loaded: commands.standings
✅ Slash commands synced
✅ Bot is online as FBP Trade Bot#1234
```

---

## Summary

**The Issue:** health.py expects organized file structure  
**Quick Fix:** Remove command loading from health.py  
**Proper Fix:** Reorganize files into `commands/` folder  
**Best Approach:** Proper fix (takes 10 min, future-proof)

**After Fix:** Push to GitHub, Render auto-deploys, bot runs 24/7!
