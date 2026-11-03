-- Schema v1: shadow-mode mirror of data/*.json (Spec 01) plus the
-- top100_snapshots.official flag that Spec 03a needs.
--
-- Conventions (specs/01_neon_foundation.md, section 3):
--   * upid is the player key everywhere, stored as text.
--   * team key is the abbreviation; full names only live in teams.name.
--   * timestamps are timestamptz; naive JSON timestamps are America/New_York.
--   * every imported table carries imported_at and, where the JSON is
--     messy, a raw jsonb column so nothing is lost.
--
-- Constraints deliberately left loose until the first clean import
-- (spec: "Use a DB check constraint on contract_tier only after the first
-- clean import"): roster_assignments.contract_tier has no FK yet, and
-- players.mlb_id is not unique because combined_players.json holds
-- duplicate MLB IDs today (reconcile reports them).

-- ---------------------------------------------------------------- reference

create table teams (
  abbr          text primary key,
  name          text not null unique,
  discord_id    text,
  yahoo_team_id text,
  division      text,
  role          text,
  -- true for the 12 teams in config/managers.json; false for teams that
  -- only appear in data/historical/*/team_mappings_*.json
  active        boolean not null default true,
  imported_at   timestamptz not null default now()
);

create table team_name_aliases (
  alias       text primary key,
  team_abbr   text not null references teams,
  imported_at timestamptz not null default now()
);

create table seasons (
  year        int primary key,
  dates       jsonb,
  imported_at timestamptz not null default now()
);

create table team_seasons (
  team_abbr    text not null references teams,
  season       int  not null,
  final_rank   int,
  standing     int,
  division     text,
  kap_rollover int,
  raw          jsonb,
  imported_at  timestamptz not null default now(),
  primary key (team_abbr, season)
);

-- ------------------------------------------------------------------ players

create table players (
  upid                 text primary key,
  name                 text not null,
  mlb_id               int,
  yahoo_id             text,
  bbref_id             text,
  fangraphs_id         text,
  birth_date           date,
  bats                 text,
  throws               text,
  height               text,
  weight               int,
  mlb_team             text,
  positions            text,
  mlb_primary_position text,
  player_type          text,
  fypd                 boolean,
  rank                 int,
  raw                  jsonb,
  imported_at          timestamptz not null default now()
);
create index players_mlb_id_idx on players (mlb_id);
create index players_yahoo_id_idx on players (yahoo_id);

create table player_aliases (
  alias_normalized text not null,
  upid             text not null references players,
  imported_at      timestamptz not null default now(),
  primary key (alias_normalized, upid)
);

-- ---------------------------------------------------- ownership and contracts

create table contract_tiers (
  tier        text primary key,
  family      text not null,
  sort_order  int  not null,
  keeper_cost int
);

insert into contract_tiers (tier, family, sort_order, keeper_cost) values
  ('FC-2',    'FC', 0, 125),
  ('FC-1',    'FC', 1, 85),
  ('VC-2',    'VC', 2, 55),
  ('VC-1',    'VC', 3, 35),
  ('TC-2',    'TC', 4, 25),
  ('TC-1',    'TC', 5, 15),
  ('TC-R',    'TC', 6, 5),
  ('TC-BC-1', 'TC', 6, 5),
  ('TC-BC-2', 'TC', 6, 5),
  ('P',       'P',  7, null);

create table roster_assignments (
  id            bigserial primary key,
  upid          text not null references players,
  -- null while a manager/FBP_Team disagreement is unresolved (see reconcile)
  team_abbr     text references teams,
  contract_type text,
  contract_tier text,
  status_raw    text,
  valid_from    timestamptz not null,
  valid_to      timestamptz,
  source        text not null,
  imported_at   timestamptz not null default now()
);
-- one open assignment per player = the current roster
create unique index roster_assignments_one_open
  on roster_assignments (upid) where valid_to is null;
create index roster_assignments_team_idx on roster_assignments (team_abbr) where valid_to is null;

-- -------------------------------------------------------------------- money

create table wb_ledger (
  txn_id         text primary key,
  ts             timestamptz not null,
  team_abbr      text not null references teams,
  amount         int  not null,
  balance_before int,
  balance_after  int,
  txn_type       text not null,
  description    text,
  related_upid   text,
  season         int,
  metadata       jsonb,
  -- the original txn_id from the JSON when the importer had to generate or
  -- de-duplicate one; null when txn_id is the JSON's own
  source_txn_id  text,
  raw            jsonb,
  imported_at    timestamptz not null default now()
);
create index wb_ledger_team_ts_idx on wb_ledger (team_abbr, ts);

-- current balance per team = balance_after of the latest ledger row
create view wb_balances as
  select distinct on (team_abbr)
         team_abbr, balance_after as balance, ts as as_of
    from wb_ledger
   order by team_abbr, ts desc, txn_id desc;

-- ------------------------------------------------------------------- trades

create table trades (
  trade_id            text primary key,
  status              text,
  initiator_team      text,
  teams               text[],
  created_at          timestamptz,
  expires_at          timestamptz,
  manager_approved_at timestamptz,
  processed_at        timestamptz,
  data_applied_at     timestamptz,
  admin_decision_by   text,
  rejection_reason    text,
  rejected_by         text,
  raw                 jsonb,
  imported_at         timestamptz not null default now()
);

create table trade_transfers (
  id          bigserial primary key,
  trade_id    text not null references trades,
  type        text not null,
  upid        text,
  from_team   text,
  to_team     text,
  amount      int,
  raw         jsonb,
  imported_at timestamptz not null default now()
);
create index trade_transfers_trade_idx on trade_transfers (trade_id);

-- ------------------------------------------------------------ event history

create table player_events (
  id           text primary key,
  season       int,
  ts           timestamptz,
  update_type  text,
  event        text,
  source       text,
  admin        text,
  upid         text,
  player_name  text,
  owner_raw    text,
  owner_abbr   text,
  contract     text,
  status_raw   text,
  -- 'transactions_history' or 'player_log'
  source_file  text not null,
  id_generated boolean not null default false,
  raw          jsonb,
  imported_at  timestamptz not null default now()
);
create index player_events_upid_idx on player_events (upid);
create index player_events_ts_idx on player_events (ts);

-- ------------------------------------------------------ drafts and prospects

create table draft_picks (
  draft       text not null,
  season      int  not null,
  round       int  not null,
  pick        int  not null,
  team_abbr   text,
  round_type  text,
  result      jsonb,
  raw         jsonb,
  imported_at timestamptz not null default now(),
  primary key (draft, season, round, pick)
);

create table top100_snapshots (
  snapshot_date date not null,
  rank          int  not null,
  upid          text,
  mlb_id        int,
  name          text,
  org           text,
  position      text,
  level         text,
  eta           text,
  raw           jsonb,
  -- Spec 03a: the November 1 list is frozen. Official rows can only be
  -- changed through an admin override that records a reason.
  official      boolean not null default false,
  source        text,
  imported_at   timestamptz not null default now(),
  primary key (snapshot_date, rank)
);

create table player_season_stats (
  upid               text not null,
  season             int  not null,
  level              text not null,
  stat_type          text not null,
  source             text not null,
  mlb_id             int,
  mlb_team           text,
  games              int,
  plate_appearances  int,
  at_bats            int,
  runs               int,
  hits               int,
  doubles            int,
  triples            int,
  home_runs          int,
  rbi                int,
  stolen_bases       int,
  caught_stealing    int,
  base_on_balls      int,
  strike_outs        int,
  avg                numeric,
  obp                numeric,
  slg                numeric,
  ops                numeric,
  innings_pitched    numeric,
  era                numeric,
  whip               numeric,
  wins               int,
  losses             int,
  saves              int,
  holds              int,
  games_started      int,
  earned_runs        int,
  raw                jsonb,
  imported_at        timestamptz not null default now(),
  primary key (upid, season, level, stat_type, source)
);

-- ----------------------------------------------------------- catch-all state

create table app_state (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);

create table history_raw (
  source_file text not null,
  row_num     int  not null,
  row         jsonb not null,
  imported_at timestamptz not null default now(),
  primary key (source_file, row_num)
);

-- per-run import bookkeeping (row counts, skips, flags), one row per import
create table import_runs (
  id          bigserial primary key,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  report      jsonb
);
