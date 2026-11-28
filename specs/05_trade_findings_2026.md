# Trade System: 2026 Findings

Audit of FBPTradeBot + fbp-hub trade code and `data/trades.json`, done 9/29/2026 to prepare the trade rebuild spec. File references are to the 2026 code.

## How managers actually use it

- 95 trades, Feb 18 to Aug 2. 63 approved, 20 withdrawn (all by the initiator), 7 rejected, 5 expired.
- **All 95 were 2-team.** No 3-team trade has ever been made.
- 316 player moves, 36 WizBucks moves, 14 pick moves. Trades ranged from 1 to 12 transfers.
- **Median time from submit to accept: about 1.5 minutes.** Deals are negotiated in Discord and the portal is used to file them.
- 4 trades never went through the portal and had to be backfilled by script (`MANUAL-20260731-*`).

## Flow today

1. `trade.html`: pick 2 or 3 teams from dropdowns.
2. Each column is what that team **receives**, so you add your own player under the other team's column, via a modal with a From Team dropdown, search and checkboxes. Picks and WB have their own modals.
3. Review modal, then Send. The initiator counts as accepted. Counterparties get a Discord DM.
4. Counterparty: Review, then Accept, then a second Confirm Accept modal. Reject needs a reason.
5. All accepted: `admin_review`, and an Approve/Reject card posts to the Discord admin channel.
6. Admin approves in Discord. **Processing happens immediately on approval**, not on a processing day. The "Proc:" date in the #trades post is only a label.

A simple 1-for-1 takes about 10 clicks to submit, 3 to accept, 1 to approve.

## Assets

Players (MLB and prospects), keeper draft picks R1-15, WizBucks in $5 steps. Buy-ins are charged automatically when an R1-3 pick moves. Prospect draft picks can't be traded.

## Enforced rules

- 2-3 teams, no duplicates, submitter must be a party.
- Window: picks only during KAP. In-season trades from the keeper draft to `trade_deadline`.
- Ownership checked at submit, accept and approve. A conflict auto-withdraws the trade.
- WB balance checked at submit only.
- 12 active trades per initiator, 14-day expiry.

## Constitution rules NOT enforced

- Roster limits (26 active, 8 SP or 9 with an SP/RP, 14 pitchers, 16 batters).
- Two-way players must be traded as a package (`2wayplayer` flag exists, never checked).
- Processing windows (Sun-Mon to Tue, Tue-Wed to Thu, Thu-Sat to Sun). The "Next Processing" day on the page is hardcoded.
- R1-3 picks need **both** managers bought in (Const. 05.6.3.1). The code charges the pick's original owner, and the page text says the sending team is charged. Three different versions of the rule.
- KAP pick-tax brackets and the $435 cap.
- The KAP trade window should close 3 days before the keeper deadline. `season_dates.json` has a `preseason-trade deadline` key that the code never reads.
- The deadline is a date compared in UTC, so the window closes at 8 PM ET.

## Security

- **Admin approve/reject endpoints only check that the caller is a manager, not an admin.** The Worker proxies every `/api/trade/*` path, so any manager can approve any trade in admin review, including their own.
- The Worker's `getManagerTeamForToken` accepts `Bearer dev_token_<TEAM>_...` with no environment check, so anyone could act as any team (if the deployed Worker matches this source; unverified).
- 17 of 63 approved trades were approved by an admin who was a party to the trade. Not forbidden by the Constitution, but it needs a policy decision.

## Bugs

- An approval is marked `approved` even if applying it throws. `DATA_LOCK` is taken without `try/finally`.
- A WB shortfall at approval only warns. Players move and the WB leg is silently dropped.
- The hourly expiry sweep expires trades in `admin_review` too, so an accepted trade dies if admins sit on it 14 days.
- Discord `/trade` never works: it compares franchise names to team abbreviations, never creates a record, and would DM the initiator too.
- DM links default to `trade-portal.html`, but the page is `trade.html` (unless an env var overrides).
- Withdraw shows on `admin_review` trades but always fails. Non-initiators can't see `admin_review` trades anywhere. A party can still reject an `admin_review` or `expired` trade.
- Stale UI text: "Trade Window: OPEN" is hardcoded, and the page promises a Discord thread that only 16 of 95 trades got.
- The website reads rosters, WB and picks from a copy synced through GitHub Actions, which lags and likely causes "owned by X" errors at submit.
- Data drift in `trades.json`: three ID formats, 3 approved trades with no `data_applied_at`, one approved with only one acceptance, `admin_decision_by` mixing abbreviations, "zpressley", "ADMIN" and "ADMIN_BACKFILL".

## UX friction

- The "receives" column model is backwards for the person building the trade.
- Two confirm modals to accept.
- From Team picked inside every modal in 3-team trades.
- No running "you give / you get" summary while building.
- A browser `prompt()` in the add-from-player-page shortcut.
- No preview of what the trade does to either roster.

## Info missing when deciding

Rank, age, level, ETA, FYPD for players (clicking a row only toggles selection). WB after the trade and WB tied up in other pending trades. Roster and pitcher/batter counts after. Prospect and DC slot counts. Two-way flag. A pick's original owner and buy-in consequences. Which teams have accepted in a 3-team trade. Keeper cost (only the tier exists in the data).

## Must preserve

1. Directed transfers of players, keeper picks and WB. 2-3 teams. "Receives" display in the Constitution's format for announcements.
2. Team identity from the server, never the request body. Submitter must be a party.
3. Window rules, with the deadline in ET.
4. Ownership checks at submit, accept and approve, auto-withdraw on conflict. WB enforced at approval, not just warned.
5. $5 WB steps, picks R1-15, R1-3 buy-in charge (payer per the Constitution), 12-active cap, 14-day expiry, initiator counts as accepted.
6. All parties accept, then admin approval with a real admin check. Rejections need a reason. Only the initiator withdraws.
7. Approval side effects in one transaction, run once: ownership, player events, draft order owner, WB ledger debit and credit.
8. Discord DMs at each step, admin card with persistent buttons, #trades post with Sub/Proc dates.
9. Stable trade IDs and a commissioner path for manual or backfilled trades.
