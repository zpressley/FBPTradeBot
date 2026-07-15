# Yahoo Season Data Collection (one-off preseason tool)

Fetches updated position eligibility, league rankings, and stat categories
from Yahoo Fantasy Baseball for a new season. Run this once at the start of
each season (originally written for 2026) — not part of the daily pipeline.

_This merges what were previously two near-duplicate docs
(`START_HERE.md` + `YAHOO_2026_GUIDE.md`) into one._

## Scripts

| Script | Purpose | Output |
|---|---|---|
| `test_yahoo_2026.py` | Verify Yahoo token + confirm the new season is active | Console report |
| `fetch_2026_yahoo_data.py` | Main fetcher — league info, rosters, position eligibility, rankings, stat categories | `data/yahoo_2026_complete.json` (full), `data/yahoo_players.json` (simplified, backward-compatible) |
| `analyze_2026_data.py` | Position-distribution analysis, multi-position players, Yahoo-vs-existing-data comparison | `data/2026_yahoo_positions.csv` |
| `quickstart_2026.py` | Runs fetch + analyze in sequence with prerequisite checks | Same as above, plus a summary report |

## Quick start

```bash
# 1. Confirm the new season is live and your token works
python3 test_yahoo_2026.py

# 2. Fetch + analyze in one shot
python3 quickstart_2026.py

# 3. Review position changes
open data/2026_yahoo_positions.csv
```

If your token has expired: `python3 get_token.py` to re-authenticate.

## Data shapes

**`yahoo_2026_complete.json`** (full):
```json
{
  "league_info": {"league_id": "15505", "season": "2026", "current_week": "1", "name": "Fantasy Baseball Pantheon"},
  "stat_categories": {"batting": [...], "pitching": [...]},
  "teams": {
    "WIZ": {"team_id": "1", "players": [
      {"yahoo_id": "12345", "name": "Juan Soto", "primary_position": "OF",
       "eligible_positions": ["OF", "Util"], "mlb_team": "NYM",
       "rankings": {"current_rank": "8", "average_draft_position": "5"}, "stats": {...}}
    ]}
  }
}
```

**`yahoo_players.json`** (simplified, what the rest of the pipeline actually consumes):
```json
{"WIZ": [{"name": "Juan Soto", "position": "OF", "team": "NYM", "yahoo_id": "12345"}]}
```

## Integrating the results

`data_pipeline/update_yahoo_players.py` and `data_pipeline/merge_players.py`
are what actually pull `yahoo_players.json` into `combined_players.json` as
part of the regular pipeline — this tool just refreshes the source file for
a new season. It doesn't wire into `data_pipeline/smart_update_all.py`
automatically; treat it as a manual once-a-season step, not automation.

## Troubleshooting

- **Token expired:** `python3 get_token.py`
- **No rankings data:** normal early in a season — Yahoo hasn't set up league rankings yet.
- **Season still shows the old year:** Yahoo hasn't rolled over the season on their end yet; wait and retry.
