# FBP Data Orchestration System

## Overview

The FBP data system intelligently manages multiple data sources that are authoritative at different times during the season. This document explains how the orchestration works and when each source is used.

## Core Components

### 1. Data Source Manager (`data_source_manager.py`)
The central orchestrator that:
- Tracks current season phase
- Determines which data sources are authoritative
- Provides source priority for each data field
- Validates data consistency

### 2. Smart Data Pipeline (`data_pipeline/smart_update_all.py`)
Automated update system that:
- Only updates necessary sources for current phase
- Skips irrelevant updates
- Optimizes API usage
- Runs via GitHub Actions daily

### 3. Bot Keeper Manager (`data/bot_keeper_manager.py`)
Offseason roster tracking that:
- Manages keeper rosters when Yahoo is frozen
- Tracks trades, contract changes
- Processes keeper deadline decisions
- Exports to combined_players.json

## Season Phase Calendar

### Offseason (Nov - Feb 9)
**What's happening:**
- Post-playoffs recovery
- Minimal activity
- Planning for next season

**Data sources:**
- ✅ MLB API: Bio updates (trades, signings)
- ✅ UPID List: Prospect database
- ✅ Bot Keeper Data: Last year's keepers
- ❌ Yahoo Rosters: FROZEN (last in-season state)
- ⚠️ Yahoo All Players: Read-only reference

**Update frequency:** Weekly

---

### PAD - Prospect Assignment Day (Feb 10)
**What's happening:**
- Managers update farm systems
- Convert PCs to FCs/DCs
- Purchase new contracts

**Data sources:**
- ✅ Bot Prospect Data: LIVE updates
- ✅ MLB API: Current prospect statuses
- ✅ Bot Keeper Data: Keeper baseline
- ❌ Yahoo Rosters: Still frozen

**Update frequency:** On-demand (via Discord bot)

---

### PPD - Prospect Draft (Feb 17)
**What's happening:**
- Prospect draft conducted
- New prospects added to farm systems

**Data sources:**
- ✅ Bot Prospect Data: Draft results
- ✅ Bot Keeper Data: No changes yet
- ✅ MLB API: Verify prospect eligibility
- ❌ Yahoo Rosters: Still frozen

**Update frequency:** Real-time during draft

---

### Franchise Tag Deadline (Feb 19)
**What's happening:**
- Managers apply franchise tags
- Contract decisions made

**Data sources:**
- ✅ Bot Keeper Data: LIVE contract updates
- ✅ Bot Prospect Data: No changes
- ❌ Yahoo Rosters: Still frozen

---

### Trade Window (Feb 20-27)
**What's happening:**
- ACTIVE TRADING PERIOD
- Keepers, prospects, picks traded
- Rosters change significantly

**Data sources:**
- ✅ Bot Keeper Data: LIVE - processes all trades
- ✅ Bot Prospect Data: LIVE - prospect trades
- ✅ Bot Draft Picks: Pick tracking
- ❌ Yahoo Rosters: Still frozen

**Critical:** Bot data is THE source of truth. All trades update bot rosters.

---

### Keeper Deadline (Feb 28)
**What's happening:**
- Final keeper decisions
- Contract advancements
- IL tags applied
- Unkept players to draft pool

**Data sources:**
- ✅ Bot Keeper Data: LIVE - contract processing
- ✅ Bot Prospect Data: Graduations processed
- ❌ Yahoo Rosters: Still frozen

**Output:** Final keeper list for each team

---

### Keeper Draft (Mar 8)
**What's happening:**
- Draft of unkept players
- New keepers assigned contracts

**Data sources:**
- ✅ MLB API: Full player universe
- ✅ Yahoo All Players: Verify eligibility
- ✅ Bot Draft Data: Draft results
- ✅ Bot Keeper Data: Add drafted players
- ❌ Yahoo Rosters: Still frozen

**Post-draft:** Bot keepers now include all keeper + draft picks

---

### Week 1 Start (Mar 17)
**CRITICAL TRANSITION POINT**

**What happens:**
- Yahoo rosters updated with final offseason rosters
- Bot keeper data SYNCS to Yahoo
- Yahoo becomes authoritative source again

**Data sources:**
- ✅ Yahoo Rosters: NOW LIVE AND AUTHORITATIVE
- ✅ MLB API: Current season stats start
- ✅ Bot Prospect Data: Still manages farm
- ⚠️ Bot Keeper Data: Read-only backup

**Initial sync process:**
1. Load final bot keeper data
2. Update Yahoo rosters manually (commissioners)
3. Verify sync with bot data
4. Switch authoritative source to Yahoo

---

### In-Season (Mar 18 - Aug 31)
**What's happening:**
- Daily roster moves in Yahoo
- Stats accumulation
- Weekly matchups
- Prospect auctions

**Data sources:**
- ✅ Yahoo Rosters: AUTHORITATIVE (synced daily)
- ✅ MLB API: Live stats, bio updates
- ✅ Bot Prospect Data: Auctions, graduations
- ⚠️ Bot Keeper Data: Backup only

**Update frequency:** Daily

**Special events:**
- **Monday 3pm:** Prospect auction opens
- **Sunday:** Auction results processed, update bot prospects

---

### Playoffs (Sep 1-14)
Same as in-season

---

### Post-Season (Sep 15+)
**What happens:**
- Season ends
- Yahoo rosters frozen at final state
- Begin offseason transition

**Data sources:**
- ✅ Yahoo Rosters: Frozen final state
- ✅ Bot Keeper Data: Initialize from Yahoo
- ✅ MLB API: Offseason updates

**Transition:**
1. Save final Yahoo rosters
2. Initialize bot keeper data from Yahoo
3. Bot becomes authoritative for offseason

---

## Data Flow Diagrams

### In-Season Data Flow
```
MLB API → combined_players.json ← Yahoo Rosters (authoritative)
                ↓
        [Daily sync 6am EST]
                ↓
        GitHub Actions Update
                ↓
        Website displays live data
```

### Offseason Data Flow
```
MLB API → combined_players.json ← Bot Keeper Data (authoritative)
                ↓
        [Discord bot updates]
                ↓
        Manual commit/push
                ↓
        Website shows offseason rosters
```

### Transition Points

**End of Season → Offseason:**
```
1. Final Yahoo sync
2. Export to bot_keepers.json
3. Freeze Yahoo updates
4. Enable bot keeper management
```

**Offseason → Week 1:**
```
1. Export final bot_keepers.json
2. Manually sync to Yahoo rosters
3. Verify accuracy
4. Enable Yahoo roster updates
5. Switch authoritative source
```

---

## combined_players.json Source Map

### During In-Season
```json
{
  "player_id": "MLB API",
  "name": "MLB API",
  "position": "MLB API",
  "mlb_team": "MLB API",
  "manager": "Yahoo Rosters",  ← AUTHORITATIVE
  "player_type": "Bot Prospects",
  "contract_type": "Yahoo Rosters",
  "yahoo_id": "Yahoo All Players",
  "current_stats": "MLB API"
}
```

### During Offseason
```json
{
  "player_id": "MLB API",
  "name": "MLB API",
  "position": "MLB API",
  "mlb_team": "MLB API",
  "manager": "Bot Keeper Data",  ← AUTHORITATIVE
  "player_type": "Bot Prospects",
  "contract_type": "Bot Keeper Data",
  "salary": "Bot Keeper Data",
  "yahoo_id": "Yahoo All Players",
  "current_stats": "MLB API (cached)"
}
```

---

## Usage

### Run Smart Pipeline
```bash
# Automatically detects phase and runs appropriate updates
python3 data_pipeline/smart_update_all.py
```

### Check Current Phase
```bash
python3 data_source_manager.py
```

### Process a Trade (Offseason)
```python
from data.bot_keeper_manager import BotKeeperManager

manager = BotKeeperManager()
manager.process_trade({
    "team1": "WIZ",
    "team1_gives": ["Shohei Ohtani"],
    "team2": "B2J",
    "team2_gives": ["Mike Trout", "Dylan Cease"],
    "wizbucks": {"WIZ": 10, "B2J": 0}
})
```

### Apply Keeper Decisions
```python
manager.apply_keeper_deadline_decisions(
    manager="WIZ",
    kept_players=["Shohei Ohtani", "Juan Soto", ...],
    il_tags={"Shohei Ohtani": True}
)
```

---

## GitHub Actions Integration

### Daily Update (6am EST)
```yaml
name: Daily Data Update

on:
  schedule:
    - cron: "0 11 * * *"  # 6am EST = 11am UTC

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Smart Pipeline
        run: python3 data_pipeline/smart_update_all.py
      - name: Commit changes
        run: |
          git config user.name "FBP Bot"
          git add data/
          git commit -m "Daily data update"
          git push
```

The smart pipeline automatically:
- Detects current phase
- Only updates necessary sources
- Skips Yahoo sync in offseason
- Runs appropriate scripts

---

## Validation & Debugging

### Check Data Consistency
```python
from data_source_manager import DataSourceManager

mgr = DataSourceManager()
validation = mgr.validate_data_consistency()

for check, passed in validation.items():
    print(f"{'✅' if passed else '❌'} {check}")
```

### Common Issues

**Yahoo rosters updating in offseason:**
- Check `data/.yahoo_sync_enabled` flag
- Verify season phase detection
- Review GitHub Actions logs

**Bot keeper data not updating:**
- Check Discord bot connectivity
- Verify bot_keepers.json exists
- Review transaction log

**Data merge conflicts:**
- Check source priority in DataSourceManager
- Verify both sources have same player IDs
- Review merge_players.py logic

---

## Migration Checklist

### End of Season (Sep 15)
- [ ] Run final Yahoo sync
- [ ] Export to bot_keepers.json
- [ ] Create `.yahoo_frozen` flag file
- [ ] Verify all keepers captured
- [ ] Update season_dates.json for next year

### Start of Season (Mar 17)
- [ ] Export final bot_keepers.json
- [ ] Manually sync to Yahoo rosters
- [ ] Verify keeper counts match
- [ ] Remove `.yahoo_frozen` flag
- [ ] Enable Yahoo sync in pipeline
- [ ] Test daily updates

---

## Architecture Benefits

1. **Single Source of Truth:** Always know which data is authoritative
2. **API Efficiency:** Only update what's needed
3. **Offseason Flexibility:** Bot manages complex keeper rules
4. **Zero Data Loss:** All changes logged
5. **Smooth Transitions:** Clear migration points
6. **Automated Updates:** Set and forget after setup

---

## Future Enhancements

1. **Automatic Transition Detection:** Auto-switch sources at key dates
2. **Conflict Resolution:** Detect and resolve data discrepancies
3. **Rollback System:** Undo bad updates
4. **Historical Archive:** Full season snapshots
5. **Web Interface:** View/edit bot keeper data in browser
