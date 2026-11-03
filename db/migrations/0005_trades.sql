-- Spec 05: trade portal (specs/05_trade_portal.md, "Schema").
--
-- Differences from the spec text (each one is flagged in the build report):
--   * The spec reshapes Spec 01's `trades` and `trade_transfers` in place. Until the cutover the daily
--     import truncates and reloads those two mirror tables from data/trades.json, so they keep their
--     names and the spec's tables are created new:
--         trades_live       the spec's `trades`
--         trade_parties     the spec's `trade_parties`
--         trade_items       the spec's `trade_items`   (replaces the mirror's `trade_transfers`)
--         trade_comments    the spec's `trade_comments`
--     At cutover (scripts/db/cutover_trades.py has loaded the 95 history rows): drop the mirror
--     tables and rename trades_live to trades.
--   * trade_sequences + trade_next_id() are additions: the 'T27-0001' ids come from one counter per season.
--   * trade_items.raw is an addition: the 2026 import keeps the original pick transfer there, because
--     2026 picks cannot be pointed at draft_slots rows (the 2026 draft order is not in draft_slots).
--   * More constraints than the spec writes (all additions): the trade id matches the season, a status
--     and its close_kind/approval fields agree, 2 or 3 parties, every item's two teams are parties,
--     no duplicate items, an item's columns match its kind, a manual trade carries a note, a
--     pick points at a real draft_slots row.

create table trade_sequences (
  season int primary key,
  next   int not null default 1 check (next >= 1)
);

-- 'T27-0001': 'T' + the last two digits of the season + a per-season counter. Rolls back with the
-- transaction that asked for it, so ids have no gaps.
create function trade_next_id(p_season int) returns text language sql as $$
  insert into trade_sequences (season, next) values (p_season, 2)
  on conflict (season) do update set next = trade_sequences.next + 1
  returning 'T' || lpad((p_season % 100)::text, 2, '0') || '-' || lpad((next - 1)::text, 4, '0')
$$;

create table trades_live (
  trade_id        text primary key,               -- 'T27-0001': season + sequence
  season          int not null,
  status          text not null check (status in
                  ('pending','accepted','approved','rejected','countered','withdrawn','expired','voided')),
  created_by      text not null references teams,
  created_at      timestamptz not null default now(),
  expires_at      timestamptz,                    -- only while pending
  countered_from  text references trades_live,    -- the offer this one answers
  approved_by     text,
  approved_at     timestamptz,                    -- also the processing date
  closed_by       text,                           -- who rejected, withdrew, expired or voided
  close_kind      text check (close_kind in ('reject','withdraw','conflict','expire','void')),
  close_note      text,
  source          text not null default 'portal' check (source in ('portal','admin_manual','import')),
  raw             jsonb,
  constraint trades_live_id_matches_season
    check (trade_id ~ ('^T' || lpad((season % 100)::text, 2, '0') || '-[0-9]{4,}$')),
  constraint trades_live_not_own_counter check (countered_from is distinct from trade_id),
  constraint trades_live_expiry_only_pending check ((status = 'pending') = (expires_at is not null)),
  constraint trades_live_approval_fields check (
    (status in ('approved','voided')) = (approved_by is not null and approved_at is not null)),
  -- a status and the way it was closed always agree
  constraint trades_live_close_kind_matches_status check (
    case status
      when 'rejected'  then close_kind = 'reject'
      when 'withdrawn' then close_kind in ('withdraw','conflict')
      when 'expired'   then close_kind = 'expire'
      when 'voided'    then close_kind = 'void'
      else close_kind is null
    end),
  constraint trades_live_closed_by check (close_kind is null or closed_by is not null),
  constraint trades_live_reason_required check (close_kind not in ('reject','void') or btrim(coalesce(close_note, '')) <> ''),
  constraint trades_live_manual_note check (source <> 'admin_manual' or btrim(coalesce(raw ->> 'note', '')) <> '')
);
create index trades_live_status_idx on trades_live (status);
create index trades_live_countered_idx on trades_live (countered_from) where countered_from is not null;

create table trade_parties (
  trade_id    text not null references trades_live,
  team_abbr   text not null references teams,
  accepted_at timestamptz,
  primary key (trade_id, team_abbr)
);
create index trade_parties_team_idx on trade_parties (team_abbr);

-- the sender is a party (checked at commit, so a trade and its parties are written in either order)
alter table trades_live add constraint trades_live_sender_is_party
  foreign key (trade_id, created_by) references trade_parties (trade_id, team_abbr) deferrable initially deferred;

-- 2 or 3 parties, checked at commit
create function trade_parties_count_check() returns trigger language plpgsql as $$
declare
  n int;
  tid text := coalesce(new.trade_id, old.trade_id);
begin
  select count(*) into n from trade_parties where trade_id = tid;
  if exists (select 1 from trades_live where trade_id = tid) and n not between 2 and 3 then
    raise exception 'trade % has % parties, a trade has 2 or 3', tid, n using errcode = '23514';
  end if;
  return null;
end $$;
create constraint trigger trade_parties_count after insert or update or delete on trade_parties
  deferrable initially deferred for each row execute function trade_parties_count_check();

create table trade_items (
  id         bigserial primary key,
  trade_id   text not null references trades_live,
  kind       text not null check (kind in ('player','pick','wb')),
  upid       text references players,
  draft_id   text, overall int,                    -- for picks, points at draft_slots
  amount     int check (amount > 0 and amount % 5 = 0),
  from_team  text not null references teams,
  to_team    text not null references teams,
  raw        jsonb,                                -- addition: the 2026 transfer, for imported picks
  check (from_team <> to_team),
  -- both teams are parties of the trade
  foreign key (trade_id, from_team) references trade_parties (trade_id, team_abbr),
  foreign key (trade_id, to_team)   references trade_parties (trade_id, team_abbr),
  foreign key (draft_id, overall)   references draft_slots (draft_id, overall),
  constraint trade_items_kind_columns check (
    case kind
      when 'player' then upid is not null and draft_id is null and overall is null and amount is null
      when 'pick'   then upid is null and amount is null and (draft_id is null) = (overall is null)
                         and (draft_id is not null or raw is not null)   -- only an imported 2026 pick may lack a slot
      when 'wb'     then amount is not null and upid is null and draft_id is null and overall is null
    end)
);
create index trade_items_trade_idx on trade_items (trade_id);
create index trade_items_upid_idx on trade_items (upid) where upid is not null;
-- no duplicate items inside one trade
create unique index trade_items_one_player on trade_items (trade_id, upid) where upid is not null;
create unique index trade_items_one_pick on trade_items (trade_id, draft_id, overall) where draft_id is not null;
create unique index trade_items_one_wb on trade_items (trade_id, from_team, to_team) where kind = 'wb';

create table trade_comments (
  id         bigserial primary key,
  trade_id   text not null references trades_live,
  team_abbr  text not null references teams,       -- a system comment is filed under the sender's team
  kind       text not null default 'comment' check (kind in ('comment','system')),
  body       text not null check (length(body) <= 500 and btrim(body) <> ''),
  created_at timestamptz not null default now()
);
create index trade_comments_trade_idx on trade_comments (trade_id, id);

comment on table trades_live is 'The spec''s `trades`. Named trades_live because the Spec 01 mirror owns `trades` until cutover.';
comment on table trade_items is 'The spec''s `trade_items`; replaces the mirror trade_transfers at cutover.';
