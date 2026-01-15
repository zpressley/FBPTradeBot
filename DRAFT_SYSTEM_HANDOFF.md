# FBP Draft Bot - Context Handoff Document
*Created: December 16, 2024 - Chat reaching context limit*

## 🎯 CURRENT STATE: COMPLETE DRAFT SYSTEM (Phases 1-5 Done)

### What We Built
A complete Discord-based draft system that replaces manual Google Sheets workflow.

**Core Features Working:**
- ✅ Custom draft orders with variable rounds (some rounds 4 teams, some 12)
- ✅ Pick detection in main channel AND DMs
- ✅ Ephemeral confirmation cards (only picker sees)
- ✅ 10-minute timer with 2-minute warnings
- ✅ Autopick from manager boards → universal board fallback
- ✅ Personal 50-player target boards (/add, /remove, /move, /board)
- ✅ Real prospect validation from combined_players.json
- ✅ Protected/unprotected round rules (1-6 protected, 7+ unprotected)
- ✅ Pinned live status message (updates every 30 sec)
- ✅ Draft board thread (one message per round, edits in place)
- ✅ Pause/resume/undo functionality
- ✅ TEST_MODE for solo testing

## 📁 File Structure

```
fbp-trade-bot/
├── bot.py                          # Main bot (load draft + board commands)
├── draft/
│   ├── __init__.py
│   ├── draft_manager.py            # Core draft logic
│   ├── pick_validator.py           # Validation rules
│   ├── board_manager.py            # Personal boards
│   └── prospect_database.py        # Load from combined_players.json
├── commands/
│   ├── draft.py                    # Discord integration (MAIN FILE)
│   └── board.py                    # Board management commands
└── data/
    ├── draft_order_2025.json       # Custom pick order
    ├── draft_state_prospect_2025.json  # Live state (auto-created)
    ├── manager_boards_2025.json    # Personal boards (auto-created)
    └── combined_players.json       # Your existing player database
```

## 🔧 Key Configuration

### TEST_MODE (commands/draft.py line ~79)
```python
self.TEST_MODE = True   # Set False for production
TEST_USER_ID = 664280448788201522  # Zach's Discord ID
```

When `TEST_MODE = True`:
- User 664280448788201522 can pick for any team
- No @mentions (uses **bold** instead)
- No DMs to managers
- Shows "[TEST MODE]" in status

### Draft Order (data/draft_order_2025.json)
```json
{
  "picks": [
    {"round": 1, "pick": 1, "team": "WIZ", "round_type": "protected"},
    {"round": 4, "pick": 36, "team": "LAW", "round_type": "protected", "notes": "Buy-in $10"},
    ...
  ]
}
```

Supports:
- Variable rounds (Round 4 only has 4 teams)
- Buy-in rounds
- Franchise penalties
- Custom snake patterns

## 🎮 Commands

### Admin Commands (/draft)
- `/draft start prospect` - Start draft
- `/draft pause` - Pause (stops timer)
- `/draft continue` - Resume (restarts timer)
- `/draft undo` - Rollback last pick
- `/draft status` - Show current state
- `/draft order` - View draft order

### Manager Commands (/board, /add, /remove, /move)
- `/board` - View your 50-player target list
- `/add [player]` - Add to board (fuzzy matching)
- `/remove [player]` - Remove from board
- `/move [player] [position]` - Reorder (1 = top)
- `/clear` - Clear entire board

## 🔄 Draft Flow

### Pre-Draft
1. Managers build boards: `/add Jackson Chourio`, `/add Kyle Teel`, etc.
2. Admin creates `draft_order_2025.json` with custom order
3. Bot loads `combined_players.json` for validation

### Draft Day
1. Admin: `/draft start prospect` in #prospect-draft channel
2. Bot creates:
   - Pinned status message (live countdown)
   - Draft board thread (updates after each pick)
   - Starts 10-minute timer
3. Managers type picks in channel OR DM bot
4. Bot shows confirmation (✅/❌ buttons)
5. Manager confirms → pick announced publicly
6. Timer resets for next pick

### Autopick (10 min timeout)
1. Checks manager's personal board
2. Picks first available player
3. Falls back to top UC from prospect database
4. Announces with source attribution

## 🐛 Known Issues & Fixes

### Issue 1: Pick Order Bug (FIXED)
**Problem:** Announcement showed wrong team "on the clock"
**Cause:** Using `get_next_pick()` after `make_pick()` already advanced
**Fix:** Use `get_current_pick()` in announce (draft already advanced)

### Issue 2: Phase 4 Code Breaking Draft (FIXED)
**Problem:** Draft advanced twice, skipping teams
**Cause:** File had Phase 4 database hooks (`on_pick_recorded`) that don't exist
**Fix:** Removed all Phase 4 hooks from draft.py

### Issue 3: Mobile Formatting (FIXED)
**Problem:** Separator line wrapped on mobile
**Fix:** Shortened from 40 to 35 characters

### Issue 4: Cancel Message (FIXED)
**Problem:** Just said "Cancelled"
**Fix:** Now shows "WIZ cancelled pick for Jackson Chourio"

## 💾 Data Persistence

### Draft State (auto-saves after each pick)
`data/draft_state_prospect_2025.json`:
- Current pick index
- All picks made
- Timer state
- Pause state
- Survives bot restarts!

### Manager Boards (auto-saves)
`data/manager_boards_2025.json`:
- Each team's 50-player list
- Persists between sessions
- Used for autopick

## 📊 Prospect Database

Loads from `data/combined_players.json`:
- Filters by `player_type`:
  - "Farm" for prospect drafts
  - "MLB" for keeper drafts
- Parses ownership from `years_simple`: PC/FC/DC/UC
- Fuzzy name matching (0.6 cutoff)
- Position filtering

## 🔐 Protected/Unprotected Rules

**Protected Rounds (1-6):**
- Can pick: UC (uncontracted) OR own PC/FC
- Cannot pick: Other team's PC/FC

**Unprotected Rounds (7+):**
- Can pick: Anyone (poaching allowed)
- Shows warning: "🏴‍☠️ POACH from HAM"

Enforced by `PickValidator.validate_pick()`

## 🎨 Discord UI Elements

### Pick Announcements (Main Channel)
```
**Round 1, Pick 5**
**RV** selects **Jackson Chourio**
CF • [MIL] • Rank #3
───────────────────────────────────

**⏰ ON THE CLOCK**
# HAM
Pick 6
```

### Status Message (Pinned, Updates Every 30s)
```
🟢 2025 PROSPECT DRAFT [TEST MODE]

Current Round: Round 1 (Protected)
Progress: 5/73 picks
⏱️ Time: 8:23

⏰ ON THE CLOCK
# HAM
Pick 6

Up Next:
On Deck: JEP (Pick 7)
In Hole: TBB (Pick 8)

Recent Picks:
• RV - Jackson Chourio
• HAM - Kyle Teel  
• CFL - James Wood
```

### Draft Board Thread (One Message Per Round)
```
═══════════════════════════════════
**ROUND 1** (PROTECTED) ⏳ IN PROGRESS (5/12)
──────────────────────────────────
  1. WIZ - Jackson Chourio
  2. B2J - Kyle Teel
  3. CFL - James Wood
  4. HAM - Paul Skenes
  5. RV - Dylan Crews
  6. SAD - ⏰ **ON THE CLOCK**
  7. JEP
  8. TBB
  ...
═══════════════════════════════════
```

## 🚀 Next Steps / Future Enhancements

### Immediate Need (Current Request)
**Bulk add to board:** Allow comma-separated list
- Example: `/addmany Jackson Chourio, Kyle Teel, James Wood`
- Parse CSV, add all at once

### Optional Future Features
1. **Prospect Database Channel** (legacy) - previously a searchable Discord
   channel with position threads; now replaced by website prospect tools.
2. **Keeper Draft Support** - Same system, different player pool
3. **Post-Draft Export** - Auto-update Google Sheets
4. **Trade Integration** - Pick trading during draft
5. **Mobile Web View** - Alternative to Discord for viewing

## 🔑 Key Technical Details

### Timer System
- `asyncio.create_task()` for background countdown
- Cancels on pick confirmation
- Pauses when draft paused
- Updates status every 30 seconds

### State Management
- Draft state persists in JSON after each pick
- Supports `/draft undo` by rolling back state
- Survives bot crashes/restarts mid-draft

### Message Editing
- Status message: `await message.edit(embed=new_embed)`
- Board thread: Stores `message_id` per round, edits same message
- Ephemeral confirmations: `delete_after=600` (10 min)

### Fuzzy Matching
- Uses `difflib.get_close_matches()`
- Cutoff 0.6-0.7 depending on use case
- Helps with typos: "chourio" → "Jackson Chourio"

## 🐛 Debugging Tips

**If picks skip teams:**
- Check `make_pick()` isn't called twice
- Verify `announce_pick` uses `get_current_pick()` not `get_next_pick()`

**If commands don't show:**
- Run `!sync` in Discord
- Wait 1-2 minutes for cache
- Check bot loaded extension: Look for "✅ Draft commands loaded" in console

**If timer doesn't work:**
- Ensure `asyncio` task isn't cancelled prematurely
- Check `PICK_TIMER_DURATION` = 600 seconds

**If autopick fails:**
- Check `board_manager` initialized in `/draft start`
- Verify `combined_players.json` exists
- Check prospect_db loaded: Look for "✅ Loaded X players"

## 📱 Mobile Considerations

- Separator lines: 35 chars max
- Use `#` for large text (Discord markdown)
- Embeds better than plain text
- Buttons work great on mobile
- Thread swipe-to-open works well

## 🔄 Deployment

**Local Testing:**
```bash
python3 bot.py
```

**Production (Render):**
```bash
./commit.sh
# Type message, push to GitHub
# Render auto-deploys
```

---

**Last Updated:** December 16, 2024
**Status:** Phases 1-5 Complete, Ready for Production Testing
**Next:** Add bulk board import, then prospect database channel
