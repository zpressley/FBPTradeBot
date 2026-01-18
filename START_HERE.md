# 🎯 Yahoo 2026 Data Collection - Complete Setup

## Files Created for You

### 1. **fetch_2026_yahoo_data.py** ⭐ MAIN FETCHER
**What it does:**
- Fetches all 2026 season data from Yahoo Fantasy API
- Gets updated player positions and eligibility
- Retrieves league-specific rankings (if available)
- Exports to two formats: complete and simplified

**Outputs:**
- `data/yahoo_2026_complete.json` - Full dataset with all metadata
- `data/yahoo_players.json` - Simplified roster format (backward compatible)

---

### 2. **analyze_2026_data.py** 📊 ANALYSIS TOOL
**What it does:**
- Analyzes position distribution across league
- Identifies multi-position eligible players
- Shows league rankings (if available)
- Compares Yahoo positions to your Google Sheet
- Exports to CSV for easy review

**Outputs:**
- `data/2026_yahoo_positions.csv` - Spreadsheet-friendly export
- Console reports with position changes and rankings

---

### 3. **quickstart_2026.py** 🚀 ONE-COMMAND RUNNER
**What it does:**
- Runs both fetcher and analyzer in sequence
- Checks prerequisites (token, credentials)
- Provides clear status updates
- Generates summary report

**Usage:**
```bash
python3 quickstart_2026.py
```

This is the easiest way to get everything!

---

### 4. **test_yahoo_2026.py** 🧪 CONNECTION TEST
**What it does:**
- Tests Yahoo API connectivity
- Verifies token is valid
- Confirms 2026 season is active
- Checks roster data access

**Usage:**
```bash
python3 test_yahoo_2026.py
```

Run this first to verify everything works!

---

### 5. **YAHOO_2026_GUIDE.md** 📖 DOCUMENTATION
Complete guide covering:
- How to use each script
- Data structure explanations
- Integration with existing systems
- Troubleshooting common issues
- Next steps and best practices

---

### 6. **WHAT_YOULL_GET.md** 📊 DATA PREVIEW
Visual guide showing:
- Exact data you'll collect
- Example outputs with real data
- Common questions answered
- Success indicators
- Troubleshooting tips

---

## Quick Start (3 Steps)

### Step 1: Test Connection
```bash
python3 test_yahoo_2026.py
```

**Expected Output:**
```
🧪 Yahoo API Connection Test
==================================================
✅ Access token retrieved
✅ API connection successful!
📊 League Information:
   League: Fantasy Baseball Pantheon
   Season: 2026
   Current Week: 1
🎉 2026 season confirmed!
```

### Step 2: Fetch & Analyze Data
```bash
python3 quickstart_2026.py
```

**Expected Output:**
```
🎯 FBP 2026 Yahoo Data - Quick Start
==================================================
✅ Prerequisites OK

🚀 Step 1: Fetching 2026 Yahoo Data
✅ Complete data saved: data/yahoo_2026_complete.json
✅ Simple rosters saved: data/yahoo_players.json

🚀 Step 2: Analyzing 2026 Data
📍 Position Distribution: ...
🔀 Multi-Position Players: 45
📈 League Rankings: ...

✅ Quick Start Complete!
```

### Step 3: Review Position Data
```bash
open data/2026_yahoo_positions.csv
```

Look for:
- Players who gained new position eligibility
- Multi-position players for roster flexibility
- Position changes from your Google Sheet
- League-specific rankings (if available)

---

## What You'll Get (Key Data)

### Updated Positions for 2026 ⭐
```json
{
  "name": "Mookie Betts",
  "eligible_positions": ["2B", "SS", "OF", "Util"]
}
```

### League Rankings (If Available)
```json
{
  "rankings": {
    "current_rank": "8",
    "average_draft_position": "5",
    "preseason_rank": "3"
  }
}
```

### Complete Player Data
```json
{
  "yahoo_id": "12345",
  "name": "Juan Soto",
  "mlb_team": "NYM",
  "primary_position": "OF",
  "eligible_positions": ["OF", "Util"],
  "status": "Healthy",
  "rankings": {...},
  "stats": {...}
}
```

---

## Troubleshooting

### Issue: "Token expired"
**Solution:**
```bash
python3 get_token.py
```

### Issue: "No rankings data"
**Cause:** Normal early season - Yahoo hasn't set up league rankings yet
**Solution:** You'll still get positions and basic data

### Issue: "Network error"
**Cause:** No internet or Yahoo API is down
**Solution:** 
1. Check internet connection
2. Try again in a few minutes
3. Verify you can access: https://baseball.fantasysports.yahoo.com/b1/8560

### Issue: "Season is 2025, not 2026"
**Cause:** Yahoo hasn't switched to 2026 yet
**Solution:** Wait for Yahoo to roll over the season (usually happens automatically)

---

## Integration with Your System

### Current System Updates
Your existing `update_yahoo_players.py` still works! The new script creates the same `yahoo_players.json` format, so it's backward compatible.

### Daily Automation (Optional)
To add to your daily pipeline:

```python
# In your pipeline script
import subprocess

# Fetch latest Yahoo data
subprocess.run(["python3", "fetch_2026_yahoo_data.py"])

# Continue with existing merge logic
# ... rest of your pipeline
```

### Merge with Google Sheets
After fetching Yahoo data, you can:
1. Review the CSV export
2. Manually update positions in your Google Sheet
3. (Future) Auto-sync positions to Google Sheet

---

## File Structure After Running

```
fbp-trade-bot/
├── data/
│   ├── yahoo_2026_complete.json     ← Full 2026 dataset
│   ├── yahoo_players.json           ← Simplified (existing format)
│   ├── 2026_yahoo_positions.csv     ← CSV export for review
│   └── combined_players.json        ← Your existing player DB
│
├── fetch_2026_yahoo_data.py         ← Main data fetcher
├── analyze_2026_data.py             ← Analysis & comparison
├── quickstart_2026.py               ← One-command runner
├── test_yahoo_2026.py               ← Connection test
│
├── YAHOO_2026_GUIDE.md              ← Complete documentation
└── WHAT_YOULL_GET.md                ← Data preview & examples
```

---

## Next Steps

1. ✅ **Test connection:** `python3 test_yahoo_2026.py`
2. ✅ **Fetch data:** `python3 quickstart_2026.py`
3. ✅ **Review positions:** Open `data/2026_yahoo_positions.csv`
4. ✅ **Compare to sheet:** Check for position changes
5. ✅ **Update if needed:** Manually update your Google Sheet

### Optional Enhancements
- Add to daily automation pipeline
- Create position change alerts
- Auto-sync to Google Sheets
- Track eligibility gains during season

---

## Summary

You now have a complete system to:
- ✅ Fetch 2026 Yahoo data (positions, rankings, stats)
- ✅ Analyze position changes
- ✅ Compare to your existing data
- ✅ Export to CSV for easy review
- ✅ Integrate with your current pipeline

**Start with:** `python3 test_yahoo_2026.py` to verify everything works!

---

## Questions?

Check these docs:
- **YAHOO_2026_GUIDE.md** - Comprehensive guide
- **WHAT_YOULL_GET.md** - Data structure examples

Or review the scripts themselves - they're well-commented!

---

**🎉 Ready to collect your 2026 data!**
