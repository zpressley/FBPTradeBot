# Spec 05: Trade Portal

**Status:** Draft for review
**Build target:** January 31, 2027
**Live:** When the KAP trade window opens (about Feb 17, 2027). The window is closed from the July 31 deadline until KAP, so nothing is at risk while this is built.
**Depends on:** Spec 01 (schema), Spec 03 (WizBucks and rosters written by the database). Uses the draft slot tables from Spec 04 for pick trades.
**Read first:** `WARP.md`, `specs/01_neon_foundation.md` (Design rules), `specs/05_trade_findings_2026.md`

## Goal

Same features as 2026, much easier to use. A simple 1-for-1 should take about 4 taps to send and 1 to accept. Both managers should see exactly what the trade does to their team before they agree. And turning down a bad offer should be as much fun as making a good one.

## Decisions (9/29)

1. **Trades apply the moment an admin approves.** Processing days existed so Yahoo moves could catch up. That's gone. The #trades post shows the approval date as the processing date.
2. **Pick trades follow the Constitution:** both managers must be bought into a round (R1-3) to trade a pick in that round. No automatic buy-in charges.
3. **Admins can approve trades they're part of.** Every approval is logged with who approved it, and the league sees every trade in #trades, which has always been enough.
4. **Negotiation lives in the portal too.** Every offer has a comment thread and counters. **Slander** as a response is left out of v1 (decided 10/27) and parked under Future ideas.

## Out of scope

- 3-team trades stay supported but aren't redesigned beyond what's below (none were made in 2026).
- Trading prospect draft picks (not allowed today).
- A trading block or "open to offers" list (future idea).
- The Discord `/trade` command. It has never worked and is removed.

## Rules (enforced in one place)

`trade_service.validate(trade, stage)` runs at **build** (as live feedback), **send**, **accept** and **approve**. Nothing else checks trade rules.

| Rule | Build / send | Accept | Approve |
|---|---|---|---|
| Every item is owned by the team sending it | block | block, auto-withdraw on conflict | block, auto-withdraw on conflict |
| Trade window open (picks only during KAP; in-season from the Keeper Draft to the deadline, deadline at 11:59 PM ET) | block | block | block |
| 2 or 3 teams, sender is a party, no duplicate items | block | | |
| WizBucks in $5 steps and within the sender's balance | block | block | **block** (2026 only warned and dropped the WB leg) |
| R1-3 pick: **both** teams bought into that round | block | block | block |
| Picks R1-15 only | block | | |
| Two-way players move as a package | block | | |
| Roster limits after the trade (26 active; 8 SP, or 9 with an SP/RP; 14 pitchers; 16 batters) | warn | warn | warn (shown to the admin) |
| 12 active offers per team | block | | |

Roster limits warn instead of block because managers fix rosters after trades, and the Constitution already handles violations with a WB penalty. Admins can decide case by case.

## Schema (migration `0005_trades.sql`)

Spec 01 imported 2026 trades into `trades` and `trade_transfers` with a `raw` column. This migration reshapes them into the tables below and keeps `raw` for history.

```sql
create table trades (
  trade_id        text primary key,               -- 'T27-0001': season + sequence
  season          int not null,
  status          text not null check (status in
                  ('pending','accepted','approved','rejected','countered','withdrawn','expired','voided')),
  created_by      text not null references teams,
  created_at      timestamptz not null default now(),
  expires_at      timestamptz,                    -- only while pending
  countered_from  text references trades,         -- the offer this one answers
  approved_by     text,
  approved_at     timestamptz,                    -- also the processing date
  closed_by       text,                           -- who rejected, withdrew or voided
  close_kind      text check (close_kind in ('reject','withdraw','conflict','expire','void')),
  close_note      text,
  source          text not null default 'portal' check (source in ('portal','admin_manual','import')),
  raw             jsonb
);

create table trade_parties (
  trade_id    text references trades,
  team_abbr   text references teams,
  accepted_at timestamptz,
  primary key (trade_id, team_abbr)
);

create table trade_items (
  id         bigserial primary key,
  trade_id   text not null references trades,
  kind       text not null check (kind in ('player','pick','wb')),
  upid       text references players,
  draft_id   text, overall int,                    -- for picks, points at draft_slots
  amount     int check (amount > 0 and amount % 5 = 0),
  from_team  text not null references teams,
  to_team    text not null references teams,
  check (from_team <> to_team)
);

create table trade_comments (
  id         bigserial primary key,
  trade_id   text not null references trades,
  team_abbr  text not null references teams,
  kind       text not null default 'comment' check (kind in ('comment','system')),
  body       text not null check (length(body) <= 500),
  created_at timestamptz not null default now()
);
```

Statuses, in plain terms:
- **pending:** sent, waiting on the other team(s). Expires after 7 days.
- **accepted:** every party accepted, waiting on an admin. **Never expires.**
- **approved:** applied. Done.
- **rejected:** turned down, with a reason (`close_kind = 'reject'`).
- **countered:** replaced by a counter offer (`countered_from` on the new one points back).
- **withdrawn:** pulled by the sender, or auto-withdrawn because an item moved (`'conflict'`).
- **expired**, **voided** (admin reversal of an approved trade, for the Constitution's injury-void rule).

## The builder

One screen, built for phones first.

1. **Pick a team.** A row of team chips. Or start from anywhere: every player card, roster page and team page gets a **Trade for** button that opens the builder with that player already added.
2. **Two columns from your side: You give / You get.** Each column is that team's roster with tabs: MLB, Prospects, Picks, WizBucks. Tap a row to add it, tap again to remove. No pop-ups.
   - Each row shows what matters when deciding: position, org, contract tier and keeper cost for MLB players. Rank, level, age, ETA and contract type (DC/PC/BC) for prospects. Round, pick number, original owner and buy-in status for picks.
   - Search box at the top of each column.
   - WizBucks: a stepper in $5 steps, showing the balance.
3. **Summary bar** pinned to the bottom: "You give 2 · You get 1 · **Review**." It turns red with the reason if something breaks a rule ("You're not bought into Round 2").
4. **Review** is one sheet: what each side gets, the impact panel, and an optional note that becomes the first comment. Then **Send**.

**3-team trades:** an "Add a third team" link under the team chips. With three teams, tapping an item asks which team gets it (three chips), then the columns show "You give / You get" per team.

A 1-for-1: team chip, their player, your player, Review, Send. **4 to 5 taps.**

## Impact panel

Shown live in the builder, on the review sheet, on every offer in the inbox, and to the admin. One row per team:

- **WizBucks:** now → after, plus how much is already tied up in their other pending offers ("$45 → $30 · $10 in other offers").
- **Roster:** active count vs 26, SP vs 8 (or 9), pitchers vs 14, batters vs 16. Anything over turns red with a warning.
- **Prospects:** PC / DC / BC counts after.
- **Picks:** which picks change hands, and the buy-in status of each team for those rounds.

## The inbox

Offers to you, offers you sent, and recent history, as cards. Each card shows "You give / You get" from **your** side, the impact panel, the comment thread, and a status tracker: **Sent → Accepted → Admin → Done**. In 3-team trades, the tracker shows who has accepted so far. Every party can see the card at every stage, including admin review (2026 hid it from non-senders).

Buttons on an offer to you:

- **Accept:** one tap, with a 5-second undo toast. If you're the last to accept, it goes to admin review.
- **Counter:** reopens the offer in the builder, pre-filled and flipped to your side. Sending it marks the original `countered` and links the two, so the thread carries over.
- **Reject:** needs a short reason.

On offers you sent: **Withdraw** while pending, and comment.

**Comments:** a simple thread on each offer, visible only to the parties (and admins), before and after approval. New comments DM the other side. 500 characters max. There's no league-wide comment section; league talk stays in Discord (#trades and #trade-talk).

## Admin

- **Web admin queue** (admins only): accepted trades waiting on approval, with the impact panel and any roster warnings. **Approve** or **Reject** with a reason. The Discord admin card stays too, with persistent buttons that survive restarts.
- **Real admin check:** the API reads the admin role from `teams.role`. A manager who isn't an admin gets a 403.
- **Admins can approve their own trades** (decision 3). The approver is logged on the trade and shown in the #trades post.
- **Manual entry:** create an already-approved trade for deals made outside the portal, with a required note. Same validation and side effects as a normal approval, `source = 'admin_manual'`.
- **Void:** reverses an approved trade (Constitution void rule) in one transaction, with a reason. Writes reversal events and a #trades post.

## Approval (one transaction)

`trade_service.approve(trade_id, admin)`:
1. Lock the trade row. If already approved, return it. Running it twice is harmless.
2. Run `validate(stage='approve')`. Any block rolls back with the reason.
3. Move players: close and open `roster_assignments` rows (contracts travel with the player).
4. Move picks: update `draft_slots.team_abbr`, keep `original_team`.
5. Move WizBucks: a debit and a credit in `wb_ledger`.
6. Write `player_events` rows (type Trade) for every player.
7. Set `status = 'approved'`, `approved_by`, `approved_at`.
8. Commit. Then queue notifications: DMs to all parties and the #trades post.

A failure anywhere leaves everything untouched (2026 could mark a trade approved after a half-applied change).

## Notifications

Sent through the same `notifications` outbox as the draft (Spec 04), with retries.

- **DM** to the other party on new offer, counter, comment, accept, reject, withdraw, approve. Each DM links straight to that offer.
- **#trades post** on approval in the Constitution's format: what each team receives, "Sub:" date and "Proc:" date (the same day now), and who approved.
- **Admin channel card** when a trade is fully accepted.

## Security

- Team identity always comes from the Worker's verified Discord login, never the request body.
- **Remove the `dev_token_<TEAM>` bypass** from the Worker (`getManagerTeamForToken`), or gate it to a dev environment variable that is never set in production.
- Admin routes check the admin role on the API side, not just in the Worker.

## Data migration

- Reshape the 95 imported 2026 trades into the new tables with `source = 'import'`. Normalize the three ID formats into `trade_id` while keeping the old ID in `raw`.
- Map 2026 statuses: `approved` → approved, `admin_rejected`/`rejected` → rejected, `withdrawn` → withdrawn, `expired` → expired, `admin_review` → accepted.
- Clean up `admin_decision_by` ("zpressley", "ADMIN", "ADMIN_BACKFILL", team abbreviations) into one `approved_by` value per trade.

## Tests (must pass before go-live)

1. A 1-for-1 from send to approved: ownership, contracts, player events and the #trades post all correct.
2. A player on the trade gets moved by another approved trade first. This trade auto-withdraws on accept, with a system comment explaining why.
3. WizBucks spent elsewhere between send and approve. Approval is blocked with the reason, nothing moves.
4. R1-3 pick where one team isn't bought into that round. Blocked at build with a clear message.
5. Two-way player without his other half. Blocked.
6. Roster over 26 after the trade. Warned in the builder, review and admin queue, not blocked.
7. Approval that fails halfway (forced error after the player moves). Nothing changes, status stays accepted.
8. Approve twice at the same moment. Applied once.
9. A non-admin calls the approve route. 403.
10. Counter: original becomes `countered`, the new offer links back, and the thread shows on both.
11. An accepted trade sits 20 days without admin action. Still accepted, not expired.
12. Deadline day: an offer sent at 11:58 PM ET goes through, 12:01 AM ET is blocked.
13. Admin manual entry and void: both apply and reverse cleanly in one transaction.

## Acceptance criteria

- [ ] All 13 tests pass
- [ ] Every rule check goes through `trade_service.validate`, and every approval through `trade_service.approve`
- [ ] A 1-for-1 takes 5 taps or fewer to send and 1 to accept
- [ ] The impact panel shows on build, review, inbox and admin
- [ ] No git and no JSON files in the trade flow
- [ ] 2026 history visible in the new trade history page

## Future ideas

- Trading block: mark players as available, see who's shopping what
- **Slander** as a trade response (parked 10/27): reject with a roast, a Random button pulling from a league list, and an optional Post to #trade-talk toggle
- Slander Board: season leaderboard of slanders sent and received
- "Who won the trade?" league vote on approved trades
- Trade suggestions based on roster needs

## Open questions

1. ~~**Slander list.**~~ Answered 10/27: leave Slander out for now. Moved to Future ideas.
2. ~~**Comment visibility.**~~ Answered 9/30: comments stay private to the parties forever. No league-wide comment section; that's what Discord is for.
3. ~~**Offer expiry.**~~ Answered 9/30: pending offers expire after 7 days (down from 14). Accepted trades waiting on an admin never expire.
