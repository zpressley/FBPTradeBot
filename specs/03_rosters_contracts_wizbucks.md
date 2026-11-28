# Spec 03: Rosters, Contracts and WizBucks

**Status:** Draft for review
**Build target:** November 30, 2026 (Roadmap: "Trades, WizBucks and the auction run on the new database")
**Cutover:** December 15, 2026. The offseason is quiet (no trades until KAP, no auction until March), so this is the safest window to switch the source of truth.
**Depends on:** Spec 01 (schema and import)
**Needed by:** Spec 02 (auction), Spec 04 (draft), Spec 05 (trades), Spec 06 (PAD/KAP/APA)
**Read first:** `WARP.md`, `specs/01_neon_foundation.md` (Design rules), `docs/{Master} FBP Constitution 2026 (1).md` Articles I-III and V

## Goal

The database becomes the one place that knows who owns every player, on what contract, and how many WizBucks every team has. Every change to those three things happens through a small set of named actions, each in one transaction, each leaving a record. The Constitution's rules live in code and tables, not in admin memory.

## What's wrong today (summary)

Full findings: `specs/03_findings_2026.md`.

- **The WizBucks ledger can't rebuild balances.** Summing the ledger matches the wallet for only 1 of 12 teams (WAR: ledger says $170, wallet says $25). There are 23 balance-chain breaks where rollover caps were applied by hand-editing the wallet with no ledger row. APA and PTDA were booked as `admin_adjustment`. 15 rows have no ID and 3 IDs are duplicated.
- **Contracts live in three overlapping fields** (`contract_type`, `years_simple`, `status`) that disagree for 44 owned players. There's no IL tag field at all. 1,284 unowned players still carry "TC 1".
- **Reduce a Tier doesn't reduce anything** on the server. In 2026 three RaT players were advanced wrongly and fixed by hand.
- **Three release paths, three different results.** Admin release blanks the tier, KAP release sets TC-1, a Yahoo drop keeps the tier for the next owner.
- **Graduation** uses a made-up 80-game batter limit, never sees pitching appearances (always 0), runs by hand, and matches players by name.
- **No DC debut fee, no voiding, no roster limits, no penalties** in code. Managers can edit their own prospects' debut dates, and that field decides DC eligibility.
- **PAD trusts the price the browser sends.** Server-side pricing doesn't exist.

## Principles

1. **The ledger is the balance.** A team's WizBucks is `sum(amount)` of its ledger rows. Nothing stores a balance separately.
2. **One contract record per player at a time,** with history. Tier, cost, IL tag and how it was acquired are columns, not text to parse.
3. **Rules are data.** Prices, tier order, limits and caps live in tables keyed by season, so next year's changes are an edit, not a code change.
4. **Every change is a named action** (below) in one transaction, with who did it and why.

## What a contract tier means

A player's stored tier is **what he costs to keep at the next KAP**. Going into KAP 2027, a VC-2 player costs $55. At KAP the team pays that, and the tier then advances, so after KAP he shows FC-1, his 2028 cost. Draft and graduation assignments follow the same meaning: a round 1-3 Keeper Draft pick gets VC-1, so he costs $35 at the next KAP.

**The tier stays with the player, not the team** (decided 10/4 with Kyle). When a player is released or dropped, his tier stays on him, and whoever picks him up as a free agent inherits it. A player with no tier yet picks up at TC-1. Draft picks still get their tier from the round. Changing this would mean reworking the add/drop system and how the league plays, so it's a future discussion, not part of this build.

## Schema (migration `0003_rosters_contracts_wb.sql`)

### Rules tables (seeded by migration, edited by admins)

```sql
create table contract_tiers (            -- extends Spec 01's table
  tier        text primary key,           -- 'TC-R','TC-BC-1','TC-BC-2','TC-1','TC-2','VC-1','VC-2','FC-1','FC-2'
  family      text not null,              -- 'TC','VC','FC','P'
  sort_order  int  not null,
  keeper_cost int,
  next_tier   text references contract_tiers,   -- TC-R->TC-1, TC-BC-1->TC-BC-2, TC-BC-2->TC-1, TC-1->TC-2, TC-2->VC-1, VC-1->VC-2, VC-2->FC-1, FC-1->FC-2, FC-2->FC-2
  lower_tier  text references contract_tiers,   -- for Reduce a Tier: FC-2->FC-1, FC-1->VC-2, VC-2->VC-1; null for TC tiers and VC-1 (can't reduce into TC)
  il_discount int                               -- TC 10, VC 15, FC 35; null for TC-R and TC-BC
);

create table wb_prices (
  season int, item text, amount int not null,
  primary key (season, item)
);
-- items: 'dc','pc','bc','dc_to_pc','dc_to_bc','pc_to_bc','debut_fee',
--        'buyin_r1','buyin_r2','buyin_r3','rat','auction_min_ob','auction_min_raise'
-- 2027 seed: dc 5, pc 10, bc 20, dc_to_pc 5, dc_to_bc 15, pc_to_bc 10, debut_fee 15,
--            buyin_r1 55, buyin_r2 35, buyin_r3 10, rat 75, auction_min_ob 10, auction_min_raise 5

create table wb_installments (
  season int, installment text,            -- 'PAD','KAP','APA','PTDA'
  bracket text,                            -- 'championship','consolation','elimination','all'
  amount int not null,
  primary key (season, installment, bracket)
);

create table wb_rollover_caps (
  season int, from_phase text, to_phase text, cap int not null,
  primary key (season, from_phase, to_phase)
);
-- PAD->KAP 30, KAP->APA 100, prior season->next season 25 per installment (75 max)

create table roster_limits (
  season int primary key,
  active_max int, sp_max int, sp_max_with_swing int, pitcher_max int, batter_max int, catcher_min int,
  dc_slot_max int
);

create table prospect_limits (
  season int primary key,
  pa int, ip numeric, appearances int, age int     -- 350 / 100 / 30 / 26
);
```

### WizBucks

```sql
create table wb_ledger (                  -- replaces Spec 01's mirror table
  id          bigserial primary key,
  team_abbr   text not null references teams,
  amount      int  not null,              -- signed
  kind        text not null,              -- see list below
  phase       text not null check (phase in ('PAD','KAP','APA','IN_SEASON','OFFSEASON')),
  season      int  not null,
  upid        text references players,
  ref_type    text, ref_id text,          -- 'trade','auction','draft','pad','kap','penalty',...
  note        text,
  created_by  text not null,              -- manager, admin name, or 'system'
  created_at  timestamptz not null default now()
);
create view wb_balances as select team_abbr, sum(amount) as balance from wb_ledger group by team_abbr;
```

`kind` values: `opening_balance`, `installment`, `finish_reward`, `rollover_in`, `rollover_forfeit`, `prospect_contract`, `prospect_upgrade`, `debut_fee`, `draft_slot`, `buyin`, `buyin_refund`, `rat`, `keeper_salary`, `auction_win`, `trade_out`, `trade_in`, `penalty`, `penalty_credit`, `draft_award`, `admin_adjustment`, `reversal`.

- **Balance can't go negative** except through `penalty` (the Constitution allows penalties against future installments). The `wb_spend()` function checks `balance - amount >= 0` inside the transaction.
- **Phases come from the season calendar.** Rollover at a phase close is automatic: the closing job carries forward up to the cap (`rollover_in`) and forfeits the rest (`rollover_forfeit`), both as ledger rows. No more hand-editing the wallet.
- **2026 history** is imported into `wb_ledger_history` (read-only, for the team page). The live ledger starts with one `opening_balance` row per team equal to `data/wizbucks.json` on cutover day, confirmed by Zach.

### Contracts and roster

```sql
create table contracts (
  id            bigserial primary key,
  upid          text not null references players,
  team_abbr     text not null references teams,
  kind          text not null check (kind in ('keeper','prospect')),
  tier          text references contract_tiers,      -- keeper tier
  prospect_type text check (prospect_type in ('DC','PC','BC')),
  acquired_via  text not null,     -- 'keeper_draft','prospect_draft','auction','trade','pad','graduation','free_agent','import'
  acquired_round int,              -- draft round when acquired by draft
  il_tag        boolean not null default false,
  il_tag_season int,
  rat_count     int not null default 0,
  international_rookie boolean not null default false,
  valid_from    timestamptz not null default now(),
  valid_to      timestamptz,
  end_reason    text,            -- 'traded','released','graduated','voided','not_kept','season_rollover'
  check ((kind='keeper' and tier is not null and prospect_type is null) or (kind='prospect' and prospect_type is not null))
);
create unique index one_open_contract on contracts (upid) where valid_to is null;

alter table players add column carried_tier text references contract_tiers;  -- the tier a player carries, owned or not

create table roster_slots (
  upid       text primary key references players,
  slot       text not null check (slot in ('active','il','na','farm')),
  updated_at timestamptz not null default now()
);
```

`contracts` replaces Spec 01's `roster_assignments` (the migration moves the rows). A trade closes the contract with `end_reason = 'traded'` and opens an identical one for the new team, so tier, IL tag and RaT history travel with the player.

### Prospect draft slots

A draft slot is a **prepaid, empty prospect contract**: the manager pays the contract price at PAD (BC $20, DC $5), and the player is chosen at the Prospect Draft to fill it.

```sql
create table prospect_draft_slots (
  id          bigserial primary key,
  season      int not null,
  team_abbr   text not null references teams,
  slot_type   text not null check (slot_type in ('BC','DC')),   -- BC for rounds 1-2, DC for rounds 3+
  ledger_id   bigint not null references wb_ledger,              -- the purchase
  filled_upid text references players,                           -- set when a drafted player fills it
  filled_at   timestamptz
);
```

- Buying a slot is one `draft_slot` ledger row at the contract price. DC slots are capped at `roster_limits.dc_slot_max` (15).
- Drafting a player fills the team's next open slot of the right type and opens the contract **with no further charge**. A team can't make a pick without an open slot of that type (Spec 04 checks this in `make_pick`).

### Prospect tracking

```sql
create table prospect_status (
  upid         text primary key references players,
  mlb_debut    date,                  -- from MLB Stats API only, never manager-edited
  pa int, ip numeric, appearances int, age int,
  over_mlb_limits boolean, over_fbp_limits boolean,
  updated_at   timestamptz not null
);
```

Updated nightly from the MLB Stats API by MLB ID.

## Actions

Every action is one function in `league/actions.py`, runs in one transaction, writes `player_events` and (when money moves) `wb_ledger`, and returns a plain-language result. The website, Discord, admin tools and the PAD/KAP/draft/auction/trade services call these. Nothing else writes contracts or WizBucks.

| Action | Who | When | What it does |
|---|---|---|---|
| `assign_prospect_contract` | manager (PAD), system (draft, auction) | PAD; draft; auction | Opens a DC/PC/BC contract. Charges the price unless it was already paid (auction win, or a prepaid draft slot). |
| `buy_draft_slot` | manager | PAD | Charges the BC or DC contract price and creates an open slot for the Prospect Draft. |
| `upgrade_prospect` | manager | DC→PC any time. DC→BC and PC→BC only during post-draft finalization, which admins open and close. | At the price table. A drafted prospect not finalized in that window loses the down payment, which is why the window exists. |
| `pay_debut_fee` | manager | within 7 days of a DC prospect's MLB debut | $15. DC becomes PC. |
| `void_dc` | system | 7 days after debut with no fee | Ends the contract (`voided`). The player goes into the next Auction Portal. September debuts are exempt from the fee but flagged "can't be rostered unless purchased." |
| `graduate` | manager (optional), system (forced at the All-Star break for anyone over 350 PA, 100 IP, 30 appearances, or age 26) | in-season | Prospect contract ends, keeper contract opens: **TC-BC-1** if BC, **TC-R** otherwise, **TC-1** for international rookies. The player moves to an active or NA slot. |
| `retain_bc` | system | at PAD, from the official Nov 1 snapshot (Spec 03a) | BC prospects in the Top 100 keep BC at no cost. Others must repurchase or drop to their PAD choice. |
| `keep_player` | manager via KAP | KAP | Charges the stored tier's keeper cost (minus IL discount), then advances the contract to `next_tier` for the following season. |
| `il_tag` | manager requests, admin approves | KAP | One per contract family. Not allowed on TC-R or TC-BC. Discount applies, the tier still advances. Flags the player ineligible for the first 5 weeks. |
| `reduce_a_tier` | manager via KAP | KAP only | $75, tax-free. **Before** charging, drops the stored tier one step to `lower_tier`. The team pays the reduced cost, and the contract advances from the reduced tier. Can't be used on TC or reduce into TC (so not on VC-1). Repeatable in later seasons. |
| `release` | manager or admin | any time; automatic for non-kept at KAP close | Ends the team's contract (`released` or `not_kept`). The player keeps his `carried_tier`. |
| `acquire_free_agent` | manager | in-season | Opens a keeper contract at the player's `carried_tier`, or TC-1 if he has none. |
| `move_slot` | manager | any time | Moves a player between active, IL, NA and farm, checking roster limits. |
| `transfer` | trade service | on approval | Closes and reopens contracts for the new team. |
| `wb_spend` / `wb_credit` | services | | Low-level ledger writes with the balance check. |
| `penalty` | admin (confirms a detected violation) | in-season | Ledger row with the rule and dates. |
| `admin_adjust` | admin | any time | Ledger or contract fix with a required reason. |

## Automatic jobs

All run from the bot's scheduler (one scheduler, per Spec 02). Each is safe to run twice.

- **Nightly prospect update:** refresh `prospect_status` from the MLB Stats API. Flag new debuts and anyone crossing MLB or FBP limits.
- **Debut watch:** DM the owner when a DC prospect debuts ("Pay the $15 fee by Oct 9 or he goes to the auction"). Void at the deadline.
- **Graduation alerts:** DM when a prospect becomes eligible. At the All-Star break, graduate everyone over the limits and post the list.
- **Phase close:** at the end of PAD and KAP, run the rollover (carry up to the cap, forfeit the rest).
- **Installments:** credit PAD, KAP, APA and PTDA on their calendar dates by each team's bracket.
- **Roster check:** nightly in-season, compare every team to `roster_limits`. Violations go to an admin queue with the penalty amount pre-filled. Nothing is charged until an admin confirms.

## Team page

One page per team, the place managers go to see everything about their own team:

- **WizBucks:** current balance, money tied up (pending trade offers, leading auction bids), and the full ledger with filters by phase and kind.
- **Keepers:** tier, keeper cost this year, next year's tier and cost, IL tag, RaT count.
- **Prospects:** contract type, rank, level, and a progress bar to each graduation limit (PA, IP, appearances, age), with "eligible" and "must graduate at the break" badges.
- **Roster limits:** active, SP, pitchers, batters against the caps.
- **Draft picks and buy-ins.**
- **Alerts:** debut fee due, graduation eligible, roster over a limit, BC at risk of not being retained.
- **Actions** right on the page: upgrade a prospect, pay a debut fee, graduate, move slots.

## Cutover (December 15)

1. Freeze writes to the JSON files (maintenance banner on the site for an hour).
2. Final Spec 01 import.
3. Build `contracts` from the import, mapping `contract_type`, `years_simple` and `status`. Every conflict (the 44 known plus anything new) goes on a list for Zach to resolve before go-live. Nothing guesses.
4. Keep tiers on unowned players as their `carried_tier` (the 1,284 unowned TC-1s are correct under the inherit rule). Check the 267 unowned "Keeper Contract" rows have a tier.
5. Write one `opening_balance` per team from `data/wizbucks.json`. Zach confirms all 12 numbers.
6. Switch the bot and API to the actions in this spec. Remove the JSON writes.

## Tests (must pass before cutover)

1. Balance equals the sum of the ledger for every team, always (checked after every test).
2. Spending more than the balance fails with no ledger row.
2b. Buy 2 BC slots and 3 DC slots at PAD: $55 charged. Drafting into each charges nothing more. A round 3 pick with no open DC slot is rejected.
3. PAD→KAP rollover with $50 left: $30 carried, $20 forfeited, both recorded.
4. Keep a TC-2 player: charged $25, then shows VC-1. Keep an FC-2: charged $125, stays FC-2.
5. IL tag on a VC-2: charged $40 ($55 minus $15), then shows FC-1. IL tag on TC-R is rejected.
6. RaT on an FC-1: reduced to VC-2, charged $55 plus $75 RaT, then shows FC-1 again. RaT on an FC-2: reduced to FC-1, charged $85 plus $75, then shows FC-2. RaT on a TC-2 or VC-1 is rejected. (Matches 2026 for Vlad, Riley and Harper.)
7. DC prospect debuts. Fee paid within 7 days: becomes PC, $15 charged. Not paid: voided and in the next auction pool. September debut: no void.
8. Graduation: BC graduate gets TC-BC-1, non-BC gets TC-R, international rookie gets TC-1. TC-BC-1 → TC-BC-2 → TC-1 over two KAPs.
9. Release a VC-2, then another team picks him up as a free agent: he comes in at VC-2. A free agent with no tier comes in at TC-1. A Keeper Draft pick in round 2 gets VC-1 regardless of his carried tier.
10. Trade a player with an IL tag and RaT history: both travel with him.
11. Roster check flags 27 active and 9 SP (no swing pitcher), and charges nothing until an admin confirms.
12. Concurrent: a trade approval and an auction win spending the same team's last $20 at once. Exactly one succeeds.

## 2026 final standings (from Yahoo, 10/28)

These are the correct 2026 finishes. They replace what the website shows and get loaded into `team_seasons` for 2026 before PAD 2027.

| Finish | Team | Division | W-L-T | Bracket |
|---|---|---|---|---|
| 1 | HAM Hammers | Charlie Hustle | 243-154-23 | Championship |
| 2 | WIZ Whiz Kids | Colossus of Clout | 222-178-20 | Championship |
| 3 | LFB La Flama Blanca | Charlie Hustle | 246-157-17 | Championship |
| 4 | DMN The Damn Yankees | Stanky's Grave | 212-188-20 | Championship |
| 5 | SAD not much of a donkey | Colossus of Clout | 222-179-19 | Consolation |
| 6 | JEP Jepordizers | Colossus of Clout | 195-199-26 | Consolation |
| 7 | B2J Btwn2Jackies | Stanky's Grave | 202-196-22 | Consolation |
| 8 | TBB The Bluke Blokes | Charlie Hustle | 207-195-18 | Consolation |
| 9 | CFL Country Fried Lamb | Colossus of Clout | 176-228-16 | Elimination |
| 10 | WAR Weekend Warriors | Stanky's Grave | 165-234-21 | Elimination |
| 11 | RV Rick Vaughn | Stanky's Grave | 159-235-26 | Elimination |
| 12 | DRO Andromedans | Charlie Hustle | 147-253-20 | Elimination |

Playoff brackets (confirmed 10/28 from the Yahoo playoff page): **1-4 Championship, 5-8 Consolation, 9-12 Elimination.** SAD won the 5th place game, so SAD is the Consolation winner.

Finish rewards for 2027: SAD +$50 KAP (Consolation winner), JEP +$25 KAP (runner-up), CFL +$20 PAD (Elimination top), WAR +$10 PAD (Elimination runner-up).

**PAD follows the Constitution (decided 10/28):** each team's PAD installment comes from its playoff bracket on `team_seasons`: Championship $100, Consolation $120, Elimination $140. For 2027 that's HAM, WIZ, LFB, DMN $100; SAD, JEP, B2J, TBB $120; CFL, WAR, RV, DRO $140. Past payments are not used to infer the rules, and the 2025 results already in the import aren't used to second-guess them.

## Open questions

1. ~~**Rollover forfeits.**~~ Answered 10/3: the excess was removed by hand in 2026. The phase-close job now forfeits it automatically and records it.
2. ~~**Unstated prices.**~~ Answered 10/3: BC is $20. Draft slots are prepaid contracts (BC slot $20, DC slot $5) filled at the draft with no further charge.
2a. ~~**Unfilled draft slots.**~~ Answered 10/3: slots are picks. Every slot bought is filled, by autopick if the manager doesn't pick. No refunds.
3. ~~**When can a PC be upgraded to BC?**~~ Answered 10/27: only during post-draft finalization, an admin-run window for Blue Chipping players you drafted (otherwise you lose the funds). No BC upgrades in season.
4. ~~**RaT math.**~~ Answered 10/4 (Kyle): one step down from the stored tier is the rule. Acuña was not supposed to drop to VC-2; it was discussed but didn't happen. See the 2026 data note in `specs/03_findings_2026.md`, since the KAP record says otherwise.
5. ~~**Release.**~~ Answered 10/4 (Kyle): the contract does not die on a drop. The tier stays with the player and the next owner inherits it.
6. ~~**Stored tier meaning.**~~ Answered 10/3: the stored tier is the cost to keep at the next KAP, so on December 15 it's the 2027 cost. See "What a contract tier means."
7. ~~**PAD brackets.**~~ Answered 10/27-10/28: follow the Constitution. PAD comes from the playoff bracket (1-4 Championship, 5-8 Consolation, 9-12 Elimination). Ignore what was paid in past seasons. The 2026 finishes are corrected in "2026 final standings" above.
8. ~~**"Graduation penalty" -$10.**~~ Answered 10/27: admin error, but the totals are correct. Leave the four rows in history as they are. It isn't a rule and there's no ledger kind for it.
9. ~~**Age 26.**~~ Answered 10/27: forced. A prospect who turns 26 graduates at the All-Star break like the PA and IP limits.
