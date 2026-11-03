-- Spec 02: auction portal (specs/02_auction_portal.md, "Schema").
--
-- Depends only on teams and players from 0001, so it also runs on a fresh
-- database before 0003. Nothing is ever deleted from these tables: admin
-- removal sets removed_at, past weeks stay queryable forever.

create table auction_weeks (
  week_start      date primary key,          -- Monday, ET
  season          int not null,
  priority_order  text[] not null,           -- team abbrs, worst standings first
  status          text not null default 'open' check (status in ('open','resolved','skipped')),
  resolved_at     timestamptz,
  resolved_by     text,                      -- 'scheduler' or admin name
  announced_at    timestamptz,               -- Discord post sent
  summary         jsonb
);

create table auction_bids (
  bid_id        uuid primary key default gen_random_uuid(),
  week_start    date not null references auction_weeks,
  team_abbr     text not null references teams,
  upid          text not null references players,
  bid_type      text not null check (bid_type in ('OB','CB')),
  amount        int  not null check (amount > 0),
  placed_at     timestamptz not null default now(),
  placed_date_et date not null,
  source        text not null check (source in ('web','discord','admin')),
  removed_at    timestamptz,                 -- admin removal, never a hard delete
  removed_by    text,
  admin_note    text,
  seq           bigserial not null,          -- insertion order: breaks placed_at ties
  constraint removed_pair check ((removed_at is null) = (removed_by is null))
);
create unique index one_ob_per_team_week on auction_bids (week_start, team_abbr)
  where bid_type = 'OB' and removed_at is null;
create unique index one_ob_per_prospect_week on auction_bids (week_start, upid)
  where bid_type = 'OB' and removed_at is null;

create table auction_decisions (
  week_start  date not null references auction_weeks,
  upid        text not null references players,
  team_abbr   text not null references teams,
  decision    text not null check (decision in ('match','forfeit')),
  decided_at  timestamptz not null default now(),
  source      text not null,
  primary key (week_start, upid)             -- one decision per prospect, final
);

create table auction_results (
  week_start  date not null references auction_weeks,
  upid        text not null references players,
  team_abbr   text not null references teams,
  amount      int  not null,
  outcome     text not null check (outcome in ('OB_ONLY','OB_MATCH','CB_WIN')),
  dropped     jsonb,                          -- teams dropped for affordability, in order
  primary key (week_start, upid)
);

-- Backstop: nothing may be added to a week once it is resolved or skipped
-- (the service checks too; this holds even for a plain INSERT).
create function auction_week_must_be_open() returns trigger language plpgsql as $$
declare st text;
begin
  select status into st from auction_weeks where week_start = new.week_start;
  if st is distinct from 'open' then
    raise exception 'auction week % is %, so % cannot add to it', new.week_start, st, tg_table_name
      using errcode = 'P0001';
  end if;
  return new;
end $$;

create trigger auction_bids_week_open before insert on auction_bids
  for each row execute function auction_week_must_be_open();
create trigger auction_decisions_week_open before insert on auction_decisions
  for each row execute function auction_week_must_be_open();
