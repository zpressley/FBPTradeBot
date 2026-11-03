"""Draft simulator (specs/04_draft_room.md, "Testing").

Runs a whole draft against a TEST database with one fake manager per team picking at random
intervals (some too slow, some never), a clock runner that is "killed" and restarted at random, an
admin who pauses and resumes, and a viewer polling state(). Then it checks that nothing stalled,
nothing was picked twice, every slot is filled and the contracts match the picks.

    python scripts/draft/simulate.py --draft prospect-2027 --clock 2 --setup --database-url URL

It refuses to run unless the draft is_test. Run it on a Neon test branch (or a local scratch
database), never on main. --setup builds the is_test draft from the mirror's 2026 order (and, for
the Prospect Draft, buys the prepaid slots the order needs). --cleanup removes everything the run
made, including its ledger rows: that needs fbp.ledger_maintenance, so it only runs against a
local database.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402

from draft import service as ds  # noqa: E402
from league import actions  # noqa: E402

SIM = "simulator"


def connect(url):
    return psycopg.connect(url, autocommit=True)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------ setup / cleanup

def setup(conn, draft_id, clock, mirror_season, rng):
    kind = draft_id.split("-")[0]
    if kind not in ("prospect", "keeper"):
        raise SystemExit("--setup needs a draft id like prospect-2027 or keeper-2027")
    season = int(draft_id.split("-")[-1])
    slots = ds.slots_from_mirror(conn, kind, mirror_season)
    ds.create_draft(conn, draft_id, season, kind, slots, clock_seconds=clock, is_test=True, forklift_seconds=1)
    teams = sorted({s["team_abbr"] for s in slots if not s["skip_reason"]})
    if kind == "prospect":
        now = datetime.now(timezone.utc)
        actions.open_window(conn, season, "PAD", now - timedelta(days=1), now + timedelta(days=1), SIM)
        bc_max = conn.execute("select bc_slot_max_round from prospect_limits where season = %s", (season,)).fetchone()[0]
        for team in teams:
            actions.wb_credit(conn, team, 1000, "opening_balance", "PAD", season, created_by=SIM)
            for slot_type, rounds in (("BC", lambda r: r <= bc_max), ("DC", lambda r: r > bc_max)):
                n = sum(1 for s in slots if s["team_abbr"] == team and rounds(s["round"]))
                if n:
                    actions.buy_draft_slot(conn, team, slot_type, created_by=SIM, season=season, count=n, now=now)
    # a full ranking of the pool, best player rank first (players.rank where there is one, then by upid)
    ranked = conn.execute("select upid from draft_pool(%s) where eligible order by (select rank from players p where p.upid = draft_pool.upid) nulls last, upid::bigint",
                          (draft_id,)).fetchall()
    res = ds.upload_rankings(conn, draft_id, [{"upid": r[0], "rank": i} for i, r in enumerate(ranked, start=1)], source=SIM)
    assert res["saved"], res["unmatched"][:3]
    # two teams on forklift (boards of 3, so they run dry and turn forklift off) and two more with boards
    chosen = rng.sample(teams, 4)
    for team in chosen:
        pool = [r[0] for r in conn.execute("select upid from draft_pool(%s, 3) where eligible order by random() limit 3", (draft_id,))]
        ds.set_board(conn, draft_id, team, "any", pool)
    for team in chosen[:2]:
        ds.set_forklift(conn, draft_id, team, True, SIM)
    log(f"setup: {draft_id} with {len(slots)} slots ({sum(1 for s in slots if not s['skip_reason'])} draftable), {len(teams)} teams, "
        f"{len(ranked)} ranked players")


def cleanup(conn, draft_id):
    d = conn.execute("select season, draft_type from drafts where draft_id = %s", (draft_id,)).fetchone()
    upids = [r[0] for r in conn.execute("select upid from draft_picks_live where draft_id = %s", (draft_id,))]
    conn.execute("delete from notifications where payload->>'draft_id' = %s", (draft_id,))
    for t in ("draft_events", "draft_add_requests", "draft_picks_live", "draft_clock", "draft_forklift", "draft_rankings",
              "draft_pool_overrides", "draft_slots"):
        conn.execute(f"delete from {t} where draft_id = %s", (draft_id,))
    if d:
        conn.execute("delete from draft_boards where season = %s and draft_type = %s", (d[0], d[1]))
    conn.execute("delete from drafts where draft_id = %s", (draft_id,))
    if upids:
        conn.execute("delete from roster_slots where upid = any(%s)", (upids,))
        conn.execute("delete from contracts where upid = any(%s)", (upids,))
        conn.execute("delete from player_events where source_file = 'league_actions' and upid = any(%s)", (upids,))
    conn.execute("delete from player_events where admin = %s and upid is null", (SIM,))
    conn.execute("delete from prospect_draft_slots where ledger_id in (select id from wb_ledger where created_by = %s)", (SIM,))
    conn.execute("set fbp.ledger_maintenance = 'on'")      # the ledger is append-only; a scratch database only
    conn.execute("delete from wb_ledger where created_by = %s", (SIM,))
    conn.execute("reset fbp.ledger_maintenance")
    conn.execute("delete from league_windows where opened_by = %s", (SIM,))
    log("cleanup done")


# ------------------------------------------------------------------ the actors

def runner(url, draft_id, clock, stats, stop, rng, stale):
    """The bot's clock loop. Every few seconds it is 'killed': no ticks, no heartbeat, for longer than `stale`."""
    conn = connect(url)
    interval = max(0.05, clock / 10)
    next_outage = time.time() + rng.uniform(3, 6)
    while not stop.is_set():
        if time.time() >= next_outage:
            stats["restarts"] += 1
            time.sleep(stale + 0.6 + rng.random())
            next_outage = time.time() + rng.uniform(4, 9)
        try:
            r = ds.clock_tick(conn, draft_id, stale_seconds=stale, restart_seconds=clock)
            if r:
                stats["tick_" + r] += 1
        except Exception as e:  # noqa: BLE001 - the real runner logs and keeps going
            stats["tick_errors"] += 1
            log(f"tick error: {e}")
        time.sleep(interval)
    conn.close()


def manager(url, draft_id, team, afk, clock, stats, stop, rng):
    conn = connect(url)
    thinking, decide_at = None, 0.0
    while not stop.is_set():
        time.sleep(rng.uniform(0.03, 0.15))
        row = conn.execute("""select c.on_clock_overall, s.team_abbr, s.round from draft_clock c
                                join drafts d on d.draft_id = c.draft_id
                                join draft_slots s on s.draft_id = c.draft_id and s.overall = c.on_clock_overall
                               where c.draft_id = %s and d.status = 'live'""", (draft_id,)).fetchone()
        if not row or row[1] != team or afk:
            thinking = None
            continue
        overall, _, rnd = row
        if thinking != overall:
            thinking, decide_at = overall, time.time() + rng.uniform(0.05, clock * 1.6)   # some take longer than the clock
            continue
        if time.time() < decide_at:
            continue
        try:
            nxt = ds._open_slots(conn, draft_id, exclude=overall, limit=1)
            run = 2 if nxt and nxt[0]["team_abbr"] == team and nxt[0]["makeup_seq"] is None and rng.random() < 0.5 else 1
            picks = [r[0] for r in conn.execute("select upid from draft_pool(%s, %s) where eligible order by random() limit %s",
                                                (draft_id, rnd, run))]
            ds.make_picks(conn, draft_id, team, picks, "web", f"mgr-{team}")
            stats["web_picks"] += len(picks)
            if len(picks) > 1:
                stats["two_time_lockins"] += 1
        except ds.DraftError as e:      # lost the race to the clock runner, or the run changed: expected, not a failure
            stats["rejected"] += 1
            stats["rejected: " + str(e)[:40]] += 1
        except Exception as e:  # noqa: BLE001
            stats["manager_errors"] += 1
            log(f"manager {team} error: {e!r}")
        thinking = None
    conn.close()


def admin(url, draft_id, stats, stop, rng):
    """Pauses and resumes the draft now and then."""
    conn = connect(url)
    while not stop.wait(rng.uniform(4, 8)):
        try:
            ds.pause(conn, draft_id, SIM, "simulated pause")
            stats["pauses"] += 1
            time.sleep(rng.uniform(0.3, 1.2))
            ds.resume(conn, draft_id, SIM)
        except ds.DraftError:
            pass
    conn.close()


def viewer(url, draft_id, stats, stop):
    conn = connect(url)
    while not stop.wait(0.5):
        try:
            payload = ds.state(conn, draft_id, team="WAR")
            json.dumps(payload)
            assert payload["server_now"]
            stats["state_polls"] += 1
        except Exception as e:  # noqa: BLE001
            stats["state_errors"] += 1
            log(f"state error: {e!r}")
    conn.close()


# ------------------------------------------------------------------ checks

def verify(conn, draft_id, clock, max_gap):
    d = conn.execute("select season, draft_type, status from drafts where draft_id = %s", (draft_id,)).fetchone()
    season, kind, status = d
    results = []

    def check(name, ok, detail=""):
        results.append((ok, name, detail))

    draftable = conn.execute("select count(*) from draft_slots where draft_id = %s and skip_reason is null", (draft_id,)).fetchone()[0]
    picks = conn.execute("select count(*), count(distinct upid), count(distinct overall) from draft_picks_live where draft_id = %s", (draft_id,)).fetchone()
    check("draft is complete", status == "complete", f"status {status}")
    check("every draftable slot is filled", picks[0] == draftable, f"{picks[0]} picks for {draftable} slots")
    check("nobody was picked twice", picks[0] == picks[1] == picks[2], f"{picks}")
    unfilled = conn.execute("""select count(*) from draft_slots s left join draft_picks_live p using (draft_id, overall)
                                where s.draft_id = %s and s.skip_reason is null and p.upid is null""", (draft_id,)).fetchone()[0]
    check("no open slot", unfilled == 0, f"{unfilled} open")
    skipped = conn.execute("""select count(*) from draft_picks_live p join draft_slots s using (draft_id, overall)
                               where p.draft_id = %s and s.skip_reason is not null""", (draft_id,)).fetchone()[0]
    check("no pick in a skipped slot", skipped == 0)
    wrong_team = conn.execute("""select count(*) from draft_picks_live p join draft_slots s using (draft_id, overall)
                                  where p.draft_id = %s and p.team_abbr <> s.team_abbr""", (draft_id,)).fetchone()[0]
    check("every pick is by the slot's owner", wrong_team == 0)
    via = "prospect_draft" if kind == "prospect" else "keeper_draft"
    bad = conn.execute("""select count(*) from draft_picks_live p join draft_slots s using (draft_id, overall)
                           left join contracts c on c.upid = p.upid and c.valid_to is null
                          where p.draft_id = %s and (c.id is null or c.team_abbr <> p.team_abbr or c.acquired_via <> %s
                                                     or c.acquired_round is distinct from s.round)""", (draft_id, via)).fetchone()[0]
    check("every pick has its open contract (team, round, source)", bad == 0, f"{bad} mismatches")
    if kind == "prospect":
        bad = conn.execute("""select count(*) from draft_picks_live p join contracts c on c.upid = p.upid and c.valid_to is null
                               where p.draft_id = %s and c.prospect_type is distinct from p.contract_tier""", (draft_id,)).fetchone()[0]
        check("contract type matches the pick's tier", bad == 0)
        open_slots = conn.execute("select count(*) from prospect_draft_slots where season = %s and filled_upid is null", (season,)).fetchone()[0]
        filled = conn.execute("select count(*) from prospect_draft_slots where filled_upid in (select upid from draft_picks_live where draft_id = %s)",
                              (draft_id,)).fetchone()[0]
        check("every prepaid slot was filled by a pick", open_slots == 0 and filled == picks[0], f"{filled} filled, {open_slots} still open")
    extra = conn.execute("""select count(*) from contracts c where c.acquired_via = %s and c.valid_to is null
                             and c.upid not in (select upid from draft_picks_live where draft_id = %s)
                             and c.valid_from > now() - interval '1 day'""", (via, draft_id)).fetchone()[0]
    check("no contract without a pick", extra == 0, f"{extra} extra")
    bad = conn.execute("select count(*) from draft_picks_live where draft_id = %s and (seconds_used < 0 or seconds_used > %s)",
                       (draft_id, clock)).fetchone()[0]
    check("seconds_used within the clock", bad == 0)
    told = conn.execute("""select coalesce(sum(jsonb_array_length(payload->'picks')), 0) from notifications
                            where kind = 'pick_made' and payload->>'draft_id' = %s""", (draft_id,)).fetchone()[0]
    check("one pick_made announcement per pick", told == picks[0], f"{told} announced")
    check("no stall: longest gap between picks", max_gap <= max(30, clock * 12), f"{max_gap:.1f}s")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--draft", required=True, help="e.g. prospect-2027 (must be an is_test draft)")
    ap.add_argument("--clock", type=int, default=2, help="seconds per pick (default 2)")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--setup", action="store_true", help="create the is_test draft from the mirror's order first")
    ap.add_argument("--mirror-season", type=int, default=2026)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=900, help="give up after this many seconds")
    ap.add_argument("--cleanup", action="store_true", help="remove everything the run made (local database only)")
    args = ap.parse_args(argv)

    import os
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set; pass --database-url")
    rng = random.Random(args.seed)
    conn = connect(url)
    if args.cleanup and (urlparse(url).hostname or "") not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit("--cleanup sets fbp.ledger_maintenance and only runs against a local database")
    if args.setup:
        existing = conn.execute("select is_test from drafts where draft_id = %s", (args.draft,)).fetchone()
        if existing is not None:
            raise SystemExit(f"{args.draft} already exists; drop --setup, or run with --cleanup first")
        setup(conn, args.draft, args.clock, args.mirror_season, rng)
    row = conn.execute("select is_test, status, draft_type from drafts where draft_id = %s", (args.draft,)).fetchone()
    if row is None:
        raise SystemExit(f"no draft {args.draft!r} (use --setup to build one)")
    if not row[0]:
        raise SystemExit(f"REFUSING to run: {args.draft} is not an is_test draft. The simulator only runs on test drafts.")
    if row[1] not in ("setup", "pool_check"):
        raise SystemExit(f"{args.draft} is {row[1]}; the simulator starts a draft from setup.")

    teams = [r[0] for r in conn.execute("select distinct team_abbr from draft_slots where draft_id = %s and skip_reason is null order by 1", (args.draft,))]
    afk = set(rng.sample(teams, 3))
    log(f"{len(teams)} managers; never pick: {sorted(afk)}; clock {args.clock}s")
    ds.start(conn, args.draft, SIM)

    stats, stop = Counter(), threading.Event()
    stale = 1.0
    threads = [threading.Thread(target=runner, args=(url, args.draft, args.clock, stats, stop, random.Random(rng.random()), stale)),
               threading.Thread(target=admin, args=(url, args.draft, stats, stop, random.Random(rng.random()))),
               threading.Thread(target=viewer, args=(url, args.draft, stats, stop))]
    threads += [threading.Thread(target=manager, args=(url, args.draft, t, t in afk, args.clock, stats, stop, random.Random(rng.random())))
                for t in teams]
    for t in threads:
        t.daemon = True
        t.start()

    t0 = time.time()
    last_count, last_change, max_gap, last_log = -1, time.time(), 0.0, 0
    status = "live"
    while time.time() - t0 < args.timeout:
        time.sleep(0.25)
        n = conn.execute("select count(*) from draft_picks_live where draft_id = %s", (args.draft,)).fetchone()[0]
        if n != last_count:
            if last_count >= 0:
                max_gap = max(max_gap, time.time() - last_change)
            last_count, last_change = n, time.time()
        status = conn.execute("select status from drafts where draft_id = %s", (args.draft,)).fetchone()[0]
        if status == "complete":
            break
        if int(time.time() - t0) // 10 > last_log:
            last_log = int(time.time() - t0) // 10
            log(f"{n} picks, {status}")
        if status == "paused" and time.time() - last_change > 5 * args.clock + 15:
            break     # paused with nobody to resume it: a stall
    stop.set()
    for t in threads:
        t.join(timeout=10)
    max_gap = max(max_gap, 0.0) if status == "complete" else max(max_gap, time.time() - last_change)
    elapsed = time.time() - t0

    results = verify(conn, args.draft, args.clock, max_gap)
    sources = dict(conn.execute("select source, count(*) from draft_picks_live where draft_id = %s group by 1 order by 1", (args.draft,)).fetchall())
    print()
    for ok, name, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    errors = stats["tick_errors"] + stats["manager_errors"] + stats["state_errors"]
    print(f"{'PASS' if errors == 0 else 'FAIL'}  no unexpected errors in the runner, managers or state polls" + ("" if errors == 0 else f"  ({errors})"))
    print(f"\n{last_count} picks in {elapsed:.0f}s; sources {sources}; longest gap between picks {max_gap:.1f}s")
    print("runner: forced restarts %d, restart-rule extensions %d, autopicks %d, forklift picks %d, forklift turned off %d, "
          "paused-for-no-autopick %d" % (stats["restarts"], stats["tick_extended"], stats["tick_autopick"], stats["tick_forklift"],
                                        stats["tick_forklift_off"], stats["tick_paused"]))
    print("managers: web picks %d (2 Time lock-ins %d), rejected tries %d (lost the race to the clock or the run changed); "
          "admin pauses %d; state polls %d" % (stats["web_picks"], stats["two_time_lockins"], stats["rejected"], stats["pauses"], stats["state_polls"]))
    failed = not all(ok for ok, _, _ in results) or errors
    print("\nSIMULATION " + ("FAILED" if failed else "PASSED"))
    if args.cleanup:
        cleanup(conn, args.draft)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
