# FBP 2027 Specs

Plans for moving FBP from JSON files in git to Postgres on Neon, and for running the league on FBP instead of Yahoo in 2027. Planned in Cowork, built in Claude Code. If something here is wrong or out of date, flag it to Zach; don't edit a spec to match the code.

## Build order

| # | Spec | Status | Target |
|---|---|---|---|
| 1 | `03a_top100_snapshot.md` (Nov 1 Top 100 freeze) | Ready | Oct 31 (late, do first) |
| 2 | `01_neon_foundation.md` (Neon setup, schema v1, import, reconcile, design rules) | Ready | Shadow mode, Nov |
| 3 | `03_rosters_contracts_wizbucks.md` (contracts, ledger, roster rules) | Ready | Cutover Dec 15 |
| 4 | `02_auction_portal.md` | Ready | Before the 2027 auction |
| 5 | `05_trade_portal.md` | Ready | Before the KAP trade window |
| 6 | `04_draft_room.md` (website draft room, clock, boards) | Ready | Test drafts before the Prospect Draft |
| 7 | `07_team_page_lineups.md` (team page, daily lineups, eligibility) | Ready | Feb 28, 2027 |
| - | `06` preseason ops (PAD, KAP, APA) | Not written yet | |
| - | `08` scoring engine | Not written yet | |

## Findings

Audits of the 2026 code and data, with file and line references. Read the matching one before building an area.

- `03_findings_2026.md` (rosters, contracts, WizBucks, Constitution summary)
- `04_draft_findings_2026.md`
- `05_trade_findings_2026.md`

## Mockups

Clickable mockups live as claude.ai artifacts (Draft Room, Trade Desk, Team Page). Ask Zach for the links. They show layout and flow, not final data.

## How to build a spec

1. `claude --worktree <area>` and create a matching Neon branch.
2. Prompt: "Read `CLAUDE.md`, `specs/README.md` and `specs/<spec>.md` (plus its findings doc). Enter plan mode and give me a build plan mapped to the spec's tests. Don't write code until I approve."
3. Build, run the tests, open a PR, and check it against the spec's acceptance criteria.
