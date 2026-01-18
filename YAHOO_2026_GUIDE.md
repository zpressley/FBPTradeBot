# Yahoo 2026 Data Collection Guide

## Overview
These tools fetch updated 2026 season data from Yahoo Fantasy Baseball, including:
- ✅ Updated player positions (eligible positions for 2026)
- ✅ League-specific rankings (ADP, current rank, preseason rank)
- ✅ Player statistics (current season stats)
- ✅ Team rosters and roster construction
- ✅ Stat category mappings

## Files Created

### 1. `fetch_2026_yahoo_data.py`
**Main data collection script** - Fetches all 2026 data from Yahoo API

**Features:**
- Fetches league information (season, current week, etc.)
- Gets complete rosters with detailed player data
- Extracts position eligibility for 2026
- Pulls league-specific rankings (if available)
- Retrieves stat categories used by your league
- Exports to two formats: complete and simplified

**Outputs:**
- `data/yahoo_2026_complete.json` - Full dataset with all details
- `data/yahoo_players.json` - Simplified format (backward compatible)

### 2. `analyze_2026_data.py`
**Data analysis and comparison tool**

**Features:**
- Position distribution analysis
- Multi-position eligible player identification
- League rankings analysis (top players)
- Yahoo vs Google Sheets position comparison
- CSV export for easy review

**Outputs:**
- `data/2026_yahoo_positions.csv` - Spreadsheet-friendly format

## Usage Instructions

### Step 1: Fetch 2026 Data
```bash
# Make sure your Yahoo token is valid
python3 get_token.py  # If token expired

# Fetch 2026 data
python3 fetch_2026_yahoo_data.py
```

**Expected Output:**
```
🚀 FBP Yahoo Data Collector for 2026
==================================================
📊 Fetching league information...
✅ League: Fantasy Baseball Pantheon
✅ Season: 2026
✅ Current Week: 1

📋 Fetching detailed rosters...
  📝 Processing WIZ...
    ✅ 26 players loaded
  📝 Processing B2J...
    ✅ 26 players loaded
  ...

📊 Fetching stat categories...
✅ Batting categories: 6
✅ Pitching categories: 6

✅ Complete data saved: data/yahoo_2026_complete.json
✅ Simple rosters saved: data/yahoo_players.json

📊 Data Summary:
  League: Fantasy Baseball Pantheon
  Season: 2026
  Teams: 12
  Total Players: 312
  Batting Stats: 6
  Pitching Stats: 6

🎉 2026 Yahoo data successfully collected!
```

### Step 2: Analyze the Data
```bash
python3 analyze_2026_data.py
```

**What You'll Get:**
- Position changes for 2026
- Multi-position eligible players
- League-specific rankings
- Discrepancies between Yahoo and your Google Sheets
- CSV export for review

### Step 3: Review Key Changes

#### Position Changes
Look for players who gained/lost position eligibility:
```
🔀 Multi-Position Eligible Players: 45

Top 10 Multi-Position Players:
  1. Shohei Ohtani (WIZ): DH, P, Util
  2. Kyle Tucker (B2J): OF, Util
  3. Mookie Betts (CFL): 2B, SS, OF, Util
  ...
```

#### Rankings
Check if Yahoo has league-specific rankings set up:
```
📈 League Rankings Analysis
Players with Rankings: 150

Top 10 Ranked Players:
Rank   Player                    Team  ADP    Preseason
1      Bobby Witt Jr.            KC    3      2
2      Juan Soto                 NYM   5      3
3      Aaron Judge               NYY   2      1
...
```

## Data Structure

### Complete Dataset (`yahoo_2026_complete.json`)
```json
{
  "league_info": {
    "league_id": "15505",
    "season": "2026",
    "current_week": "1",
    "name": "Fantasy Baseball Pantheon"
  },
  "stat_categories": {
    "batting": [...],
    "pitching": [...]
  },
  "teams": {
    "WIZ": {
      "team_id": "1",
      "players": [
        {
          "yahoo_id": "12345",
          "name": "Juan Soto",
          "primary_position": "OF",
          "eligible_positions": ["OF", "Util"],
          "mlb_team": "NYM",
          "rankings": {
            "current_rank": "8",
            "average_draft_position": "5"
          },
          "stats": {...}
        }
      ]
    }
  }
}
```

### Simplified Dataset (`yahoo_players.json`)
```json
{
  "WIZ": [
    {
      "name": "Juan Soto",
      "position": "OF",
      "team": "NYM",
      "yahoo_id": "12345"
    }
  ]
}
```

## Integration with Existing Systems

### Update Combined Players
After fetching 2026 data, you can merge it with your existing player database:

```python
# In data_pipeline/merge_players.py
# Add position updates from Yahoo 2026 data

import json

# Load Yahoo 2026 data
with open("data/yahoo_2026_complete.json", "r") as f:
    yahoo_data = json.load(f)

# Update positions in combined_players.json
# Yahoo positions take precedence for active roster players
```

### Position Updates for Google Sheets
Export the CSV and review changes:
```bash
# After running analyze_2026_data.py
open data/2026_yahoo_positions.csv

# Review and update your Google Sheet manually if needed
```

## Troubleshooting

### Token Expired
```bash
# Re-authenticate with Yahoo
python3 get_token.py
```

### No Rankings Data
If rankings show as empty, Yahoo may not have league rankings set up yet. This is normal early in the season.

### Position Discrepancies
Compare Yahoo positions to your Google Sheet. Yahoo positions for rostered players should be considered authoritative for 2026.

## Key Benefits

1. **Accurate 2026 Positions**: Get Yahoo's official position eligibility
2. **League Rankings**: If your league has custom rankings
3. **Easy Comparison**: CSV export for reviewing changes
4. **Backward Compatible**: Updates existing `yahoo_players.json` format
5. **Complete Data**: Preserves all Yahoo metadata for future use

## Next Steps

1. ✅ Run `fetch_2026_yahoo_data.py` to get 2026 data
2. ✅ Review `analyze_2026_data.py` output for changes
3. ✅ Check `data/2026_yahoo_positions.csv` for position updates
4. ✅ Update your Google Sheet if needed
5. ✅ Integrate into daily pipeline: `data_pipeline/update_yahoo_players.py`

## Questions?

Check the output files for comprehensive data structure examples.
