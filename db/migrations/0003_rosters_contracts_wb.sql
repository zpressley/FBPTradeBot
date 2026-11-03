-- Spec 03: rosters, contracts and WizBucks (specs/03_rosters_contracts_wizbucks.md).
--
-- (0002 is reserved for the auction spec.)
--
-- Shadow mode: roster_assignments keeps being reloaded from the JSON by the
-- daily import until the December 15 cutover. The tables created here that
-- hold live data (wb_ledger, contracts, roster_slots, prospect_draft_slots,
-- prospect_status) start EMPTY and are never touched by the import.
-- scripts/db/cutover_contracts.py fills contracts / opening balances on
-- cutover day.

-- ---------------------------------------------------------------- history rename
-- Spec 01's mirror of data/wizbucks_transactions.json becomes read-only 2026
-- history; the live ledger below takes the name wb_ledger.
alter table wb_ledger rename to wb_ledger_history;
alter view  wb_balances rename to wb_balances_history;
alter index wb_ledger_pkey        rename to wb_ledger_history_pkey;
alter index wb_ledger_team_ts_idx rename to wb_ledger_history_team_ts_idx;

-- ---------------------------------------------------------------- contract tiers
alter table contract_tiers
  add column next_tier   text references contract_tiers,
  add column lower_tier  text references contract_tiers,
  add column il_discount int;

update contract_tiers set next_tier = v.next_tier from (values
  ('TC-R','TC-1'), ('TC-BC-1','TC-BC-2'), ('TC-BC-2','TC-1'), ('TC-1','TC-2'),
  ('TC-2','VC-1'), ('VC-1','VC-2'), ('VC-2','FC-1'), ('FC-1','FC-2'), ('FC-2','FC-2')
) as v(tier, next_tier) where contract_tiers.tier = v.tier;

update contract_tiers set lower_tier = v.lower_tier from (values
  ('FC-2','FC-1'), ('FC-1','VC-2'), ('VC-2','VC-1')
) as v(tier, lower_tier) where contract_tiers.tier = v.tier;

update contract_tiers set il_discount = case family when 'TC' then 10 when 'VC' then 15 when 'FC' then 35 end
 where tier not in ('TC-R','TC-BC-1','TC-BC-2','P');

-- ---------------------------------------------------------------- rules tables
create table wb_prices (
  season int, item text, amount int not null check (amount >= 0),
  primary key (season, item)
);
insert into wb_prices (season, item, amount) values
  (2027,'dc',5), (2027,'pc',10), (2027,'bc',20), (2027,'dc_to_pc',5), (2027,'dc_to_bc',15),
  (2027,'pc_to_bc',10), (2027,'debut_fee',15), (2027,'buyin_r1',55), (2027,'buyin_r2',35),
  (2027,'buyin_r3',10), (2027,'rat',75), (2027,'auction_min_ob',10), (2027,'auction_min_raise',5),
  -- additions to the spec's item list: the Constitution's roster-violation fine (Art. V, section 06)
  (2027,'roster_violation',20), (2027,'roster_violation_daily',5);

create table wb_installments (
  season int, installment text,
  bracket text,
  amount int not null,
  primary key (season, installment, bracket)
);
insert into wb_installments (season, installment, bracket, amount) values
  (2027,'PAD','championship',100), (2027,'PAD','consolation',120), (2027,'PAD','elimination',140),
  (2027,'KAP','all',375),
  (2027,'APA','all',100),
  (2027,'PTDA','championship',15), (2027,'PTDA','consolation',25), (2027,'PTDA','elimination',35);

create table wb_rollover_caps (
  season int, from_phase text, to_phase text, cap int not null,
  primary key (season, from_phase, to_phase)
);
insert into wb_rollover_caps (season, from_phase, to_phase, cap) values
  (2027,'PAD','KAP',30), (2027,'KAP','APA',100),
  (2027,'PRIOR_SEASON','PAD',25), (2027,'PRIOR_SEASON','KAP',25), (2027,'PRIOR_SEASON','APA',25);

create table roster_limits (
  season int primary key,
  active_max int, sp_max int, sp_max_with_swing int, pitcher_max int, batter_max int, catcher_min int,
  dc_slot_max int
);
insert into roster_limits values (2027, 26, 8, 9, 14, 16, 1, 15);

create table prospect_limits (
  season int primary key,
  pa int, ip numeric, appearances int, age int,
  -- addition to the spec: draft rounds 1..N use BC slots, later rounds DC slots
  bc_slot_max_round int
);
insert into prospect_limits values (2027, 350, 100, 30, 26, 2);

-- Additions to the spec (flagged in the build report): data the Constitution
-- states but the spec has no table for, so the Python actions never hardcode it.
create table wb_finish_rewards (
  season int, installment text, final_rank int, amount int not null,   -- final_rank is the PRIOR season's finish
  primary key (season, installment, final_rank)
);
insert into wb_finish_rewards values
  (2027,'KAP',5,50), (2027,'KAP',6,25), (2027,'PAD',9,20), (2027,'PAD',10,10);

create table contract_entry_tiers (
  entry text primary key,
  tier  text not null references contract_tiers
);
insert into contract_entry_tiers values
  ('graduate_bc','TC-BC-1'), ('graduate','TC-R'), ('graduate_international','TC-1'),
  ('free_agent_default','TC-1'),
  ('keeper_draft_r1','VC-1'), ('keeper_draft_r2','VC-1'), ('keeper_draft_r3','VC-1'),
  ('keeper_draft_other','TC-1');

-- ---------------------------------------------------------------- team brackets
alter table team_seasons
  add column bracket text check (bracket in ('championship','consolation','elimination'));

-- ---------------------------------------------------------------- WizBucks ledger
create table wb_ledger (
  id          bigserial primary key,
  team_abbr   text not null references teams,
  amount      int  not null,
  kind        text not null check (kind in (
    'opening_balance','installment','finish_reward','rollover_in','rollover_forfeit',
    'prospect_contract','prospect_upgrade','debut_fee','draft_slot','buyin','buyin_refund','rat',
    'keeper_salary','auction_win','trade_out','trade_in','penalty','penalty_credit','draft_award',
    'admin_adjustment','reversal')),
  phase       text not null check (phase in ('PAD','KAP','APA','IN_SEASON','OFFSEASON')),
  season      int  not null,
  upid        text references players,
  ref_type    text, ref_id text,
  note        text,
  created_by  text not null,
  created_at  timestamptz not null default now()
);
create index wb_ledger_team_idx on wb_ledger (team_abbr, created_at);
create index wb_ledger_upid_idx on wb_ledger (upid) where upid is not null;
-- automatic jobs are safe to run twice: one installment/rollover row per team
create unique index wb_ledger_job_once on wb_ledger (team_abbr, kind, ref_type, ref_id)
  where ref_type in ('installment','rollover');

create view wb_balances as
  select team_abbr, sum(amount) as balance from wb_ledger group by team_abbr;

-- Credits are positive, debits negative; admin_adjustment and reversal may be either sign.
-- Three kinds may carry zero: opening_balance (a team that starts at $0), rollover_in (the
-- once-per-team marker row when nothing is carried) and keeper_salary (a keep that costs $0
-- after an IL discount leaves the marker that makes a second keep impossible).
alter table wb_ledger add constraint wb_ledger_amount_sign check (
  case kind
    when 'opening_balance' then amount >= 0
    when 'rollover_in'     then amount >= 0
    when 'keeper_salary'   then amount <= 0
    when 'installment' then amount > 0 when 'finish_reward' then amount > 0
    when 'buyin_refund' then amount > 0 when 'trade_in' then amount > 0
    when 'penalty_credit' then amount > 0 when 'draft_award' then amount > 0
    when 'prospect_contract' then amount < 0 when 'prospect_upgrade' then amount < 0
    when 'debut_fee' then amount < 0 when 'draft_slot' then amount < 0
    when 'buyin' then amount < 0 when 'rat' then amount < 0
    when 'auction_win' then amount < 0 when 'trade_out' then amount < 0
    when 'penalty' then amount < 0 when 'rollover_forfeit' then amount < 0
    else true            -- admin_adjustment, reversal
  end);

-- Append-only. The ledger is the balance, so a row is never edited or removed; a mistake is
-- fixed with a new admin_adjustment or reversal row. The only way around the trigger is a
-- session that sets  fbp.ledger_maintenance = 'on'  (test cleanup, one-off repair by the
-- database owner). Application code must never set it.
create function wb_ledger_append_only() returns trigger language plpgsql as $$
begin
  if current_setting('fbp.ledger_maintenance', true) = 'on' then
    if tg_op = 'DELETE' then return old; end if;
    return new;
  end if;
  raise exception 'wb_ledger is append-only: % is not allowed (add an adjustment or reversal row instead)', tg_op
    using errcode = 'P0001';
end $$;
create trigger wb_ledger_no_update_delete before update or delete on wb_ledger
  for each row execute function wb_ledger_append_only();
create trigger wb_ledger_no_truncate before truncate on wb_ledger
  for each statement execute function wb_ledger_append_only();

-- A team's balance may not end below zero unless the debit is a penalty (the Constitution
-- allows fines against future installments). Deferred: checked at commit, so it also stops a
-- plain INSERT that bypasses wb_spend(). It takes the same per-team lock as wb_spend().
create function wb_ledger_check_balance() returns trigger language plpgsql as $$
declare
  bal bigint;
begin
  if new.kind = 'penalty' or new.amount >= 0 then
    return null;
  end if;
  perform pg_advisory_xact_lock(hashtext('wb:' || new.team_abbr));
  select coalesce(sum(l.amount), 0) into bal from wb_ledger l where l.team_abbr = new.team_abbr;
  if bal < 0 then
    raise exception 'insufficient WizBucks: % would end at %', new.team_abbr, bal using errcode = 'P0001';
  end if;
  return null;
end $$;
create constraint trigger wb_ledger_balance_nonneg after insert on wb_ledger
  deferrable initially deferred for each row execute function wb_ledger_check_balance();

-- One BC retention charge per player per season (retain_bc is idempotent)
create unique index wb_ledger_bc_retain_once on wb_ledger (ref_id) where ref_type = 'bc_retain';
-- a ledger row can be reversed once
create unique index wb_ledger_reversal_once on wb_ledger (ref_id) where ref_type = 'reversal';

-- Low-level ledger writes. wb_spend takes amount > 0 and records -amount; it
-- refuses to take the balance below zero except for kind = 'penalty'.
create function wb_spend(team_abbr text, amount int, kind text, phase text, season int,
                         upid text, ref_type text, ref_id text, note text, created_by text)
returns bigint language plpgsql as $$
declare
  bal int;
  new_id bigint;
begin
  if amount is null or amount <= 0 then
    raise exception 'wb_spend amount must be positive, got %', amount;
  end if;
  perform pg_advisory_xact_lock(hashtext('wb:' || wb_spend.team_abbr));
  select coalesce(sum(l.amount), 0) into bal from wb_ledger l where l.team_abbr = wb_spend.team_abbr;
  if wb_spend.kind <> 'penalty' and bal - wb_spend.amount < 0 then
    raise exception 'insufficient WizBucks: balance %, needed %', bal, wb_spend.amount
      using errcode = 'P0001';
  end if;
  insert into wb_ledger (team_abbr, amount, kind, phase, season, upid, ref_type, ref_id, note, created_by)
  values (wb_spend.team_abbr, -wb_spend.amount, wb_spend.kind, wb_spend.phase, wb_spend.season,
          wb_spend.upid, wb_spend.ref_type, wb_spend.ref_id, wb_spend.note, wb_spend.created_by)
  returning id into new_id;
  return new_id;
end $$;

create function wb_credit(team_abbr text, amount int, kind text, phase text, season int,
                          upid text, ref_type text, ref_id text, note text, created_by text)
returns bigint language plpgsql as $$
declare
  new_id bigint;
begin
  if amount is null or amount < 0 then
    raise exception 'wb_credit amount must not be negative, got %', amount;
  end if;
  perform pg_advisory_xact_lock(hashtext('wb:' || wb_credit.team_abbr));
  insert into wb_ledger (team_abbr, amount, kind, phase, season, upid, ref_type, ref_id, note, created_by)
  values (wb_credit.team_abbr, wb_credit.amount, wb_credit.kind, wb_credit.phase, wb_credit.season,
          wb_credit.upid, wb_credit.ref_type, wb_credit.ref_id, wb_credit.note, wb_credit.created_by)
  returning id into new_id;
  return new_id;
end $$;

-- ---------------------------------------------------------------- contracts and roster
-- contracts is created EMPTY. roster_assignments stays the mirror until the
-- Dec 15 cutover (scripts/db/cutover_contracts.py builds contracts from it).
create table contracts (
  id            bigserial primary key,
  upid          text not null references players,
  team_abbr     text not null references teams,
  kind          text not null check (kind in ('keeper','prospect')),
  tier          text references contract_tiers,
  prospect_type text check (prospect_type in ('DC','PC','BC')),
  acquired_via  text not null check (acquired_via in
    ('keeper_draft','prospect_draft','auction','trade','pad','graduation','free_agent','import')),
  acquired_round int,
  il_tag        boolean not null default false,
  il_tag_season int,
  il_tag_family text check (il_tag_family in ('TC','VC','FC')),   -- addition: family at tag time (the tier advances)
  il_tag_team   text references teams,   -- addition: the team whose yearly allowance the tag used (it travels in a trade)
  il_ineligible_until date,              -- addition: KAP end + 5 weeks; the IL-tagged player is ineligible until then
  bc_decision_season int,                -- addition: PAD season in which retain_bc decided this BC
  bc_decision   text check (bc_decision in ('retained','repurchase','DC','PC')),
  rat_count     int not null default 0 check (rat_count >= 0),
  international_rookie boolean not null default false,
  valid_from    timestamptz not null default now(),
  valid_to      timestamptz,
  end_reason    text check (end_reason in ('traded','released','graduated','voided','not_kept','season_rollover')),
  constraint contracts_open_iff_no_end_reason check ((valid_to is null) = (end_reason is null)),
  constraint contracts_valid_range check (valid_to is null or valid_to >= valid_from),
  constraint contracts_il_tag_fields check (
    il_tag = (il_tag_season is not null) and il_tag = (il_tag_family is not null) and il_tag = (il_tag_team is not null)),
  constraint contracts_bc_decision_fields check ((bc_decision is null) = (bc_decision_season is null)),
  check ((kind='keeper' and tier is not null and prospect_type is null) or (kind='prospect' and prospect_type is not null)),
  -- addition: 'P' is the old prospect marker, not a keeper tier
  constraint contracts_tier_not_p check (tier is distinct from 'P')
);
create unique index one_open_contract on contracts (upid) where valid_to is null;
create index contracts_team_idx on contracts (team_abbr) where valid_to is null;
-- the IL-tag allowance is counted over open AND closed contracts
create index contracts_il_tag_idx on contracts (il_tag_team, il_tag_season, il_tag_family) where il_tag;

-- 'P' is the old prospect marker, never a tier a player carries
alter table players add column carried_tier text references contract_tiers
  constraint players_carried_tier_not_p check (carried_tier is distinct from 'P');

create table roster_slots (
  upid       text primary key references players,
  slot       text not null check (slot in ('active','il','na','farm')),
  updated_at timestamptz not null default now()
);

create table prospect_draft_slots (
  id          bigserial primary key,
  season      int not null,
  team_abbr   text not null references teams,
  slot_type   text not null check (slot_type in ('BC','DC')),
  ledger_id   bigint not null unique references wb_ledger,
  filled_upid text unique references players,
  filled_at   timestamptz,
  constraint prospect_draft_slots_filled_together check ((filled_upid is null) = (filled_at is null))
);
create index prospect_draft_slots_open_idx on prospect_draft_slots (season, team_abbr, slot_type)
  where filled_upid is null;

create table prospect_status (
  upid         text primary key references players,
  mlb_debut    date,
  pa int, ip numeric, appearances int, age int,
  over_mlb_limits boolean, over_fbp_limits boolean,
  updated_at   timestamptz not null
);

-- ---------------------------------------------------------------- phase windows
-- Which actions are allowed is decided by these rows, never by an argument the caller passes.
-- An admin opens and closes a window (league.actions.open_window / close_window).
create table league_windows (
  season    int  not null,
  window_name text not null check (window_name in ('PAD','KAP','BC_FINALIZATION','TRADE')),  -- "window" is reserved in SQL
  opens_at  timestamptz not null,
  closes_at timestamptz not null,
  opened_by text not null,
  primary key (season, window_name),
  check (closes_at > opens_at)
);
