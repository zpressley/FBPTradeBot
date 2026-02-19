# KAP Submission System

## Overview
The KAP (Keeper Assignment Period) submission system handles keeper selections, contract updates, draft pick taxation, and WizBucks transactions.

## API Endpoint

### POST /api/kap/submit

**Request Body:**
```json
{
  "team": "WIZ",
  "season": 2026,
  "keepers": [
    {
      "upid": "1234",
      "name": "Player Name",
      "contract": "VC-1",
      "has_il_tag": false,
      "has_rat": false
    }
  ],
  "il_tags": {
    "TC": "5678",
    "VC": null,
    "FC": null
  },
  "rat_applications": ["9012"],
  "buyins_purchased": [1, 2],
  "taxable_spend": 250,
  "submitted_by": "username"
}
```

**Response:**
```json
{
  "ok": true,
  "team": "WIZ",
  "season": 2026,
  "timestamp": "2026-03-01T12:00:00",
  "keepers_selected": 15,
  "keeper_salary_cost": 150,
  "rat_cost": 75,
  "buyin_cost": 90,
  "total_taxable_spend": 240,
  "wb_spent": 315,
  "wb_remaining": 60,
  "draft_picks_taxed": [6, 7, 8]
}
```

## Processing Steps

### 1. Contract Updates (combined_players.json)
For each keeper:
- Advance contract tier (e.g., VC-1 → VC-2)
- Set `contract_year` to submission season
- Apply IL tag discount if applicable
- Handle RaT tier reduction if applicable

### 2. Player Log Entries (player_log.json)
Each keeper gets a log entry:
```json
{
  "timestamp": "2026-03-01T12:00:00",
  "team": "WIZ",
  "player": {
    "upid": "1234",
    "name": "Player Name",
    "mlb_team": "NYY"
  },
  "action": "keeper_selection",
  "details": {
    "season": 2026,
    "old_contract": "VC-1",
    "new_contract": "VC-2",
    "salary": 35,
    "has_il_tag": false,
    "has_rat": false
  }
}
```

### 3. Draft Pick Taxation (draft_order_2026.json)
Based on taxable spend, mark picks as `taxed_out: true`:
- $421-$435: Lose Rounds 4-8
- $401-$420: Lose Rounds 5-7
- $376-$400: Lose Rounds 6-8
- $351-$375: Lose Rounds 7-9
- $326-$350: Lose Rounds 8-10
- ≤$325: No tax

### 4. WizBucks Deduction (wizbucks.json)
```json
{
  "Whiz Kids": 60
}
```
Balance updated: `old_balance - total_spend`

### 5. Transaction Log (wizbucks_transactions.json)
```json
{
  "id": "kap_WIZ_2026-03-01T12:00:00",
  "timestamp": "2026-03-01T12:00:00",
  "team": "WIZ",
  "team_name": "Whiz Kids",
  "amount": -315,
  "balance_before": 375,
  "balance_after": 60,
  "transaction_type": "KAP_submission",
  "description": "2026 KAP: 15 keepers selected",
  "metadata": {
    "season": 2026,
    "keeper_count": 15,
    "keeper_salary": 150,
    "rat_cost": 75,
    "buyin_cost": 90,
    "taxable_spend": 240,
    "taxed_rounds": [6, 7, 8],
    "submitted_by": "username"
  }
}
```

### 6. Submission Metadata (kap_submissions.json)
```json
{
  "WIZ": {
    "season": 2026,
    "team": "WIZ",
    "timestamp": "2026-03-01T12:00:00",
    "keeper_count": 15,
    "keepers": [...],
    "taxable_spend": 240,
    "tax_bracket": {
      "min": 0,
      "max": 325,
      "rounds": []
    },
    "submitted_by": "username"
  }
}
```

### 7. Git Sync to GitHub
After saving all files, automatically commits and pushes to GitHub:

**Commit Message:**
```
KAP 2026: WIZ submitted 15 keepers
```

**Files Committed:**
- `data/combined_players.json`
- `data/player_log.json`
- `data/draft_order_2026.json`
- `data/wizbucks.json`
- `data/wizbucks_transactions.json`
- `data/kap_submissions.json`

**Environment Variables Required:**
- `GITHUB_TOKEN` - GitHub personal access token
- `GITHUB_REPO` - Repository (default: `zpressley/FBPTradeBot`)
- `GITHUB_USER` - Username (default: `x-access-token`)
- `REPO_ROOT` - Repository root path (default: current directory)

### 8. Discord Notification
Posts to transactions channel (1089979265619083444):

**Embed:**
- Title: `{TEAM} – KAP Submission (2026)`
- Color: Gold
- Fields:
  - Keepers Selected
  - Keeper Salaries
  - Reduce-a-Tier (if used)
  - Buy-Ins (if purchased)
  - Taxable Spend
  - Draft Pick Tax
- Footer: Total WB Spent, Remaining, Timestamp

## Costs

### Keeper Salaries
```
TC-R:    $5
TC-BC-1: $5
TC-BC-2: $5
TC-1:    $15
TC-2:    $25
VC-1:    $35
VC-2:    $55
FC-1:    $85
FC-2:    $125
FC-2+:   $125
```

### IL Tag Discounts
- TC Tier: -$10
- VC Tier: -$15
- FC Tier: -$35

### Salary Tools
- Reduce-a-Tier (RaT): $75 (tax-free, can use multiple)
- IL Tags: FREE (1 per tier)

### Buy-Ins
- Round 1: $55 (taxable)
- Round 2: $35 (taxable)
- Round 3: $10 (taxable)

**Note:** Buy-ins are purchased separately via `/api/buyin/purchase` endpoint before KAP submission.

## Contract Advancement Rules

**Normal Progression:**
```
TC-R → TC-1 → TC-2 → VC-1 → VC-2 → FC-1 → FC-2+ (terminal)
```

**Blue Chip Exception:**
```
TC-BC-1 → TC-BC-2 → TC-1 (then follows normal progression)
```

**All Contracts:**
- TC-R → TC-1
- TC-BC-1 → TC-BC-2 (Blue Chip year 1)
- TC-BC-2 → TC-1 (Blue Chip year 2, enters normal progression)
- TC-1 → TC-2
- TC-2 → VC-1
- VC-1 → VC-2
- VC-2 → FC-1
- FC-1 → FC-2+
- FC-2+ → FC-2+ (stays at terminal tier)

## Error Handling

**Validation Errors:**
- Insufficient WizBucks
- Invalid contract tier
- Keeper count exceeds 26
- Taxable spend exceeds $435

**System Errors:**
- File I/O failures
- Discord notification failures (non-blocking)

## Testing

Test mode available via `test_mode=True` parameter:
- Writes to `kap_submissions_test.json`
- Does NOT update production data files
- Useful for validation and debugging
