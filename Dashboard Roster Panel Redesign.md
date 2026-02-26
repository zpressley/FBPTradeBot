# Dashboard Roster Panel Redesign
## Problem
The roster panel (Panel 0) in dashboard.html has:
* Massive whitespace (panel is ~5800px tall)
* Generic batting/pitching/bench layout instead of FBP positional roster
* Unreadable on mobile
* Drag-and-drop that doesn't work well
* No season picker; shows MiLB stats
## Current State
* `js/lineup-builder.js` — renders generic batting(9)/pitching(10)/bench(11) tables with drag-and-drop
* `css/lineup-builder.css` — styles for current table layout + swap modal
* `dashboard-tabs.js` — calls `LineupBuilder.init()` and `renderAdvancedLineup()` for Panel 0
* `data/player_stats.json` — 23k entries, seasons 2006-2025, levels MLB/MiLB, types batting/pitching
* `data/combined_players.json` — 6.5k players, positions comma-separated (C, 1B, 2B, 3B, SS, CF, OF, SP, RP)
* Player positions from combined_players: `1B, 2B, 3B, C, CF, OF, RP, SP, SS`
## Proposed Changes
### 1. New Roster Slot Structure
FBP roster (26 man):
**Batters (9):** C, 1B, 2B, SS, 3B, CF, OF, OF, UTIL
**Pitchers (6):** SP, RP, P, P, P, P
**Bench (11):** BN × 11
Position eligibility:
* C → has "C"
* 1B → has "1B"
* 2B → has "2B"
* SS → has "SS"
* 3B → has "3B"
* CF → has "CF"
* OF → has "OF" or "CF" (CF qualifies)
* UTIL → any non-pitcher
* SP → has "SP"
* RP → has "RP"
* P → has "SP" or "RP" or "P"
* BN → any player on team
### 2. Rewrite `js/lineup-builder.js`
* Replace generic batting/pitching/bench arrays with slot-based model: `ROSTER_SLOTS` constant defining each slot label and position eligibility
* Each slot rendered as a full-width row with a dropdown `<select>` to pick from qualifying team players (keepers + prospects)
* Season picker: toggle between 2024 and 2025 (default 2025). Filter `player_stats.json` to `level === 'MLB'` only
* Stats display: batter rows show H/AB, HR, RBI, SB, AVG, OPS; pitcher rows show IP, ERA, K, K/9
* Persist slot assignments to localStorage keyed by team abbreviation
* Remove all drag-and-drop and swap modal code
### 3. Rewrite `css/lineup-builder.css`
Mobile-first, Yahoo-style:
* Each roster slot is a full-width card/row
* Player name + contract badge on left, position label pill
* Stats extend to the right; each **row** scrolls horizontally independently (touch: horizontal pan on the row shows stats, vertical scroll outside rows = normal page scroll)
* Dropdown styled as a compact select or tap-to-open picker
* No whitespace; tight padding
Desktop:
* Same row structure but wider so more stats visible without scroll
* Stats columns align in a table-like grid
### 4. Fix panel height in `css/dashboard-tabs.css`
* Remove `min-height: 240px` from `.dash-panel` or make it `auto`
* Let content determine panel height
### 5. Files changed
* `js/lineup-builder.js` — full rewrite
* `css/lineup-builder.css` — full rewrite
* `css/dashboard-tabs.css` — remove min-height on `.dash-panel`
* `dashboard.html` — no changes needed (script tags already correct)
* `js/dashboard-tabs.js` — minor: `loadRosterPanel()` may need small update if LineupBuilder API changes
