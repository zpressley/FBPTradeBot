-- 0009_two_way.sql
-- Two-way players (Constitution Art. V s03 Clause 1; Spec 07 "Two-way players").
--
-- A two-way player is two players rows, a batter half and a pitcher half, linked by
-- raw->>'2wayplayer' (each row names the other's upid). Spec 07: "two linked roster entries
-- ... under one contract. Each version uses its own roster spot (2 of the 26) ... Traded,
-- released and kept as a package."
--
-- What this adds:
--   1. players.two_way_twin: the link as a real column with a foreign key, so it is not
--      a convention buried in JSON. Generated from raw, so the fact still lives in one place.
--   2. One contract: each half keeps its own contracts row (every ownership query keys on
--      upid), and a deferred constraint makes the two rows agree on team and terms at commit.
--      Owning one half, or the halves' tiers drifting apart, cannot be committed.
--   3. Options: slot 'optioned' (off the 26, still owned), for two-way halves only, and
--      two_way_options with the per-contract, per-season maximum enforced here. A trade
--      opens new contracts, which is what resets the receiving team's options.

-- ------------------------------------------------------------------ the link
alter table players add column two_way_twin text
  generated always as (nullif(raw ->> '2wayplayer', '')) stored;

alter table players
  add constraint players_two_way_twin_fkey foreign key (two_way_twin) references players (upid)
    deferrable initially deferred,
  add constraint players_two_way_not_self check (two_way_twin is distinct from upid);

create unique index players_two_way_twin_once on players (two_way_twin) where two_way_twin is not null;

-- Deferred, so both rows of a pair can be written in one transaction. A deferred trigger still sees NEW as it
-- was when the statement ran, so the row is read again: what matters is how the pair stands at commit.
create function players_two_way_symmetric() returns trigger language plpgsql as $$
declare
  twin text;
begin
  select p.two_way_twin into twin from players p where p.upid = new.upid;
  if not found then
    return null;
  end if;
  if twin is not null
     and (select p.two_way_twin from players p where p.upid = twin) is distinct from new.upid then
    raise exception 'two-way link is one-sided: % names % as its other half, but % does not name it back',
      new.upid, twin, twin;
  end if;
  if exists (select 1 from players p where p.two_way_twin = new.upid and p.upid is distinct from twin) then
    raise exception 'two-way link is one-sided: another player names % as its other half, but % does not name it back',
      new.upid, new.upid;
  end if;
  return null;
end $$;

create constraint trigger players_two_way_symmetric after insert or update of raw on players
  deferrable initially deferred for each row execute function players_two_way_symmetric();

-- -------------------------------------------------------------- one contract
create function contracts_two_way_agree() returns trigger language plpgsql as $$
declare
  twin text;
  a contracts;
  b contracts;
begin
  select p.two_way_twin into twin from players p where p.upid = new.upid;
  if twin is null then
    return null;
  end if;
  select * into a from contracts c where c.upid = new.upid and c.valid_to is null;
  select * into b from contracts c where c.upid = twin and c.valid_to is null;
  if (a.id is null) <> (b.id is null) then
    raise exception 'two-way player: % and % are one contract and must be owned together', new.upid, twin;
  end if;
  if a.id is not null and
     (a.team_abbr, a.kind, a.tier, a.prospect_type, a.il_tag, a.il_tag_season, a.il_tag_family, a.il_tag_team,
      a.il_ineligible_until, a.rat_count)
     is distinct from
     (b.team_abbr, b.kind, b.tier, b.prospect_type, b.il_tag, b.il_tag_season, b.il_tag_family, b.il_tag_team,
      b.il_ineligible_until, b.rat_count) then
    raise exception 'two-way player: % and % are one contract, but their team or terms differ', new.upid, twin;
  end if;
  return null;
end $$;

create constraint trigger contracts_two_way_agree after insert or update on contracts
  deferrable initially deferred for each row execute function contracts_two_way_agree();

-- ------------------------------------------------------------------- options
alter table roster_slots drop constraint roster_slots_slot_check;
alter table roster_slots add constraint roster_slots_slot_check
  check (slot in ('active', 'il', 'na', 'farm', 'optioned'));

create function roster_slots_optioned_two_way_only() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from players p where p.upid = new.upid and p.two_way_twin is not null) then
    raise exception 'only one half of a two-way player can be optioned (%)', new.upid;
  end if;
  return new;
end $$;

create trigger roster_slots_optioned_two_way_only before insert or update of slot on roster_slots
  for each row when (new.slot = 'optioned') execute function roster_slots_optioned_two_way_only();

-- "the ability to option each version of the two-way player twice throughout the year"
alter table roster_limits add column two_way_options_max int not null default 2 check (two_way_options_max >= 0);

create table two_way_options (
  id          bigserial primary key,
  contract_id bigint not null references contracts,
  upid        text   not null references players,
  season      int    not null,
  optioned_at timestamptz not null,
  created_by  text   not null
);
create index two_way_options_contract_idx on two_way_options (contract_id, season);

create function two_way_options_within_max() returns trigger language plpgsql as $$
declare
  cap  int;
  used int;
begin
  select l.two_way_options_max into cap from roster_limits l where l.season = new.season;
  if cap is null then
    raise exception 'no roster limits for season %', new.season;
  end if;
  select count(*) into used from two_way_options o where o.contract_id = new.contract_id and o.season = new.season;
  if used >= cap then
    raise exception 'no two-way options left for % in % (max %)', new.upid, new.season, cap;
  end if;
  return new;
end $$;

create trigger two_way_options_within_max before insert on two_way_options
  for each row execute function two_way_options_within_max();
