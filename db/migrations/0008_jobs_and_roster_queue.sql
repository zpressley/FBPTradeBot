-- Spec 03 "Automatic jobs" (specs/03_rosters_contracts_wizbucks.md): the two pieces of
-- state the scheduler needs. The spec describes both behaviourally and gives no schema,
-- so these tables are plumbing, not rules -- every amount and limit still comes from
-- wb_prices / roster_limits in 0003, and only league/actions.py writes the ledger.
--
-- Depends on 0001 (teams) and 0003 (wb_ledger, wb_prices, roster_limits).

-- ------------------------------------------------- roster-violation admin queue
-- Spec 03: "Violations go to an admin queue with the penalty amount pre-filled.
-- Nothing is charged until an admin confirms." So this table holds the finding and
-- never any money: confirming calls league.actions.penalty(), which is the only
-- thing that writes wb_ledger, and the ledger row it creates is recorded here.
--
-- Columns mirror league.actions.roster_violations(): rule / over / detail, plus the
-- team and penalty_amount that roster_check() adds.
create table roster_violation_queue (
  id              bigserial primary key,
  season          int  not null,
  team_abbr       text not null references teams,
  rule            text not null,
  -- named over_by because "over" is a reserved word in SQL, the same reason
  -- league_windows.window_name is not called "window" (0003)
  over_by         int  not null check (over_by > 0),
  detail          text not null,
  penalty_amount  int  not null check (penalty_amount >= 0),
  nights_standing int  not null default 1 check (nights_standing > 0),
  first_seen_at   timestamptz not null,
  last_seen_at    timestamptz not null,

  -- a row leaves the queue exactly one way: it cleared on its own, an admin
  -- confirmed the fine, or an admin dismissed it
  cleared_at      timestamptz,
  confirmed_at    timestamptz,
  confirmed_by    text,
  ledger_id       bigint references wb_ledger,
  dismissed_at    timestamptz,
  dismissed_by    text,
  dismiss_reason  text,

  check (last_seen_at >= first_seen_at),
  check (num_nonnulls(cleared_at, confirmed_at, dismissed_at) <= 1),
  check ((confirmed_at is null) = (confirmed_by is null)),
  check ((dismissed_at is null) = (dismissed_by is null)),
  -- the ledger row only exists once an admin has confirmed
  check (ledger_id is null or confirmed_at is not null)
);

-- One OPEN row per team per rule per season. This is what makes the nightly job
-- idempotent: a violation still standing updates its row (bumping nights_standing),
-- it never inserts a second one.
create unique index roster_violation_queue_open_idx
  on roster_violation_queue (season, team_abbr, rule)
  where cleared_at is null and confirmed_at is null and dismissed_at is null;

create index roster_violation_queue_team_idx on roster_violation_queue (team_abbr, season);

-- -------------------------------------------------------- MLB rookie limits
-- prospect_status.over_mlb_limits (0003) has a real consumer: league.actions.retain_bc
-- reads it for the Art. III s04 exemption, where a BC prospect over MLB rookie limits
-- but under FBP limits retains his contract free. Nothing ever set that column, and
-- unlike FBP's limits (prospect_limits, 0003) there was no table of MLB's, so the
-- nightly job had nowhere to read them from and the exemption could never fire.
--
-- These are MLB's published rookie-eligibility thresholds, kept as data so the Python
-- never hardcodes them, same as every other limit in 0003.
--
-- NEEDS ZACH, two things:
--   1. Confirm the numbers below are the ones FBP means.
--   2. MLB's definition also ends rookie status at 45 days on an active roster. That
--      is NOT modelled here: the Stats API stat groups this job reads give no active-
--      roster day count, so over_mlb_limits is computed from AB and IP only. A player
--      who passed 45 days without passing 130 AB / 50 IP will read as under the limit.
create table mlb_rookie_limits (
  season int primary key,
  ab int not null,
  ip numeric not null
);
insert into mlb_rookie_limits (season, ab, ip) values (2027, 130, 50);

-- MLB's rookie limit is at-bats; FBP's is plate appearances (prospect_limits.pa), so
-- the two cannot share a column.
alter table prospect_status add column ab int;

-- ------------------------------------------------------------------- job_runs
-- Scheduler bookkeeping, one row per job. `next_attempt_at` is the backoff gate:
-- the dispatcher skips a job until that time has passed.
--
-- This lives in the database rather than memory or a JSON file on purpose. WARP.md's
-- "External API Rate Limits" rule exists because standings_refresh_tick retried every
-- 5 minutes for 11 days (~1,700 failed Yahoo calls) with no backoff; Railway redeploys
-- often, so in-memory backoff state would reset just as effectively as having none.
create table job_runs (
  job_name            text primary key,
  last_run_at         timestamptz,
  last_success_at     timestamptz,
  last_result         text,
  consecutive_failures int  not null default 0 check (consecutive_failures >= 0),
  next_attempt_at     timestamptz,
  last_error          text
);
