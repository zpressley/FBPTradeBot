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
  admin_note    text
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
