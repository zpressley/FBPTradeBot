# TRADE TOOL - Complete Website Integration with Discord Workflow

## 🎯 **What You're Building**

A **website-based trade submission form** that connects to your existing Discord trade approval workflow. Managers can build trades on the website, which then creates the same private thread approval process you already have working in Discord.

---

## ✅ **What Already Works (Discord)**

You have a **complete trade workflow** in Discord:

```
/trade command
  ↓
Player validation (fuzzy matching)
  ↓
Preview shown to submitter
  ↓
Submitter confirms
  ↓
Private thread created in #pending-trades
  ↓
All involved managers must approve (buttons)
  ↓
Admin review (separate channel)
  ↓
Admin approves → Posted to #trades channel
```

### **Existing Discord Files:**
- `commands/trade.py` - `/trade` slash command
- `commands/trade_logic.py` - Thread creation, approval views, modals
- `commands/utils.py` - Manager IDs, trade dates

**Channel IDs:**
- Pending Trades: `1356234086833848492`
- Admin Review: `875594022033436683`
- Final Trades: `1197200421639438537`

---

## 🔗 **Your Integration Task**

Build a **website form** that triggers the **same Discord workflow** via API:

```
Website form
  ↓
Cloudflare Worker (proxy)
  ↓
Bot API endpoint
  ↓
Calls existing create_trade_thread() function
  ↓
Rest of Discord workflow (same as /trade command)
```

---

## 📊 **Your ACTUAL Data**

### **Players from `FBPHub.data.players`:**

```json
{
  "name": "Jordan Lawlar",
  "team": "AZ",
  "position": "3B",
  "manager": "Rick Vaughn",
  "FBP_Team": "RV",
  "player_type": "Farm",
  "contract_type": "Purchased Contract",
  "years_simple": "P",
  "upid": "3445"
}
```

**Filter:** Show only players where `manager === userTeam`

### **Team Mappings:**

```javascript
const TEAM_NAMES = {
    "WIZ": "Whiz Kids",
    "HAM": "Hammers",
    "B2J": "Btwn2Jackies",
    "CFL": "Country Fried Lamb",
    "LAW": "Law-Abiding Citizens",
    "LFB": "La Flama Blanca",
    "JEP": "Jepordizers!",
    "TBB": "The Bluke Blokes",
    "DRO": "Andromedans",
    "RV": "Rick Vaughn",
    "SAD": "not much of a donkey",
    "WAR": "Weekend Warriors"
};
```

---

## 🤖 **Backend Implementation**

### **Add to `health.py`:**

```python
from pydantic import BaseModel
from commands.trade_logic import create_trade_thread

class WebTradePayload(BaseModel):
    teams: list[str]  # ["WIZ", "HAM"] or ["WIZ", "HAM", "B2J"]
    players: dict[str, list[str]]  # {"WIZ": [...], "HAM": [...]}
    wizbucks: dict[str, int] = {}  # {"WIZ": 25, "HAM": 0}

@app.post("/api/trade/submit")
async def api_submit_trade(
    payload: WebTradePayload,
    authorized: bool = Depends(verify_api_key)
):
    """Submit trade from website - creates Discord thread using existing workflow"""
    
    if not bot.is_ready():
        raise HTTPException(status_code=503, detail="Discord bot not ready")
    
    # Get guild (your Discord server)
    GUILD_ID = 875592505926758480  # Your FBP Discord server ID
    guild = bot.get_guild(GUILD_ID)
    
    if not guild:
        raise HTTPException(status_code=500, detail="Could not find Discord server")
    
    # Format trade data to match existing Discord format
    trade_data = {
        "teams": payload.teams,
        "players": payload.players,
        "wizbucks": payload.wizbucks
    }
    
    # Use existing function!
    thread = await create_trade_thread(guild, trade_data)
    
    if not thread:
        raise HTTPException(status_code=500, detail="Failed to create trade thread")
    
    return {
        "success": True,
        "thread_id": str(thread.id),
        "thread_url": thread.jump_url,
        "message": "Trade submitted! Check Discord for approval thread."
    }
```

### **Add to Cloudflare Worker:**

Already exists! Just verify this is in `api-worker.js`:

```javascript
if (path === '/api/trade/submit' && request.method === 'POST') {
  return await proxyToBot('/api/trade/submit', env, request);
}
```

---

## 🎨 **Frontend Implementation**

### **`trade.html`** - Structure

Match `pad.html` and `draft.html` patterns:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Trade Builder - FBP Hub</title>
    <link rel="stylesheet" href="css/styles.css">
    <link rel="stylesheet" href="css/trade.css">
</head>
<body>
    <nav class="mobile-nav">
        <!-- Same nav as all pages -->
    </nav>

    <main class="container">
        <!-- Auth check -->
        <div class="auth-required" id="authRequired" style="display: none;">
            <i class="fas fa-lock"></i>
            <p>Please log in to submit trades</p>
            <a href="login.html" class="btn-primary">LOGIN</a>
        </div>

        <!-- Trade builder -->
        <div id="tradeContent" style="display: none;">
            <div class="page-header">
                <h2>TRADE BUILDER</h2>
                <p class="page-subtitle">Build multi-team trades</p>
            </div>

            <!-- Sticky info bar (like PAD WizBucks bar) -->
            <div class="trade-sticky-bar">
                <div class="trade-info-grid">
                    <div class="trade-info-item">
                        <span class="trade-info-label">Your WizBucks</span>
                        <span class="trade-info-value" id="yourWB">$142</span>
                    </div>
                    <div class="trade-info-item">
                        <span class="trade-info-label">Trade Window</span>
                        <span class="trade-info-value" id="tradeWindow">OPEN</span>
                    </div>
                    <div class="trade-info-item">
                        <span class="trade-info-label">Next Processing</span>
                        <span class="trade-info-value" id="nextProcessing">Tuesday</span>
                    </div>
                </div>
            </div>

            <!-- Team selector -->
            <div class="team-selector-section">
                <h3>Trading Teams</h3>
                <div class="team-selector-grid">
                    <button class="team-count-btn active" onclick="setTeamCount(2)">
                        2 Teams
                    </button>
                    <button class="team-count-btn" onclick="setTeamCount(3)">
                        3 Teams
                    </button>
                </div>
            </div>

            <!-- Trade builder grid -->
            <div class="trade-builder-grid" id="tradeBuilderGrid">
                <!-- Team 1 (Your team - pre-filled) -->
                <div class="trade-team-section">
                    <div class="trade-team-header">
                        <h4>YOUR TEAM (SENDING)</h4>
                        <span id="team1Name">WIZ</span>
                    </div>
                    <div class="trade-assets-list" id="team1AssetsList">
                        <!-- Empty state -->
                    </div>
                    <button class="btn-add-asset" onclick="showPlayerPicker('team1')">
                        <i class="fas fa-plus"></i> Add Player
                    </button>
                    <button class="btn-add-asset" onclick="showWBPicker('team1')">
                        <i class="fas fa-coins"></i> Add WizBucks
                    </button>
                </div>

                <!-- Team 2 -->
                <div class="trade-team-section">
                    <div class="trade-team-header">
                        <h4>TEAM 2</h4>
                        <select id="team2Select" class="team-select">
                            <option value="">Select Team...</option>
                            <!-- Populated dynamically -->
                        </select>
                    </div>
                    <div class="trade-assets-list" id="team2AssetsList"></div>
                    <button class="btn-add-asset" onclick="showPlayerPicker('team2')" disabled id="team2AddPlayer">
                        <i class="fas fa-plus"></i> Add Player
                    </button>
                    <button class="btn-add-asset" onclick="showWBPicker('team2')" disabled id="team2AddWB">
                        <i class="fas fa-coins"></i> Add WizBucks
                    </button>
                </div>

                <!-- Team 3 (optional) -->
                <div class="trade-team-section" id="team3Section" style="display: none;">
                    <!-- Same structure -->
                </div>
            </div>

            <!-- Trade summary (what each team RECEIVES) -->
            <div class="trade-summary-section">
                <h3>Trade Summary</h3>
                <div id="tradeSummary"></div>
            </div>

            <!-- Actions -->
            <div class="trade-actions">
                <button class="btn-secondary" onclick="clearTrade()">
                    <i class="fas fa-times"></i> Clear
                </button>
                <button class="btn-primary" onclick="previewTrade()" id="submitTradeBtn" disabled>
                    <i class="fas fa-eye"></i> Preview Trade
                </button>
            </div>
        </div>
    </main>

    <!-- Player picker modal (reuse admin.html pattern) -->
    <div class="modal" id="playerPickerModal">
        <div class="modal-content">
            <div class="modal-header">
                <h3>Select Player</h3>
                <button class="modal-close" onclick="closePlayerPicker()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div class="search-bar-inline">
                    <i class="fas fa-search"></i>
                    <input type="text" id="playerPickerSearch" placeholder="Search your roster...">
                </div>
                <div id="playerPickerResults"></div>
            </div>
        </div>
    </div>

    <!-- WizBucks picker modal -->
    <div class="modal" id="wbPickerModal">
        <div class="modal-content">
            <div class="modal-header">
                <h3>Add WizBucks</h3>
                <button class="modal-close" onclick="closeWBPicker()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <p>Available: <strong id="wbAvailable">$142</strong></p>
                <input type="number" id="wbAmount" min="5" step="5" placeholder="Amount ($5 increments)">
                <button class="btn-primary" onclick="confirmWB()">Add</button>
            </div>
        </div>
    </div>

    <!-- Preview modal (reuse confirmation modal pattern) -->
    <div class="confirmation-modal" id="tradePreviewModal">
        <div class="confirmation-content">
            <div class="confirmation-header">
                <h2>Confirm Trade</h2>
                <p>Review before submitting to Discord</p>
            </div>
            <div id="tradePreviewContent"></div>
            <div class="confirmation-warning">
                <i class="fas fa-info-circle"></i>
                <strong>This will create a private thread in Discord</strong>
                <p>All managers will need to approve before admin review</p>
            </div>
            <div class="confirmation-actions">
                <button class="btn-secondary" onclick="closePreview()">Cancel</button>
                <button class="btn-primary" onclick="submitTrade()">
                    <i class="fas fa-paper-plane"></i> Submit to Discord
                </button>
            </div>
        </div>
    </div>

    <script src="js/main.js"></script>
    <script src="js/auth.js"></script>
    <script src="js/trade.js"></script>
</body>
</html>
```

---

### **`css/trade.css`** - Styling

Match PAD and draft patterns:

```css
/* Trade sticky bar (like PAD WizBucks bar) */
.trade-sticky-bar {
    position: sticky;
    top: 80px;
    z-index: 120;
    background-color: var(--bg-charcoal);
    border: var(--border-width-thick) solid var(--primary-red);
    border-radius: var(--radius-lg);
    padding: var(--space-lg);
    margin-bottom: var(--space-xl);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.trade-info-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--space-lg);
}

.trade-info-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-xs);
}

.trade-info-label {
    font-family: var(--font-body);
    font-size: var(--text-xs);
    color: var(--text-gray);
    text-transform: uppercase;
    font-weight: 700;
}

.trade-info-value {
    font-family: var(--font-mono);
    font-size: var(--text-2xl);
    font-weight: 700;
    color: var(--accent-yellow);
}

/* Team selector */
.team-selector-section {
    background-color: var(--bg-charcoal);
    border: var(--border-width) solid rgba(255, 255, 255, 0.1);
    border-radius: var(--radius-lg);
    padding: var(--space-xl);
    margin-bottom: var(--space-xl);
}

.team-selector-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--space-md);
}

.team-count-btn {
    background: transparent;
    border: var(--border-width) solid var(--primary-red);
    color: var(--text-white);
    padding: var(--space-lg);
    border-radius: var(--radius-md);
    font-family: var(--font-title);
    font-weight: 700;
    font-size: var(--text-lg);
    cursor: pointer;
    transition: all var(--transition-fast);
}

.team-count-btn.active {
    background-color: var(--primary-red);
}

/* Trade builder grid */
.trade-builder-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--space-xl);
    margin-bottom: var(--space-xl);
}

.trade-builder-grid.three-team {
    grid-template-columns: repeat(3, 1fr);
}

.trade-team-section {
    background-color: var(--bg-charcoal);
    border: var(--border-width-thick) solid var(--primary-red);
    border-radius: var(--radius-lg);
    padding: var(--space-xl);
}

.trade-team-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-lg);
    padding-bottom: var(--space-md);
    border-bottom: var(--border-width) solid var(--primary-red);
}

.trade-team-header h4 {
    font-family: var(--font-title);
    color: var(--accent-yellow);
    font-size: var(--text-lg);
    letter-spacing: 1px;
}

.team-select {
    background-color: rgba(0, 0, 0, 0.3);
    border: var(--border-width) solid var(--primary-red);
    color: var(--text-white);
    padding: var(--space-sm) var(--space-md);
    border-radius: var(--radius-md);
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: var(--text-base);
}

/* Asset list */
.trade-assets-list {
    min-height: 200px;
    margin-bottom: var(--space-lg);
}

.trade-asset-card {
    background-color: rgba(0, 0, 0, 0.3);
    border: var(--border-width) solid rgba(255, 255, 255, 0.1);
    border-radius: var(--radius-md);
    padding: var(--space-md);
    margin-bottom: var(--space-sm);
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.trade-asset-info {
    flex: 1;
}

.trade-asset-name {
    font-weight: 700;
    color: var(--text-white);
    margin-bottom: var(--space-xs);
}

.trade-asset-meta {
    font-size: var(--text-sm);
    color: var(--text-gray);
}

.trade-asset-wb {
    font-family: var(--font-mono);
    font-size: var(--text-xl);
    font-weight: 700;
    color: var(--accent-yellow);
}

.btn-remove-asset {
    background: transparent;
    border: var(--border-width) solid #f44336;
    color: #f44336;
    padding: var(--space-xs) var(--space-sm);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: all var(--transition-fast);
}

.btn-remove-asset:hover {
    background-color: #f44336;
    color: white;
}

.btn-add-asset {
    width: 100%;
    background: transparent;
    border: var(--border-width) dashed rgba(255, 255, 255, 0.2);
    color: var(--text-gray);
    padding: var(--space-md);
    border-radius: var(--radius-md);
    margin-bottom: var(--space-sm);
    cursor: pointer;
    font-family: var(--font-body);
    font-weight: 600;
    transition: all var(--transition-fast);
}

.btn-add-asset:hover:not(:disabled) {
    border-color: var(--primary-red);
    background-color: rgba(239, 62, 66, 0.05);
    color: var(--text-white);
}

.btn-add-asset:disabled {
    opacity: 0.3;
    cursor: not-allowed;
}

/* Trade summary */
.trade-summary-section {
    background-color: var(--bg-charcoal);
    border: var(--border-width-thick) solid var(--accent-yellow);
    border-radius: var(--radius-lg);
    padding: var(--space-xl);
    margin-bottom: var(--space-xl);
}

.trade-summary-section h3 {
    color: var(--accent-yellow);
    margin-bottom: var(--space-lg);
}

.trade-summary-team {
    background-color: rgba(0, 0, 0, 0.3);
    border-radius: var(--radius-md);
    padding: var(--space-lg);
    margin-bottom: var(--space-md);
}

.trade-summary-team h4 {
    color: var(--primary-red);
    margin-bottom: var(--space-md);
}

/* Mobile responsive */
@media (max-width: 767px) {
    .trade-builder-grid {
        grid-template-columns: 1fr;
    }
    
    .trade-info-grid {
        grid-template-columns: repeat(2, 1fr);
        gap: var(--space-md);
    }
}
```

---

### **`js/trade.js`** - Logic

```javascript
/**
 * FBP Hub - Trade Builder
 * Connects to Discord trade approval workflow
 */

let TRADE_STATE = {
    userTeam: null,
    teamCount: 2,
    teams: {
        team1: null,  // User's team (auto-filled)
        team2: null,
        team3: null
    },
    assets: {
        team1: { players: [], wizbucks: 0 },
        team2: { players: [], wizbucks: 0 },
        team3: { players: [], wizbucks: 0 }
    },
    currentPickerTeam: null
};

/**
 * Initialize trade page
 */
async function initTradePage() {
    console.log('🔄 Initializing trade page...');
    
    // Check auth
    if (!authManager.isAuthenticated()) {
        document.getElementById('authRequired').style.display = 'flex';
        return;
    }
    
    TRADE_STATE.userTeam = authManager.getTeam();
    TRADE_STATE.teams.team1 = TRADE_STATE.userTeam.abbreviation;
    
    document.getElementById('tradeContent').style.display = 'block';
    
    // Load data
    await loadTradeData();
    
    // Setup UI
    populateTeamSelectors();
    updateTradeInfo();
    displayTrade();
}

/**
 * Load trade window info
 */
async function loadTradeData() {
    // Get processing windows
    const now = new Date();
    const day = now.getDay(); // 0=Sun, 6=Sat
    
    let nextProcessing = 'Tuesday';
    if (day === 0 || day === 1) nextProcessing = 'Tuesday';
    else if (day >= 2 && day <= 3) nextProcessing = 'Thursday';
    else nextProcessing = 'Sunday';
    
    document.getElementById('nextProcessing').textContent = nextProcessing;
    
    // Get WizBucks balance
    const teamFull = TEAM_ABBR_TO_FULL[TRADE_STATE.userTeam.abbreviation];
    const wb = FBPHub.data.wizbucks?.[teamFull] || 0;
    document.getElementById('yourWB').textContent = `$${wb}`;
    
    // Check trade window (opens after APA, closes Jul 31)
    // For now, just show OPEN
    document.getElementById('tradeWindow').textContent = 'OPEN';
}

/**
 * Populate team selectors
 */
function populateTeamSelectors() {
    const teams = Object.keys(TEAM_NAMES).filter(t => t !== TRADE_STATE.userTeam.abbreviation);
    
    const options = teams.map(abbr => 
        `<option value="${abbr}">${abbr} - ${TEAM_NAMES[abbr]}</option>`
    ).join('');
    
    document.getElementById('team2Select').innerHTML = '<option value="">Select Team...</option>' + options;
    document.getElementById('team3Select').innerHTML = '<option value="">Select Team...</option>' + options;
    
    // Setup change handlers
    document.getElementById('team2Select').addEventListener('change', (e) => {
        TRADE_STATE.teams.team2 = e.target.value;
        updateTeamButtons();
        displayTrade();
    });
    
    document.getElementById('team3Select')?.addEventListener('change', (e) => {
        TRADE_STATE.teams.team3 = e.target.value;
        updateTeamButtons();
        displayTrade();
    });
}

/**
 * Set team count (2 or 3)
 */
function setTeamCount(count) {
    TRADE_STATE.teamCount = count;
    
    // Update buttons
    document.querySelectorAll('.team-count-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    event.target.classList.add('active');
    
    // Show/hide team 3
    const team3Section = document.getElementById('team3Section');
    if (team3Section) {
        team3Section.style.display = count === 3 ? 'block' : 'none';
    }
    
    const grid = document.getElementById('tradeBuilderGrid');
    if (grid) {
        grid.classList.toggle('three-team', count === 3);
    }
    
    displayTrade();
}

/**
 * Show player picker for a team
 */
function showPlayerPicker(teamKey) {
    TRADE_STATE.currentPickerTeam = teamKey;
    
    const teamAbbr = TRADE_STATE.teams[teamKey];
    if (!teamAbbr) {
        showToast('Select a team first', 'error');
        return;
    }
    
    // Get team's roster
    const roster = FBPHub.data.players.filter(p => p.manager === teamAbbr || p.FBP_Team === teamAbbr);
    
    document.getElementById('playerPickerModal').classList.add('active');
    
    // Setup search
    const searchInput = document.getElementById('playerPickerSearch');
    searchInput.value = '';
    
    displayPlayerPickerResults(roster);
    
    searchInput.oninput = () => {
        const query = searchInput.value.toLowerCase();
        const filtered = roster.filter(p => 
            p.name.toLowerCase().includes(query) ||
            (p.position || '').toLowerCase().includes(query)
        );
        displayPlayerPickerResults(filtered);
    };
}

/**
 * Display player picker results
 */
function displayPlayerPickerResults(players) {
    const container = document.getElementById('playerPickerResults');
    
    if (players.length === 0) {
        container.innerHTML = '<div class="empty-state">No players found</div>';
        return;
    }
    
    container.innerHTML = players.map(p => `
        <div class="player-result-card" onclick="selectPlayer('${p.upid}')">
            <div class="player-result-info">
                <div class="player-result-name">${p.name}</div>
                <div class="player-result-meta">
                    <span>${p.position}</span>
                    <span>${p.team}</span>
                    <span>${p.years_simple || 'N/A'}</span>
                </div>
            </div>
        </div>
    `).join('');
}

/**
 * Select player and add to trade
 */
function selectPlayer(upid) {
    const player = FBPHub.data.players.find(p => p.upid === upid);
    if (!player) return;
    
    const teamKey = TRADE_STATE.currentPickerTeam;
    
    // Check not already added
    if (TRADE_STATE.assets[teamKey].players.some(p => p.upid === upid)) {
        showToast('Player already added', 'warning');
        return;
    }
    
    TRADE_STATE.assets[teamKey].players.push(player);
    
    closePlayerPicker();
    displayTrade();
    showToast(`${player.name} added`, 'success');
}

/**
 * Show WizBucks picker
 */
function showWBPicker(teamKey) {
    TRADE_STATE.currentPickerTeam = teamKey;
    
    const teamAbbr = TRADE_STATE.teams[teamKey];
    if (!teamAbbr) {
        showToast('Select a team first', 'error');
        return;
    }
    
    // Get available WB
    const teamFull = TEAM_ABBR_TO_FULL[teamAbbr];
    const available = FBPHub.data.wizbucks?.[teamFull] || 0;
    
    document.getElementById('wbAvailable').textContent = `$${available}`;
    document.getElementById('wbAmount').value = '';
    document.getElementById('wbAmount').max = available;
    
    document.getElementById('wbPickerModal').classList.add('active');
}

/**
 * Confirm WB amount
 */
function confirmWB() {
    const amount = parseInt(document.getElementById('wbAmount').value);
    
    if (!amount || amount < 5) {
        showToast('Minimum $5', 'error');
        return;
    }
    
    if (amount % 5 !== 0) {
        showToast('Must be $5 increments', 'error');
        return;
    }
    
    const teamKey = TRADE_STATE.currentPickerTeam;
    TRADE_STATE.assets[teamKey].wizbucks = amount;
    
    closeWBPicker();
    displayTrade();
    showToast(`$${amount} WB added`, 'success');
}

/**
 * Display current trade
 */
function displayTrade() {
    // Display each team's assets
    for (let i = 1; i <= 3; i++) {
        const teamKey = `team${i}`;
        const container = document.getElementById(`${teamKey}AssetsList`);
        if (!container) continue;
        
        const assets = TRADE_STATE.assets[teamKey];
        const playerCards = assets.players.map(p => `
            <div class="trade-asset-card">
                <div class="trade-asset-info">
                    <div class="trade-asset-name">${p.name}</div>
                    <div class="trade-asset-meta">${p.position} • ${p.team} • ${p.years_simple || 'N/A'}</div>
                </div>
                <button class="btn-remove-asset" onclick="removePlayer('${teamKey}', '${p.upid}')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `).join('');
        
        const wbCard = assets.wizbucks > 0 ? `
            <div class="trade-asset-card">
                <div class="trade-asset-wb">$${assets.wizbucks} WB</div>
                <button class="btn-remove-asset" onclick="removeWB('${teamKey}')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        ` : '';
        
        container.innerHTML = playerCards + wbCard || '<div class="empty-state">No assets added</div>';
    }
    
    // Update summary (what each team RECEIVES)
    updateTradeSummary();
    
    // Enable/disable submit
    const hasEnoughAssets = TRADE_STATE.assets.team1.players.length > 0 || TRADE_STATE.assets.team1.wizbucks > 0;
    const hasTeam2 = !!TRADE_STATE.teams.team2;
    const hasTeam2Assets = TRADE_STATE.assets.team2.players.length > 0 || TRADE_STATE.assets.team2.wizbucks > 0;
    
    document.getElementById('submitTradeBtn').disabled = !(hasEnoughAssets && hasTeam2 && hasTeam2Assets);
}

/**
 * Update trade summary (inverted - shows what each receives)
 */
function updateTradeSummary() {
    const container = document.getElementById('tradeSummary');
    
    const teamKeys = TRADE_STATE.teamCount === 3 ? ['team1', 'team2', 'team3'] : ['team1', 'team2'];
    
    const summaryHTML = teamKeys.map(teamKey => {
        const teamAbbr = TRADE_STATE.teams[teamKey];
        if (!teamAbbr) return '';
        
        // What this team RECEIVES = what OTHER teams send
        const receiving = [];
        
        teamKeys.forEach(otherKey => {
            if (otherKey === teamKey) return;
            
            const otherAssets = TRADE_STATE.assets[otherKey];
            otherAssets.players.forEach(p => receiving.push(`${p.name} (${p.position})`));
            if (otherAssets.wizbucks > 0) receiving.push(`$${otherAssets.wizbucks} WB`);
        });
        
        if (receiving.length === 0) return '';
        
        return `
            <div class="trade-summary-team">
                <h4>${teamAbbr} receives:</h4>
                <ul>
                    ${receiving.map(r => `<li>${r}</li>`).join('')}
                </ul>
            </div>
        `;
    }).join('');
    
    container.innerHTML = summaryHTML || '<div class="empty-state">Add assets to see trade summary</div>';
}

/**
 * Preview trade before submit
 */
function previewTrade() {
    // Build preview
    const teams = [TRADE_STATE.teams.team1, TRADE_STATE.teams.team2];
    if (TRADE_STATE.teamCount === 3 && TRADE_STATE.teams.team3) {
        teams.push(TRADE_STATE.teams.team3);
    }
    
    const previewHTML = teams.map(teamAbbr => {
        // What this team receives
        const receiving = [];
        
        teams.forEach(otherTeam => {
            if (otherTeam === teamAbbr) return;
            
            const otherKey = Object.keys(TRADE_STATE.teams).find(k => TRADE_STATE.teams[k] === otherTeam);
            const assets = TRADE_STATE.assets[otherKey];
            
            assets.players.forEach(p => {
                receiving.push(`${p.position} ${p.name} [${p.team}] [${p.years_simple || 'NA'}]`);
            });
            
            if (assets.wizbucks > 0) {
                receiving.push(`$${assets.wizbucks} WB`);
            }
        });
        
        return `
            <div class="confirmation-section">
                <h4>🔁 ${teamAbbr} receives:</h4>
                ${receiving.map(r => `<p>• ${r}</p>`).join('')}
            </div>
        `;
    }).join('');
    
    document.getElementById('tradePreviewContent').innerHTML = previewHTML;
    document.getElementById('tradePreviewModal').classList.add('active');
}

/**
 * Submit trade to Discord via API
 */
async function submitTrade() {
    const btn = event.target;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting...';
    
    // Build payload
    const teams = [TRADE_STATE.teams.team1, TRADE_STATE.teams.team2];
    if (TRADE_STATE.teamCount === 3 && TRADE_STATE.teams.team3) {
        teams.push(TRADE_STATE.teams.team3);
    }
    
    const players = {};
    const wizbucks = {};
    
    teams.forEach(teamAbbr => {
        const teamKey = Object.keys(TRADE_STATE.teams).find(k => TRADE_STATE.teams[k] === teamAbbr);
        const assets = TRADE_STATE.assets[teamKey];
        
        // What OTHER teams receive from this team (send = receive logic)
        const otherTeams = teams.filter(t => t !== teamAbbr);
        const receiving = [];
        
        otherTeams.forEach(otherTeam => {
            const otherKey = Object.keys(TRADE_STATE.teams).find(k => TRADE_STATE.teams[k] === otherTeam);
            const otherAssets = TRADE_STATE.assets[otherKey];
            
            otherAssets.players.forEach(p => {
                receiving.push(`${p.position} ${p.name} [${p.team}] [${p.years_simple || 'NA'}]`);
            });
            
            if (otherAssets.wizbucks > 0) {
                receiving.push(`$${otherAssets.wizbucks} WB`);
            }
        });
        
        players[teamAbbr] = receiving;
        wizbucks[teamAbbr] = assets.wizbucks;
    });
    
    const payload = { teams, players, wizbucks };
    
    try {
        const session = authManager.getSession();
        const response = await fetch(`${AUTH_CONFIG.workerUrl}/api/trade/submit`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${session.token}`
            },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Trade submission failed');
        }
        
        const result = await response.json();
        
        closePreview();
        showToast('✅ Trade submitted! Check Discord for approval thread.', 'success');
        
        // Clear trade
        setTimeout(() => {
            clearTrade();
        }, 2000);
        
    } catch (err) {
        console.error('Trade submission error:', err);
        showToast(`Submission failed: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-paper-plane"></i> Submit to Discord';
    }
}

/**
 * Remove player from trade
 */
function removePlayer(teamKey, upid) {
    TRADE_STATE.assets[teamKey].players = TRADE_STATE.assets[teamKey].players.filter(p => p.upid !== upid);
    displayTrade();
}

/**
 * Remove WizBucks from trade
 */
function removeWB(teamKey) {
    TRADE_STATE.assets[teamKey].wizbucks = 0;
    displayTrade();
}

/**
 * Clear entire trade
 */
function clearTrade() {
    TRADE_STATE.teams.team2 = null;
    TRADE_STATE.teams.team3 = null;
    
    Object.keys(TRADE_STATE.assets).forEach(key => {
        TRADE_STATE.assets[key] = { players: [], wizbucks: 0 };
    });
    
    document.getElementById('team2Select').value = '';
    document.getElementById('team3Select').value = '';
    
    displayTrade();
}

/**
 * Update team add buttons (enable when team selected)
 */
function updateTeamButtons() {
    const team2 = TRADE_STATE.teams.team2;
    document.getElementById('team2AddPlayer').disabled = !team2;
    document.getElementById('team2AddWB').disabled = !team2;
    
    if (TRADE_STATE.teamCount === 3) {
        const team3 = TRADE_STATE.teams.team3;
        document.getElementById('team3AddPlayer').disabled = !team3;
        document.getElementById('team3AddWB').disabled = !team3;
    }
}

// Modal helpers
function closePlayerPicker() {
    document.getElementById('playerPickerModal').classList.remove('active');
}

function closeWBPicker() {
    document.getElementById('wbPickerModal').classList.remove('active');
}

function closePreview() {
    document.getElementById('tradePreviewModal').classList.remove('active');
}

function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'}"></i>
        <span>${message}</span>
    `;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// Expose globally
window.initTradePage = initTradePage;
window.setTeamCount = setTeamCount;
window.showPlayerPicker = showPlayerPicker;
window.selectPlayer = selectPlayer;
window.showWBPicker = showWBPicker;
window.confirmWB = confirmWB;
window.previewTrade = previewTrade;
window.submitTrade = submitTrade;
window.removePlayer = removePlayer;
window.removeWB = removeWB;
window.clearTrade = clearTrade;
window.closePlayerPicker = closePlayerPicker;
window.closeWBPicker = closeWBPicker;
window.closePreview = closePreview;
```

---

## 🤖 **Backend Integration**

### **Add to `health.py`:**

```python
from pydantic import BaseModel
from commands.trade_logic import create_trade_thread

class WebTradePayload(BaseModel):
    teams: list[str]
    players: dict[str, list[str]]
    wizbucks: dict[str, int] = {}

@app.post("/api/trade/submit")
async def api_submit_trade(
    payload: WebTradePayload,
    authorized: bool = Depends(verify_api_key)
):
    """Submit trade from website - triggers existing Discord workflow"""
    
    if not bot.is_ready():
        raise HTTPException(status_code=503, detail="Bot not ready")
    
    # Your Discord server ID
    GUILD_ID = 875592505926758480
    guild = bot.get_guild(GUILD_ID)
    
    if not guild:
        raise HTTPException(status_code=500, detail="Discord server not found")
    
    # Format exactly like Discord command does
    trade_data = {
        "teams": payload.teams,
        "players": payload.players,
        "wizbucks": payload.wizbucks
    }
    
    # Call your existing function!
    thread = await create_trade_thread(guild, trade_data)
    
    if not thread:
        raise HTTPException(status_code=500, detail="Failed to create thread")
    
    return {
        "success": True,
        "thread_id": str(thread.id),
        "thread_url": thread.jump_url
    }
```

### **Add to Cloudflare Worker:**

```javascript
// In api-worker.js (ADD THIS)
if (path === '/api/trade/submit' && request.method === 'POST') {
  return await proxyToBot('/api/trade/submit', env, request);
}
```

---

## 📋 **Complete File List**

### **Frontend (3 files):**
1. `trade.html` - Trade builder form
2. `css/trade.css` - Styling (matches FBP Hub design)
3. `js/trade.js` - Trade logic and API integration

### **Backend (2 additions):**
4. Add endpoint to `health.py`
5. Add route to Cloudflare Worker `api-worker.js`

### **Existing (No changes needed):**
- `commands/trade.py` - Discord /trade command
- `commands/trade_logic.py` - Approval workflow
- All Discord functionality stays the same!

---

## ✅ **How It Works**

### **User Flow:**

1. Manager opens `trade.html`
2. Selects 2 or 3 teams
3. Adds players from each team's roster
4. Adds WizBucks (optional, $5 increments)
5. Preview shows what each team receives
6. Clicks "Submit to Discord"
7. **Creates private thread in Discord** (existing workflow!)
8. All managers approve via Discord buttons
9. Admin approves via Discord
10. Posted to #trades channel

### **Data Flow:**

```
Website form
  ↓
POST to fbp-auth.zpressley.workers.dev/api/trade/submit
  ↓
Worker adds X-API-Key
  ↓
Proxies to Render bot: POST /api/trade/submit
  ↓
Bot calls existing create_trade_thread(guild, trade_data)
  ↓
Creates private thread in #pending-trades
  ↓
Existing approval workflow (no changes!)
```

---

## 🎨 **Design Requirements**

### **Must Match:**
- Layout: `pad.html` (sticky bar, sections, modals)
- Styling: `css/styles.css` (red/yellow, Barlow Condensed fonts)
- Components: `css/admin.css` (modals, buttons)
- Patterns: `js/pad.js` (auth check, API calls)

### **Key Visual Elements:**

**Sticky Info Bar:**
- Your WizBucks balance
- Trade window status (OPEN/CLOSED)
- Next processing day
- Matches PAD sticky bar style

**Team Sections:**
- Red borders
- Yellow headers
- Player cards with remove buttons
- Add player/WB buttons

**Trade Summary:**
- Yellow border
- Shows what each team RECEIVES
- Inverted view (Team A gets Team B's assets)

**Preview Modal:**
- Red border
- Yellow header
- Warning about Discord thread
- Confirm/Cancel buttons

---

## 🚨 **Critical Integration Points**

### **1. Player Format:**

Website sends formatted strings (same as Discord):

```javascript
// Format: "POS Name [Team] [Contract]"
const formatted = `${p.position} ${p.name} [${p.team}] [${p.years_simple || 'NA'}]`;
```

### **2. Trade Data Structure:**

Must match Discord format exactly:

```python
{
  "teams": ["WIZ", "HAM", "B2J"],  # 2 or 3 teams
  "players": {
    "WIZ": ["SS Bobby Witt Jr. [KC] [VC-2]", "$25 WB"],
    "HAM": ["OF Kyle Schwarber [PHI] [FC-1]"],
    "B2J": ["SS Leo de Vries [ATL] [P]"]
  },
  "wizbucks": {
    "WIZ": 25,
    "HAM": 0,
    "B2J": 0
  }
}
```

### **3. Channel IDs:**

From `trade_logic.py` (keep these):

```python
PENDING_CHANNEL_ID = 1356234086833848492
ADMIN_REVIEW_CHANNEL_ID = 875594022033436683
TRADE_CHANNEL_ID = 1197200421639438537
```

### **4. Guild ID:**

```python
GUILD_ID = 875592505926758480  # Your FBP Discord server
```

---

## 📝 **Acceptance Checklist**

### **Frontend:**
- [ ] `trade.html` matches PAD/draft design
- [ ] 2-team and 3-team support
- [ ] Player picker shows only team's roster
- [ ] WizBucks validation ($5 increments)
- [ ] Trade summary shows receives (inverted)
- [ ] Preview modal before submit
- [ ] Mobile responsive
- [ ] Works with authManager
- [ ] Sticky info bar

### **Backend:**
- [ ] Endpoint added to `health.py`
- [ ] Uses existing `create_trade_thread()`
- [ ] Formats data correctly
- [ ] Returns thread URL
- [ ] Error handling

### **Integration:**
- [ ] Worker route exists
- [ ] Creates Discord thread
- [ ] Managers can approve in Discord
- [ ] Admin can approve in Discord
- [ ] Posts to #trades channel
- [ ] Existing workflow unchanged

---

## 🔄 **What Changes from Original Trade Tool**

**Original (This Chat):**
- Built trade.html from scratch
- Didn't know about Discord workflow
- Standalone implementation

**New (This Request):**
- Connect to existing Discord workflow ✅
- Use existing `create_trade_thread()` ✅
- Keep all Discord functionality ✅
- Website is just another input method ✅

---

**Build the website form that triggers your existing Discord approval system. No changes to Discord code needed!**
