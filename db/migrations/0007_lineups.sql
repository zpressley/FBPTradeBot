-- Spec 07: team page, lineups and position eligibility (specs/07_team_page_lineups.md).
--
-- (0004-0006 are reserved for other specs.) Depends on 0001 (teams, players) and
-- 0003 (roster_slots, contracts). Everything here starts empty except the slot setup and
-- the settings rows; lineups are written only by lineups/service.py.

-- ---------------------------------------------------------------- settings
-- Small key/value rows for numbers the rules mention but no table owns.
create table league_settings (
  key   text primary key,
  value jsonb not null
);
insert into league_settings (key, value) values
  ('eligibility_games', '5'),   -- Spec 07: 5 games (or starts / relief appearances) earn a position
  ('lineup_future_days', '7');  -- Spec 07: managers can set lineups up to 7 days ahead
-- (the IL and NA limits, 5 and 2, live in roster_limits.il_max / na_max, Spec 03)

-- MLB team abbreviations differ between sources (players.mlb_team, the schedule feed). Every team
-- name either source may carry maps to one canonical abbreviation; lock and optimize resolve through it.
create table mlb_team_aliases (
  alias text primary key,
  abbr  text not null
);
insert into mlb_team_aliases (alias, abbr)
select a, a from unnest(array['ATH','AZ','ATL','BAL','BOS','CWS','CHC','CIN','CLE','COL','DET','HOU','KC','LAA',
                              'LAD','MIA','MIL','MIN','NYM','NYY','PHI','PIT','SD','SEA','SF','STL','TB','TEX',
                              'TOR','WSH']) a;
insert into mlb_team_aliases (alias, abbr) values
  ('OAK','ATH'), ('ARI','AZ'), ('WSN','WSH'), ('TBR','TB'), ('KCR','KC'),
  ('SDP','SD'), ('SFG','SF'), ('CHW','CWS'), ('LA','LAD');

-- The canonical abbreviation for any alias; text the table does not know passes through unchanged.
create function mlb_abbr(t text) returns text language sql stable as $$
  select coalesce((select abbr from mlb_team_aliases where alias = t), t)
$$;

-- ---------------------------------------------------------------- slot setup
-- Position codes used by eligibility and slot 'accepts'. DH is an addition to the spec's list:
-- a batter with no fielding position (Ohtani's batter row, a pure DH) needs something for UTIL to accept.
create table lineup_slot_types (
  season      int,
  code        text,
  count       int not null check (count > 0),
  accepts     text[] not null,
  is_starting boolean not null,
  sort_order  int not null,
  primary key (season, code),
  constraint lineup_slot_code check (code in ('C','1B','2B','SS','3B','CF','OF','UTIL','SP','RP','P','BN')),
  constraint lineup_slot_accepts check (
    cardinality(accepts) > 0
    and accepts <@ array['C','1B','2B','3B','SS','CF','OF','DH','SP','RP']::text[]),
  constraint lineup_slot_bn_not_starting check ((code = 'BN') = (not is_starting))
);
insert into lineup_slot_types (season, code, count, accepts, is_starting, sort_order) values
  (2027, 'C',    1, '{C}',                                       true,  1),
  (2027, '1B',   1, '{1B}',                                      true,  2),
  (2027, '2B',   1, '{2B}',                                      true,  3),
  (2027, 'SS',   1, '{SS}',                                      true,  4),
  (2027, '3B',   1, '{3B}',                                      true,  5),
  (2027, 'CF',   1, '{CF}',                                      true,  6),
  (2027, 'OF',   2, '{OF,CF}',                                   true,  7),
  (2027, 'UTIL', 1, '{C,1B,2B,3B,SS,CF,OF,DH}',                  true,  8),
  (2027, 'SP',   1, '{SP}',                                      true,  9),
  (2027, 'RP',   1, '{RP}',                                      true, 10),
  (2027, 'P',    4, '{SP,RP}',                                   true, 11),
  (2027, 'BN',  11, '{C,1B,2B,3B,SS,CF,OF,DH,SP,RP}',            false, 12);

-- ---------------------------------------------------------------- eligibility
create table position_eligibility (
  upid      text not null references players,
  season    int  not null,
  position  text not null check (position in ('C','1B','2B','3B','SS','CF','OF','DH','SP','RP')),
  source    text not null check (source in ('last_season','this_season','primary','admin')),
  games     int,
  earned_on date,                 -- first lineup day he can use it (null = from the start of the season)
  primary key (upid, season, position)
);

-- ---------------------------------------------------------------- MLB feeds
create table mlb_games (
  game_pk    bigint primary key,
  game_date  date not null,
  start_time timestamptz not null,
  home_team  text,                -- MLB team abbreviation, the same text as players.mlb_team
  away_team  text,
  status     text not null check (status in ('scheduled','live','final','postponed')),
  probable_home_sp int,           -- MLB IDs
  probable_away_sp int
);
create index mlb_games_date_idx on mlb_games (game_date);

-- Where MLB lists each player (from the Stats API), for the IL / NA move conditions and the IL / NA badges.
-- Chosen over a prospect_status column: prospect_status only has prospects, and this needs every owned player.
create table mlb_roster_status (
  upid             text primary key references players,
  status           text not null check (status in ('active','il','minors','other')),
  mlb_status_code  text,          -- raw Stats API roster status code, e.g. 'A', 'D15', 'MIN'
  primary_position text,          -- from the Stats API, for rookies with no MLB games
  updated_at       timestamptz not null
);

-- ---------------------------------------------------------------- lineups
create table lineups (
  team_abbr  text not null references teams,
  game_date  date not null,                       -- ET calendar day
  season     int generated always as (extract(year from game_date)::int) stored,
  slot_code  text not null,
  slot_index int  not null check (slot_index >= 1),
  upid       text references players,             -- null = empty slot
  locked_at  timestamptz,                         -- when this player's game started
  set_by     text not null,                       -- manager, 'carryover', 'optimize', 'admin'
  primary key (team_abbr, game_date, slot_code, slot_index),
  unique (team_abbr, game_date, upid),
  foreign key (season, slot_code) references lineup_slot_types (season, code),
  constraint lineups_locked_needs_player check (locked_at is null or upid is not null)
);
create index lineups_upid_idx on lineups (upid) where upid is not null;

-- slot_index may not exceed the slot's count (a foreign key can't say that)
create function lineups_slot_index_ok() returns trigger language plpgsql as $$
begin
  if new.slot_index > (select count from lineup_slot_types where season = new.season and code = new.slot_code) then
    raise exception 'slot % has no position %', new.slot_code, new.slot_index using errcode = '23514';
  end if;
  return new;
end $$;
create trigger lineups_slot_index_ok before insert or update on lineups
  for each row execute function lineups_slot_index_ok();

-- Optimistic concurrency: one row per team per day, bumped by every lineup write
-- (a save, a carryover, a lock kicking in). A save must carry the version it started from.
create table lineup_versions (
  team_abbr  text not null references teams,
  game_date  date not null,
  version    int  not null check (version >= 1),
  updated_at timestamptz not null default now(),
  primary key (team_abbr, game_date)
);
