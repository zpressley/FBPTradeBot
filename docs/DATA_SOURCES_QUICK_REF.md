# FBP Data Sources - Quick Reference

## 🎯 When to Use What

### OFFSEASON (Nov - Feb 9)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Bot Keeper Data | Yahoo frozen at end of season |
| Prospects | Bot Data | Farm system management |
| Player Bio | MLB API | Weekly updates for trades/signings |
| Stats | MLB API (cached) | Monthly refresh |
| Yahoo Sync | ❌ OFF | Don't update from Yahoo |

**Pipeline:** `smart_update_all.py` runs minimal updates

---

### PAD (Feb 10)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Bot Keeper Data | Baseline keepers |
| Prospects | Bot Data | LIVE - managers updating farm |
| Player Bio | MLB API | Daily |
| Yahoo Sync | ❌ OFF | |

**Action:** Discord bot processes prospect assignments

---

### PPD (Feb 17)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Bot Keeper Data | No changes yet |
| Prospects | Bot Data | LIVE - draft happening |
| Player Bio | MLB API | Real-time for draft |
| Yahoo Sync | ❌ OFF | |

**Action:** Discord bot runs prospect draft

---

### TRADE WINDOW (Feb 20-27)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Bot Keeper Data | ACTIVE - processing trades |
| Prospects | Bot Data | ACTIVE - prospect trades |
| Draft Picks | Bot Data | Pick trades |
| Yahoo Sync | ❌ OFF | |

**Critical:** All trades update bot data, not Yahoo

---

### KEEPER DEADLINE (Feb 28)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Bot Keeper Data | LIVE - contract processing |
| Prospects | Bot Data | Graduations |
| Yahoo Sync | ❌ OFF | |

**Action:** Bot processes keeper decisions, advances contracts

---

### KEEPER DRAFT (Mar 8)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Draft Pool | MLB API + Yahoo All | Full universe minus keepers |
| Rosters | Bot Keeper Data | Add drafted players |
| Yahoo Sync | ❌ OFF | |

**Action:** Bot records draft picks, updates keeper rosters

---

### 🔄 TRANSITION (Mar 10-16)
**Prepare for Yahoo sync:**
1. Export final `bot_keepers.json`
2. Manually update Yahoo rosters
3. Verify accuracy
4. Enable Yahoo sync

---

### WEEK 1 START (Mar 17)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Yahoo Rosters | ✅ NOW AUTHORITATIVE |
| Prospects | Bot Data | Still bot-managed |
| Player Bio | MLB API | Daily |
| Stats | MLB API | Daily |
| Yahoo Sync | ✅ ON | Daily updates begin |

**Transition:** Yahoo becomes source of truth again

---

### IN-SEASON (Mar 18 - Aug 31)
| Data Type | Source | Notes |
|-----------|--------|-------|
| Rosters | Yahoo Rosters | ✅ AUTHORITATIVE |
| Prospects | Bot Data | Weekly auctions |
| Player Bio | MLB API | Daily |
| Stats | MLB API | Daily |
| Standings | Yahoo | Weekly |
| Yahoo Sync | ✅ ON | Daily at 6am EST |

**Pipeline:** Full daily updates via GitHub Actions

---

### PLAYOFFS (Sep 1-14)
Same as in-season

---

### POST-SEASON (Sep 15+)
**Transition back to offseason:**
1. Run final Yahoo sync
2. Export to `bot_keepers.json`
3. Disable Yahoo sync
4. Update season dates for next year

---

## 🚦 Key Indicators

### Is Yahoo Authoritative Right Now?
```python
from data_source_manager import DataSourceManager
mgr = DataSourceManager()
print(mgr.should_update_yahoo_rosters())
```
- `True` = Week 1 through Playoffs
- `False` = All other times

### Which Roster Source Should I Use?
```python
source = mgr.get_roster_source()
print(source.value)
```
- `yahoo_rosters` = In-season
- `bot_data_keepers` = Offseason/Pre-season

---

## 🔧 Common Commands

### Check Current Status
```bash
python3 data_source_manager.py
```

### Run Smart Pipeline
```bash
python3 data_pipeline/smart_update_all.py
```

### Initialize Bot Keepers (After Season)
```python
from data.bot_keeper_manager import BotKeeperManager
mgr = BotKeeperManager()
mgr.initialize_from_yahoo()
```

### Process Trade (Offseason)
```python
from data.bot_keeper_manager import BotKeeperManager
mgr = BotKeeperManager()
mgr.process_trade({
    "team1": "WIZ",
    "team1_gives": ["Player A"],
    "team2": "B2J",
    "team2_gives": ["Player B", "Player C"]
})
```

### Export Bot Keepers to Combined Players
```python
mgr = BotKeeperManager()
export = mgr.export_for_combined_players()
# This gets merged into combined_players.json
```

---

## 📅 Annual Update Checklist

### Start of Offseason (Sep 15)
- [ ] Run final Yahoo sync
- [ ] Create `bot_keepers.json` from Yahoo
- [ ] Disable Yahoo sync flag
- [ ] Verify all keepers captured

### Update Season Dates (Nov/Dec)
- [ ] Edit `config/season_dates.json`
- [ ] Update all 2026 dates to 2027
- [ ] Test phase detection
- [ ] Update GitHub Actions schedule

### Start of Season (Mar 17)
- [ ] Export `bot_keepers.json`
- [ ] Manually sync to Yahoo
- [ ] Enable Yahoo sync flag
- [ ] Run first daily pipeline
- [ ] Verify data accuracy

---

## 🐛 Troubleshooting

### "Yahoo rosters updating in offseason"
1. Check current phase: `python3 data_source_manager.py`
2. Verify should_update_yahoo_rosters() returns False
3. Check GitHub Actions aren't forcing Yahoo sync

### "Bot keeper data not found"
1. Initialize from Yahoo: `BotKeeperManager().initialize_from_yahoo()`
2. Verify `data/yahoo_players.json` exists
3. Check transaction log for errors

### "Data source conflict"
1. Run validation: `mgr.validate_data_consistency()`
2. Check which phase system thinks it is
3. Verify season_dates.json is current year

### "Keeper count mismatch"
1. Export bot keepers: `mgr.export_for_combined_players()`
2. Compare to Yahoo roster
3. Check transaction log for missed updates
4. Review keeper deadline processing

---

## 💡 Key Principles

1. **One Source of Truth:** Never have two systems claiming authority
2. **Phase-Aware:** System knows what time of year it is
3. **Graceful Transitions:** Clear handoffs between sources
4. **Everything Logged:** All changes tracked
5. **Fail-Safe:** Can always recover from bot keeper data

---

## 🔗 Related Documentation

- Full details: `/docs/DATA_ORCHESTRATION.md`
- Bot keeper manager: `/data/bot_keeper_manager.py`
- Smart pipeline: `/data_pipeline/smart_update_all.py`
- Season config: `/config/season_dates.json`
