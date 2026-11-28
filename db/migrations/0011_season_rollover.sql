-- 0011_season_rollover.sql
-- The season-boundary rollover (Constitution Art. I s02.2 and Art. IV s02.3.1).
--
-- Leftover WizBucks carry into the new season up to $25 into each of PAD, KAP and APA
-- ($75 at most), and the manager chooses the split. 0003 seeded the three PRIOR_SEASON
-- caps in wb_rollover_caps but nothing could record a manager's choice or apply it.
--
--   wb_rollover_declarations   what each team asked to carry into each installment
--   ledger kind rollover_hold  the KAP and APA shares leave the balance when the season
--                              opens, so they can't be spent during PAD or counted in the
--                              PAD-to-KAP cap, and come back as rollover_in with their
--                              installment (league.actions.credit_installment)

create table wb_rollover_declarations (
  season      int  not null,
  team_abbr   text not null references teams,
  to_phase    text not null check (to_phase in ('PAD', 'KAP', 'APA')),
  amount      int  not null check (amount >= 0),
  declared_by text not null,
  declared_at timestamptz not null default now(),
  primary key (season, team_abbr, to_phase)
);

create function wb_rollover_declaration_within_cap() returns trigger language plpgsql as $$
declare
  limit_amount int;
begin
  select c.cap into limit_amount from wb_rollover_caps c
   where c.season = new.season and c.from_phase = 'PRIOR_SEASON' and c.to_phase = new.to_phase;
  if limit_amount is null then
    raise exception 'no season rollover cap into % for %', new.to_phase, new.season;
  end if;
  if new.amount > limit_amount then
    raise exception 'at most $% can roll over into % (asked for $%)', limit_amount, new.to_phase, new.amount;
  end if;
  return new;
end $$;

create trigger wb_rollover_declaration_within_cap before insert or update on wb_rollover_declarations
  for each row execute function wb_rollover_declaration_within_cap();

alter table wb_ledger drop constraint wb_ledger_kind_check;
alter table wb_ledger add constraint wb_ledger_kind_check check (kind in (
  'opening_balance','installment','finish_reward','rollover_in','rollover_forfeit','rollover_hold',
  'prospect_contract','prospect_upgrade','debut_fee','draft_slot','buyin','buyin_refund','rat',
  'keeper_salary','auction_win','trade_out','trade_in','penalty','penalty_credit','draft_award',
  'admin_adjustment','reversal'));

alter table wb_ledger drop constraint wb_ledger_amount_sign;
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
    when 'rollover_hold' then amount < 0
    else true            -- admin_adjustment, reversal
  end);
