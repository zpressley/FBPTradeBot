-- Spec 04: draft room (specs/04_draft_room.md, "Schema").
--
-- Differences from the spec text (each one is flagged in the build report):
--   * The spec says "rename draft_picks to draft_slots and add columns". Until the cutover the
--     daily import truncates and reloads Spec 01's draft_picks mirror from data/draft_order_2026.json,
--     so that table is left alone. The spec's tables are created new:
--         draft_slots       the order (the spec's renamed table)
--         draft_picks_live  the spec's "draft_picks" (actual picks). It keeps the name
--                           draft_picks_live because the mirror owns "draft_picks" until cutover;
--                           rename the mirror away and this one to draft_picks then.
--   * draft_slots.makeup_seq and draft_picks_live.seq are additions (void makeup order, undo order).
--   * draft_award_amounts is an addition: the speed-award prize money as data, not code.
--   * a partial unique index on drafts and one on wb_ledger (speed awards pay once) are additions.
--
-- Search needs both extensions (both ship with Postgres 16 and are available on Neon).
create extension if not exists unaccent;
create extension if not exists pg_trgm;

create table drafts (
  draft_id         text primary key,            -- e.g. 'prospect-2027'
  season           int  not null,
  draft_type       text not null check (draft_type in ('prospect','keeper')),
  status           text not null default 'setup'
                   check (status in ('setup','pool_check','live','paused','complete')),
  clock_seconds    int  not null default 240 check (clock_seconds > 0),
  forklift_seconds int  not null default 10 check (forklift_seconds > 0),
  starts_at        timestamptz,
  is_test          boolean not null default false
);
-- addition: boards are keyed (season, draft_type), so a season has one real draft of each type
create unique index one_real_draft_per_type on drafts (season, draft_type) where not is_test;

create table draft_slots (
  draft_id      text not null references drafts,
  overall       int  not null,
  round         int  not null,
  pick_in_round int  not null,
  original_team text not null references teams,
  team_abbr     text not null references teams,   -- current owner
  round_type    text not null check (round_type in ('fypd','open','vc','tc')),
  skip_reason   text check (skip_reason in ('keeper','taxed','buyin_unpaid')),
  -- addition: set when an admin voids the pick in this slot. Makeup slots are taken first, in
  -- the order they were voided, right after the pick that is on the clock.
  makeup_seq    int,
  primary key (draft_id, overall),
  unique (draft_id, round, pick_in_round),
  unique (draft_id, makeup_seq)
);
create index draft_slots_team_idx on draft_slots (draft_id, team_abbr);

create table draft_picks_live (                   -- the spec's draft_picks
  draft_id      text not null,
  overall       int  not null,
  upid          text not null references players,
  team_abbr     text not null references teams,
  source        text not null check (source in ('web','autopick','forklift','admin')),
  made_by       text not null,                    -- manager, 'system', or admin name
  made_at       timestamptz not null default now(),
  -- clock length minus time left at the pick (full clock for autopick and forklift). Speed awards.
  seconds_used  int check (seconds_used >= 0),
  contract_tier text not null,                    -- 'BC'/'DC' (prospect draft) or 'VC-1'/'TC-1' (keeper draft)
  seq           bigint generated always as identity,   -- addition: the order picks were made (undo takes the latest)
  primary key (draft_id, overall),
  foreign key (draft_id, overall) references draft_slots,
  unique (draft_id, upid)                         -- a player can only be drafted once
);
create index draft_picks_live_seq_idx on draft_picks_live (draft_id, seq);

create table draft_clock (
  draft_id            text primary key references drafts,
  on_clock_overall    int,
  deadline_at         timestamptz,                -- null while paused
  paused_remaining_ms int,
  warned              boolean not null default false,
  runner_heartbeat_at timestamptz,                -- the clock runner writes this every second
  foreign key (draft_id, on_clock_overall) references draft_slots
);

create table draft_forklift (
  draft_id   text references drafts,
  team_abbr  text references teams,
  enabled_at timestamptz not null default now(),
  primary key (draft_id, team_abbr)
);

create table draft_boards (                       -- each manager's big board
  season     int  not null,
  draft_type text not null,
  team_abbr  text not null references teams,
  slot_key   text not null check (slot_key = 'any' or slot_key ~ '^[A-Za-z0-9_-]+:[0-9]+$'),  -- a pick they own ('prospect-2027:27') or 'any'
  position   int  not null check (position between 1 and 50),   -- boards hold up to 50 players
  upid       text not null references players,
  primary key (season, draft_type, team_abbr, slot_key, position),
  unique (season, draft_type, team_abbr, slot_key, upid)
);

create table draft_rankings (                     -- uploaded by Zach before each draft
  draft_id text references drafts,
  upid     text references players,
  rank     int  not null check (rank >= 1),
  source   text,
  primary key (draft_id, upid)
);

create table draft_pool_overrides (               -- admin fixes found during pool check
  draft_id text references drafts,
  upid     text references players,
  action   text not null check (action in ('include','exclude')),
  reason   text not null check (btrim(reason) <> ''),
  set_by   text not null,
  set_at   timestamptz not null default now(),
  primary key (draft_id, upid)
);

create table notifications (                      -- outbox: nothing is sent inside a transaction
  id         bigserial primary key,
  kind       text not null,                       -- 'on_clock','warning','on_deck','pick_made','draft_paused',...
  team_abbr  text,
  payload    jsonb not null,
  created_at timestamptz not null default now(),
  sent_at    timestamptz,
  attempts   int  not null default 0,
  last_error text
);
create index notifications_unsent_idx on notifications (id) where sent_at is null;

create table draft_events (                       -- admin actions, for the audit trail
  id       bigserial primary key,
  draft_id text references drafts,
  kind     text not null,                         -- 'start','pause','resume','undo','swap','void','makeup_pick','forklift_on','player_added',...
  actor    text not null,
  detail   jsonb,
  at       timestamptz not null default now()
);
create index draft_events_draft_idx on draft_events (draft_id, id);

create table draft_add_requests (
  id            bigserial primary key,
  draft_id      text not null references drafts,
  requested_by  text not null references teams,
  mlb_id        int  not null,
  player_name   text not null,
  note          text,
  status        text not null default 'pending' check (status in ('pending','approved','rejected')),
  paused_draft  boolean not null default false,
  decided_by    text,
  decided_at    timestamptz,
  decision_note text,
  created_upid  text references players,
  created_at    timestamptz not null default now()
);
create unique index one_pending_request_per_player on draft_add_requests (draft_id, mlb_id)
  where status = 'pending';

-- Fastest-drafter prize money (spec: 1st $30 ... 6th $5). Data, not code.
create table draft_award_amounts (
  season int not null,
  place  int not null check (place between 1 and 6),
  amount int not null check (amount > 0),
  primary key (season, place)
);
insert into draft_award_amounts (season, place, amount) values
  (2027,1,30), (2027,2,25), (2027,3,20), (2027,4,15), (2027,5,10), (2027,6,5);

-- Speed awards pay once per team per season, even if the job runs twice.
create unique index wb_ledger_speed_award_once on wb_ledger (team_abbr, ref_id) where ref_type = 'draft_speed';

-- search looks aliases up by player; the Spec 01 primary key leads with the alias text
create index player_aliases_upid_idx on player_aliases (upid);

-- ------------------------------------------------------------ search helper
-- Lowercase, accents stripped, apostrophes and periods removed, other punctuation becomes a space.
-- (Same idea as player_aliases.alias_normalized.)
create function draft_norm(t text) returns text language sql stable as $$
  select btrim(regexp_replace(regexp_replace(lower(unaccent(coalesce(t, ''))), '[.''’]', '', 'g'), '[^a-z0-9]+', ' ', 'g'))
$$;

-- ----------------------------------------------------- the one eligibility rule
-- draft_pool(draft_id) returns EVERY player it considered, with eligible and a plain-text reason.
-- The website, search, autopick, boards and the pick check all use it; nothing else decides eligibility.
--
-- for_round: the round of the slot being picked. When given and that round is an FYPD round
-- (draft_slots.round_type = 'fypd'), only fypd = true players are eligible. null = ignore that rule.
--
-- Ownership is read from contracts (the live table) AND, until the Dec 15 cutover, from the open
-- roster_assignments rows (the shadow mirror, still the roster of record for 2026 holdings).
-- REMOVE THE roster_assignments JOIN BELOW AT CUTOVER, once contracts is the only owner list.
--
-- Order of reasons: drafted here, admin exclude, owned, [admin include], wrong player type, FYPD.
-- An admin include lifts the player-type and FYPD rules only; it never makes a drafted or owned
-- player eligible (the one-open-contract index would reject the pick anyway).
create function draft_pool(p_draft_id text, for_round int default null)
returns table (
  upid text, name text, eligible boolean, reason text,
  player_type text, positions text, mlb_team text, fypd boolean,
  draft_rank int, drafted_overall int, owner text
) language sql stable as $$
  select p.upid, p.name, x.reason is null, x.reason,
         p.player_type, p.positions, p.mlb_team, p.fypd,
         r.rank, dp.overall, coalesce(oc.team_abbr, ra.team_abbr)
    from drafts d
   cross join players p
    left join draft_picks_live dp on dp.draft_id = d.draft_id and dp.upid = p.upid
    left join draft_pool_overrides o on o.draft_id = d.draft_id and o.upid = p.upid
    left join draft_rankings r on r.draft_id = d.draft_id and r.upid = p.upid
    left join contracts oc on oc.upid = p.upid and oc.valid_to is null
    left join roster_assignments ra on ra.upid = p.upid and ra.valid_to is null   -- mirror: remove at cutover
   cross join lateral (
      select min(s.round) as lo, max(s.round) as hi, coalesce(bool_or(s.round = for_round), false) as is_round
        from draft_slots s where s.draft_id = d.draft_id and s.round_type = 'fypd'
   ) f
   cross join lateral (
      select case
        when dp.upid is not null then 'Already drafted (pick ' || dp.overall || ')'
        when o.action = 'exclude' then 'Excluded by admin: ' || o.reason
        when oc.upid is not null or ra.upid is not null
          then 'Owned by ' || coalesce(oc.team_abbr, ra.team_abbr, 'a team (not yet resolved)')
        when o.action = 'include' then null
        when d.draft_type = 'prospect' and p.player_type is distinct from 'Farm'
          then 'Not a prospect (' || coalesce(p.player_type, 'no player type') || ')'
        when d.draft_type = 'keeper' and p.player_type is distinct from 'MLB'
          then 'Not an MLB player (' || coalesce(p.player_type, 'no player type') || ')'
        when d.draft_type = 'prospect' and f.is_round and not coalesce(p.fypd, false)
          then 'Not FYPD eligible (' || case when f.lo = f.hi then 'round ' || f.lo else 'rounds ' || f.lo || '-' || f.hi end || ')'
        else null
      end as reason
   ) x
   where d.draft_id = p_draft_id
$$;
