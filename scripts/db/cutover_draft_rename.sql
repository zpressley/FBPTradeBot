-- Spec 04 cutover: the draft room's picks table takes the spec's name, draft_picks.
-- Plain SQL with no BEGIN/COMMIT of its own; run it as ONE transaction with --single-transaction. Zach runs it ONCE, at the cutover, together with the matching one-line change
-- in draft/service.py (PICKS_TABLE = "draft_picks") and the same for trades/service.py (see db/README.md):
--
--     psql "$DATABASE_URL_DIRECT" --single-transaction -v ON_ERROR_STOP=1 -f scripts/db/cutover_draft_rename.sql
--
-- BEFORE running it: scripts/db/import_json.py still truncates and reloads Spec 01's mirror table named
-- draft_picks. After this rename that name is the LIVE picks table, so remove "draft_picks" from the import
-- (its build_draft_picks step and the truncate list) in the same change, or the next daily import wipes the picks.
--
-- What it does:
--   1. the Spec 01 mirror draft_picks (the 2026 order) becomes draft_picks_mirror_2026, indexes included;
--   2. draft_picks_live becomes draft_picks, with its indexes, constraints and sequence;
--   3. draft_pool() is redefined to read the new name (SQL functions keep the table by name, not by id, so it
--      must be recreated; it is the only function that names the table). A test keeps this copy identical to
--      the one in 0004_draft.sql apart from the table name.
alter table draft_picks rename to draft_picks_mirror_2026;
alter index draft_picks_pkey rename to draft_picks_mirror_2026_pkey;

alter table draft_picks_live rename to draft_picks;
alter index draft_picks_live_pkey rename to draft_picks_pkey;
alter index draft_picks_live_draft_id_upid_key rename to draft_picks_draft_id_upid_key;
alter index draft_picks_live_seq_idx rename to draft_picks_seq_idx;
alter sequence draft_picks_live_seq_seq rename to draft_picks_seq_seq;
alter table draft_picks rename constraint draft_picks_live_source_check to draft_picks_source_check;
alter table draft_picks rename constraint draft_picks_live_seconds_used_check to draft_picks_seconds_used_check;
alter table draft_picks rename constraint draft_picks_live_upid_fkey to draft_picks_upid_fkey;
alter table draft_picks rename constraint draft_picks_live_team_abbr_fkey to draft_picks_team_abbr_fkey;
alter table draft_picks rename constraint draft_picks_live_draft_id_overall_fkey to draft_picks_draft_id_overall_fkey;

-- draft_pool() now reads draft_picks. A SQL function is stored as text and parsed on each call, so the old copy
-- would fail on draft_picks_live; the replacement has the same signature and result columns.
create or replace function draft_pool(p_draft_id text, for_round int default null)
returns table (
  upid text, name text, eligible boolean, reason text,
  player_type text, positions text, mlb_team text, fypd boolean,
  draft_rank int, drafted_overall int, owner text,
  eligible_rounds text, round_note text
) language sql stable as $$
  select p.upid, p.name, x.reason is null, x.reason,
         p.player_type, p.positions, p.mlb_team, p.fypd,
         r.rank, dp.overall, coalesce(oc.team_abbr, ra.team_abbr),
         case when x.base_reason is not null then 'none'
              when x.fypd_blocked then 'open_rounds_only'
              else 'all' end,
         case when x.base_reason is null and x.fypd_blocked then x.fypd_text end
    from drafts d
   cross join players p
    left join draft_picks dp on dp.draft_id = d.draft_id and dp.upid = p.upid
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
        else null
      end as base_reason,
      -- would the FYPD rule keep him out of the FYPD rounds? (an admin include lifts it)
      (d.draft_type = 'prospect' and f.lo is not null and not coalesce(p.fypd, false)
        and o.action is distinct from 'include') as fypd_blocked,
      'Not FYPD eligible (' || case when f.lo = f.hi then 'round ' || f.lo else 'rounds ' || f.lo || '-' || f.hi end || ')' as fypd_text
   ) y
   cross join lateral (
      select y.base_reason, y.fypd_blocked, y.fypd_text,
             case when y.base_reason is not null then y.base_reason
                  when y.fypd_blocked and f.is_round then y.fypd_text
                  else null end as reason
   ) x
   where d.draft_id = p_draft_id
$$;
