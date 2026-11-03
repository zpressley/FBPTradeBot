"""Lineups and position eligibility (specs/07_team_page_lineups.md).

This is the only code that writes lineups, lineup_versions, position_eligibility, mlb_games and
mlb_roster_status. Every function takes a psycopg connection and opens its own
`with conn.transaction():` (a savepoint inside a caller's transaction). Writes to one team's lineup
take the same `team:<abbr>` advisory lock the league actions use, so a roster move and a lineup save
for one team take turns.

Errors: LineupError (a RuleError; the message is safe to show a manager) and LineupConflict.
Times are timestamptz; a lineup day is the ET calendar date.

Lineup shape, in and out: a list of {"slot_code", "slot_index", "upid"} dicts. Cells left out of a
saved lineup are stored empty.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from league import actions
from league.actions import RuleError

ET = ZoneInfo("America/New_York")
FUTURE_DAYS = 7
BATTER_POSITIONS = ("C", "1B", "2B", "3B", "SS", "CF", "OF", "DH")
PITCHER_POSITIONS = ("SP", "RP")
_MAP = {"LF": "OF", "RF": "OF", "OF": "OF", "CF": "CF", "C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS",
        "DH": "DH", "SP": "SP", "RP": "RP", "SIRP": "RP", "MIRP": "RP"}


class LineupError(RuleError):
    """The lineup breaks a rule. `problems` lists every reason."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


class LineupConflict(Exception):
    """The lineup changed since the caller loaded it. `latest` is what the screen should reload."""

    def __init__(self, latest):
        self.latest = latest
        super().__init__("Lineup changed, here's the latest")


# ------------------------------------------------------------------ plumbing

def _one(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _exec(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _lock_team(conn, team):
    _exec(conn, "select pg_advisory_xact_lock(hashtext('team:' || %s))", (team,))


def _today(now) -> date:
    return now.astimezone(ET).date()


def _setting(conn, key):
    r = _one(conn, "select value from league_settings where key = %s", (key,))
    if r is None:
        raise RuleError(f"league setting {key!r} is missing")
    return r["value"]


def _slot_types(conn, season):
    rows = _all(conn, "select * from lineup_slot_types where season = %s order by sort_order", (season,))
    if not rows:
        raise LineupError(f"no lineup slots are set up for {season}")
    return rows


def _grid(types):
    return [(t["code"], i) for t in types for i in range(1, t["count"] + 1)]


def _team_check(conn, team):
    if not _one(conn, "select 1 as x from teams where abbr = %s", (team,)):
        raise LineupError(f"unknown team {team!r}")


def _bump(conn, team, game_date, now):
    return _one(conn, """insert into lineup_versions (team_abbr, game_date, version, updated_at) values (%s, %s, 1, %s)
                         on conflict (team_abbr, game_date)
                         do update set version = lineup_versions.version + 1, updated_at = excluded.updated_at
                         returning version""", (team, game_date, now))["version"]


def _version(conn, team, game_date):
    r = _one(conn, "select version from lineup_versions where team_abbr = %s and game_date = %s", (team, game_date))
    return r["version"] if r else 0


def _active_upids(conn, team):
    return {r["upid"] for r in _all(conn, """
        select c.upid from contracts c join roster_slots rs on rs.upid = c.upid and rs.slot = 'active'
         where c.team_abbr = %s and c.valid_to is null""", (team,))}


def _write_cells(conn, team, game_date, types, cells, set_by, locked=None):
    """Replace the team's whole day with the full slot grid. `cells` maps (code, index) to a upid."""
    locked = locked or {}
    _exec(conn, "delete from lineups where team_abbr = %s and game_date = %s", (team, game_date))
    with conn.cursor() as cur:
        cur.executemany(
            """insert into lineups (team_abbr, game_date, slot_code, slot_index, upid, locked_at, set_by)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            [(team, game_date, code, idx, cells.get((code, idx)), locked.get((code, idx)), set_by)
             for code, idx in _grid(types)])


def _copy_forward(conn, team, game_date):
    """Cells of the team's latest earlier lineup in the same season, without anyone no longer active.
    None when the team has no earlier lineup."""
    prev = _one(conn, """select max(game_date) as d from lineups
                          where team_abbr = %s and game_date < %s and season = %s""", (team, game_date, game_date.year))
    if not prev or prev["d"] is None:
        return None
    active = _active_upids(conn, team)
    return {(r["slot_code"], r["slot_index"]): r["upid"] if r["upid"] in active else None
            for r in _all(conn, "select slot_code, slot_index, upid from lineups where team_abbr = %s and game_date = %s",
                          (team, prev["d"]))}


# ------------------------------------------------------------------ reading

def get_lineup(conn, team, game_date):
    """{"team", "game_date", "version", "slots": [...]} with every slot in the grid. A day nobody has
    saved yet shows a copy of the latest earlier day (version 0, nothing stored)."""
    types = _slot_types(conn, game_date.year)
    stored = {(r["slot_code"], r["slot_index"]): r for r in _all(
        conn, "select * from lineups where team_abbr = %s and game_date = %s", (team, game_date))}
    if stored:
        cells = {k: r["upid"] for k, r in stored.items()}
    else:
        cells = _copy_forward(conn, team, game_date) or {}
    slots = []
    for code, idx in _grid(types):
        r = stored.get((code, idx))
        slots.append({"slot_code": code, "slot_index": idx, "upid": cells.get((code, idx)),
                      "locked_at": r["locked_at"] if r else None,
                      "set_by": r["set_by"] if r else "carryover"})
    return {"team": team, "game_date": game_date, "version": _version(conn, team, game_date), "slots": slots}


def _eligibility(conn, season, game_date, upids):
    """{upid: set(positions usable on game_date)}"""
    out = {u: set() for u in upids}
    for r in _all(conn, """select upid, position from position_eligibility
                            where season = %s and upid = any(%s) and (earned_on is null or earned_on <= %s)""",
                  (season, list(upids), game_date)):
        out[r["upid"]].add(r["position"])
    return out


# ------------------------------------------------------------------ validation

def validate(conn, team, game_date, lineup):
    """The one validation function. Raises LineupError listing every problem; returns the lineup as
    {(slot_code, slot_index): upid} (empty cells omitted) when it is fine.

    Checks: every slot exists, no player twice, every player is on the team with roster slot 'active',
    every player fits his slot through position_eligibility, and nothing locked was changed."""
    types = {t["code"]: t for t in _slot_types(conn, game_date.year)}
    problems, cells, seen = [], {}, {}
    for entry in lineup:
        code, idx, upid = entry["slot_code"], entry["slot_index"], entry.get("upid")
        t = types.get(code)
        if t is None or not 1 <= idx <= t["count"]:
            problems.append(f"There is no {code} slot {idx}.")
            continue
        if (code, idx) in cells or (code, idx) in seen:
            problems.append(f"{code} slot {idx} is listed twice.")
            continue
        seen[(code, idx)] = True
        if upid is not None:
            cells[(code, idx)] = upid
    locked_rows = _all(conn, """select slot_code, slot_index, upid from lineups
                                 where team_abbr = %s and game_date = %s and locked_at is not null""", (team, game_date))
    names = {r["upid"]: r["name"] for r in _all(
        conn, "select upid, name from players where upid = any(%s)",
        (list(set(cells.values()) | {r["upid"] for r in locked_rows}),))}
    label = lambda u: names.get(u, u)  # noqa: E731

    where = {}
    for (code, idx), upid in cells.items():
        where.setdefault(upid, []).append(code)
    for upid, slots in where.items():
        if len(slots) > 1:
            problems.append(f"{label(upid)} is in more than one slot ({', '.join(slots)}).")

    status = {r["upid"]: r for r in _all(conn, """
        select p.upid, c.team_abbr, rs.slot from players p
          left join contracts c on c.upid = p.upid and c.valid_to is null
          left join roster_slots rs on rs.upid = p.upid
         where p.upid = any(%s)""", (list(where),))}
    elig = _eligibility(conn, game_date.year, game_date, list(where))
    for upid in where:
        s = status.get(upid)
        if s is None:
            problems.append(f"Unknown player {upid}.")
        elif s["team_abbr"] != team:
            problems.append(f"{label(upid)} is not on {team}.")
        elif s["slot"] != "active":
            problems.append(f"{label(upid)} is {'on the IL' if s['slot'] == 'il' else 'on NA' if s['slot'] == 'na' else 'a farm player'}"
                            " and can't be in the lineup.")
    for (code, idx), upid in cells.items():
        s = status.get(upid)
        if s is None or s["team_abbr"] != team or s["slot"] != "active":
            continue
        t = types[code]
        # the bench takes any active player; every other slot goes through position_eligibility
        if t["is_starting"] and not (set(t["accepts"]) & elig[upid]):
            have = ", ".join(sorted(elig[upid])) or "no positions yet"
            problems.append(f"{label(upid)} can't play {code} (eligible at {have}).")

    for r in locked_rows:
        if cells.get((r["slot_code"], r["slot_index"])) != r["upid"]:
            problems.append(f"{label(r['upid'])} is locked in {r['slot_code']} {r['slot_index']}: his game has started.")
    if problems:
        raise LineupError(problems)
    return cells


# ------------------------------------------------------------------ saving

def save_lineup(conn, team, game_date, lineup, version, set_by, now):
    """Save a team's whole day in one transaction. `version` is the one the caller loaded
    (get_lineup()['version']); if the lineup moved on since, raise LineupConflict(latest)."""
    _team_check(conn, team)
    with conn.transaction():
        _lock_team(conn, team)
        today = _today(now)
        if game_date < today or game_date > today + timedelta(days=FUTURE_DAYS):
            raise LineupError(f"Lineups can be set from today up to {FUTURE_DAYS} days ahead.")
        lock_started_games(conn, now, team=team)   # so a game that just started is locked even if the job hasn't run
        latest = get_lineup(conn, team, game_date)
        if latest["version"] != version:
            raise LineupConflict(latest)
        cells = validate(conn, team, game_date, lineup)
        locked = {(s["slot_code"], s["slot_index"]): s["locked_at"] for s in latest["slots"] if s["locked_at"]}
        _write_cells(conn, team, game_date, _slot_types(conn, game_date.year), cells, set_by, locked)
        _bump(conn, team, game_date, now)
    return get_lineup(conn, team, game_date)


# ------------------------------------------------------------------ nightly and minute jobs

def carryover(conn, game_date, now):
    """The 12:00 AM ET job: give every team without a lineup on `game_date` a copy of its latest
    earlier lineup (set_by 'carryover'), dropping anyone no longer active. A day that already has
    rows (a manager's edit, or an earlier run of this job) is never touched, so it is safe to run twice."""
    copied = skipped = 0
    types = _slot_types(conn, game_date.year)
    for t in _all(conn, "select abbr from teams where active order by abbr"):
        team = t["abbr"]
        with conn.transaction():
            _lock_team(conn, team)
            if _one(conn, "select 1 as x from lineups where team_abbr = %s and game_date = %s limit 1", (team, game_date)):
                skipped += 1
                continue
            cells = _copy_forward(conn, team, game_date)
            if cells is None:
                skipped += 1
                continue
            _write_cells(conn, team, game_date, types, cells, "carryover")
            _bump(conn, team, game_date, now)
            copied += 1
    return {"copied": copied, "skipped": skipped}


def lock_started_games(conn, now, team=None):
    """Set locked_at on lineup players whose game has started (the earlier start if he has two games);
    clear it where the game is postponed. Bumps the version of every lineup it changes."""
    since = _today(now) - timedelta(days=1)
    game = """select 1 from players p join mlb_games g on g.game_date = l.game_date
                and p.mlb_team in (g.home_team, g.away_team) and g.status <> 'postponed' and g.start_time <= %s
               where p.upid = l.upid"""
    changed = set()
    for r in _all(conn, f"""update lineups l set locked_at = null
                             where l.locked_at is not null and l.game_date >= %s and (%s::text is null or l.team_abbr = %s)
                               and not exists ({game}) returning l.team_abbr, l.game_date""", (since, team, team, now)):
        changed.add((r["team_abbr"], r["game_date"]))
    for r in _all(conn, """
        with started as (
          select l.team_abbr, l.game_date, l.slot_code, l.slot_index, min(g.start_time) as st
            from lineups l join players p on p.upid = l.upid
            join mlb_games g on g.game_date = l.game_date and p.mlb_team in (g.home_team, g.away_team)
                            and g.status <> 'postponed' and g.start_time <= %s
           where l.game_date >= %s and (%s::text is null or l.team_abbr = %s)
           group by 1, 2, 3, 4)
        update lineups l set locked_at = s.st from started s
         where (l.team_abbr, l.game_date, l.slot_code, l.slot_index) = (s.team_abbr, s.game_date, s.slot_code, s.slot_index)
           and l.locked_at is distinct from s.st
        returning l.team_abbr, l.game_date""", (now, since, team, team)):
        changed.add((r["team_abbr"], r["game_date"]))
    for t, d in sorted(changed):
        _bump(conn, t, d, now)
    return len(changed)


def refresh_games(conn, schedule_rows, now):
    """Upsert the day's schedule (rows from mlb_api.fetch_schedule), then re-run the locks so a
    postponement unlocks right away."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """insert into mlb_games (game_pk, game_date, start_time, home_team, away_team, status,
                                          probable_home_sp, probable_away_sp)
                   values (%(game_pk)s, %(game_date)s, %(start_time)s, %(home_team)s, %(away_team)s, %(status)s,
                           %(probable_home_sp)s, %(probable_away_sp)s)
                   on conflict (game_pk) do update set game_date = excluded.game_date, start_time = excluded.start_time,
                     home_team = excluded.home_team, away_team = excluded.away_team, status = excluded.status,
                     probable_home_sp = excluded.probable_home_sp, probable_away_sp = excluded.probable_away_sp""",
                [{"probable_home_sp": None, "probable_away_sp": None} | dict(r) for r in schedule_rows])
        lock_started_games(conn, now)
    return len(schedule_rows)


# ------------------------------------------------------------------ eligibility

def _is_pitcher_row(player) -> bool:
    return actions.classify(player["positions"], player.get("mlb_primary_position"))["pitcher"]


def _primary_positions(player, mlb_primary) -> set:
    """A player's primary position(s) as eligibility codes. Pitcher rows only get SP / RP and batter
    rows only get batter positions, so a two-way player's two rows never share a side."""
    pitcher = _is_pitcher_row(player)
    out = set()
    for raw in (player["mlb_primary_position"], mlb_primary, player["positions"]):
        for tok in actions._tokens(raw):
            if tok in ("TWP", "UTIL", "UT", "U"):
                tok = "SP" if pitcher else "DH"
            out.add(_MAP.get(tok, ""))
        out -= {""}
        out &= set(PITCHER_POSITIONS if pitcher else BATTER_POSITIONS)
        if out:
            return out
    return out


def refresh_eligibility(conn, season, fielding_rows, now, *, source="this_season", through=None):
    """Nightly job. `fielding_rows` is mlb_api.fetch_fielding output: {mlb_id, position, games, starts, relief}.

    1. Every owned player gets his primary position for `season` (source 'primary'), if he has none.
    2. Positions with `eligibility_games` or more games (SP: starts, RP: relief appearances) are added.
       LF / CF / RF / OF games add up to OF; CF alone earns CF.
       source='last_season': pass last year's totals before the season (no earned_on, usable from day one).
       source='this_season': rows are totals through `through` (default: yesterday ET); a new position is
       usable from the day after, so earned_on = through + 1. Nothing is ever removed.
    A player with no games can still have a primary position; one the API does not know is skipped."""
    if source not in ("last_season", "this_season"):
        raise ValueError("source must be 'last_season' or 'this_season'")
    need = int(_setting(conn, "eligibility_games"))
    earned_on = None if source == "last_season" else (through or _today(now) - timedelta(days=1)) + timedelta(days=1)

    totals = {}   # (mlb_id, position) -> games
    for r in fielding_rows:
        pos = r.get("position")
        if pos == "P":
            for p, n in (("SP", r.get("starts") or 0), ("RP", r.get("relief") or 0)):
                totals[(r["mlb_id"], p)] = totals.get((r["mlb_id"], p), 0) + n
        elif pos in ("LF", "CF", "RF", "OF"):
            totals[(r["mlb_id"], "OF")] = totals.get((r["mlb_id"], "OF"), 0) + (r.get("games") or 0)
            if pos == "CF":
                totals[(r["mlb_id"], "CF")] = totals.get((r["mlb_id"], "CF"), 0) + (r.get("games") or 0)
        elif pos in ("C", "1B", "2B", "3B", "SS"):
            totals[(r["mlb_id"], pos)] = totals.get((r["mlb_id"], pos), 0) + (r.get("games") or 0)

    added = 0
    with conn.transaction():
        with conn.cursor() as cur:
            owned = _all(conn, """select p.upid, p.positions, p.mlb_primary_position, s.primary_position
                                    from players p join contracts c on c.upid = p.upid and c.valid_to is null
                                    left join mlb_roster_status s on s.upid = p.upid""")
            for p in owned:
                for pos in _primary_positions(p, p["primary_position"]):
                    added += cur.execute("""insert into position_eligibility (upid, season, position, source)
                                            values (%s, %s, %s, 'primary') on conflict do nothing""",
                                         (p["upid"], season, pos)).rowcount
            by_mlb = {}
            for p in _all(conn, "select upid, positions, mlb_primary_position, mlb_id from players where mlb_id is not null"):
                by_mlb.setdefault(p["mlb_id"], []).append(p)
            for (mlb_id, pos), games in totals.items():
                for p in by_mlb.get(mlb_id, []):
                    if _is_pitcher_row(p) != (pos in PITCHER_POSITIONS):
                        continue   # a two-way player's other row
                    if games >= need:
                        added += cur.execute(
                            """insert into position_eligibility (upid, season, position, source, games, earned_on)
                               values (%s, %s, %s, %s, %s, %s)
                               on conflict (upid, season, position) do update
                                 set games = greatest(position_eligibility.games, excluded.games)""",
                            (p["upid"], season, pos, source, games, earned_on)).rowcount
    return {"changed": added}


def refresh_mlb_status(conn, rows, now):
    """Store where MLB lists each player (mlb_api.fetch_player_positions rows) for every player with that MLB ID."""
    n = 0
    with conn.transaction():
        for r in rows:
            n += _exec(conn, """insert into mlb_roster_status (upid, status, mlb_status_code, primary_position, updated_at)
                                select upid, %s, %s, %s, %s from players where mlb_id = %s
                                on conflict (upid) do update set status = excluded.status,
                                  mlb_status_code = excluded.mlb_status_code,
                                  primary_position = coalesce(excluded.primary_position, mlb_roster_status.primary_position),
                                  updated_at = excluded.updated_at""",
                       (r["status"], r.get("status_code"), r.get("primary_position"), now, r["mlb_id"]))
    return n


def admin_add_eligibility(conn, upid, season, position, reason, by):
    """An admin adds a position by hand (source 'admin'). A reason is required."""
    if position not in BATTER_POSITIONS + PITCHER_POSITIONS:
        raise LineupError(f"{position!r} is not a position.")
    if not (reason or "").strip():
        raise LineupError("A reason is required.")
    with conn.transaction():
        p = _one(conn, "select upid, name, positions, mlb_primary_position from players where upid = %s", (upid,))
        if p is None:
            raise LineupError(f"unknown player {upid!r}")
        if _is_pitcher_row(p) != (position in PITCHER_POSITIONS):
            raise LineupError(f"{p['name']} is a {'pitcher' if _is_pitcher_row(p) else 'batter'} and can't be given {position}.")
        n = _exec(conn, """insert into position_eligibility (upid, season, position, source)
                           values (%s, %s, %s, 'admin') on conflict do nothing""", (upid, season, position))
        if n == 0:
            return {"message": f"{p['name']} is already eligible at {position}."}
        msg = f"{p['name']} given {position} eligibility by {by}: {reason.strip()}"
        actions._event(conn, upid, "eligibility_add", msg, team=None, season=season, by=by,
                       ts=datetime.now(ET))
    return {"message": msg}


# ------------------------------------------------------------------ optimize

_BIG = 10 ** 9


def _assign(cost):
    """Min-cost assignment of n rows to m >= n columns (Hungarian method). Returns the column per row."""
    n, m = len(cost), len(cost[0])
    inf = float("inf")
    u, v, p, way = [0] * (n + 1), [0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    res = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            res[p[j] - 1] = j - 1
    return res


def optimize(conn, team, game_date):
    """A proposed lineup (list of slot dicts, nothing saved). Locked players stay where they are. For the rest:
    first start as many players with a game that day as possible, then fill every other starting slot,
    then prefer better-ranked players (lower players.rank; unranked last). Whoever is left goes to the bench."""
    types = _slot_types(conn, game_date.year)
    current = get_lineup(conn, team, game_date)
    cells = {(s["slot_code"], s["slot_index"]): s["upid"] for s in current["slots"] if s["locked_at"]}
    fixed = set(cells.values())
    players = [p for p in _all(conn, """
        select p.upid, p.name, p.rank,
               exists (select 1 from mlb_games g where g.game_date = %s and g.status <> 'postponed'
                          and p.mlb_team in (g.home_team, g.away_team)) as has_game
          from players p where p.upid = any(%s)""", (game_date, list(_active_upids(conn, team) - fixed)))]
    players.sort(key=lambda p: (p["rank"] is None, p["rank"] or 0, p["name"]))
    elig = _eligibility(conn, game_date.year, game_date, [p["upid"] for p in players])
    by_code = {t["code"]: t for t in types}

    slots = [(c, i) for c, i in _grid(types) if by_code[c]["is_starting"] and (c, i) not in cells]
    if slots and players:
        cost = []
        for c, i in slots:
            row = []
            for k, p in enumerate(players):
                if set(by_code[c]["accepts"]) & elig[p["upid"]]:
                    row.append(-(1_000_000 * p["has_game"] + 1_000 + (len(players) - k)))
                else:
                    row.append(_BIG)
            cost.append(row + [0] * len(slots))   # one "leave it empty" column per slot
        for k, col in enumerate(_assign(cost)):
            if col is not None and col < len(players) and cost[k][col] != _BIG:
                cells[slots[k]] = players[col]["upid"]
    placed = set(cells.values())
    bench = [p["upid"] for p in players if p["upid"] not in placed]
    for (c, i), upid in zip([g for g in _grid(types) if g[0] == "BN" and g not in cells], bench):
        cells[(c, i)] = upid
    return [{"slot_code": c, "slot_index": i, "upid": cells.get((c, i))} for c, i in _grid(types)]


# ------------------------------------------------------------------ roster moves from the team page

def _drop_from_lineups(conn, team, upids, now):
    """A player who left the active slot leaves today's and every future lineup."""
    changed = set()
    for r in _all(conn, """update lineups set upid = null, locked_at = null
                            where team_abbr = %s and game_date >= %s and upid = any(%s)
                            returning game_date""", (team, _today(now), list(upids))):
        changed.add(r["game_date"])
    for d in sorted(changed):
        _bump(conn, team, d, now)


def _mlb_status(conn, upid):
    r = _one(conn, "select s.status, p.name from players p left join mlb_roster_status s on s.upid = p.upid where p.upid = %s",
             (upid,))
    if r is None:
        raise LineupError(f"unknown player {upid!r}")
    return r["name"], r["status"]


def _slot_room(conn, team, slot, setting):
    n = _one(conn, """select count(*) as n from contracts c join roster_slots rs on rs.upid = c.upid and rs.slot = %s
                       where c.team_abbr = %s and c.valid_to is null""", (slot, team))["n"]
    if n >= int(_setting(conn, setting)):
        raise LineupError(f"{team} already has {n} on {slot.upper()}.")


def _move(conn, team, upid, slot, by, now):
    r = actions.move_slot(conn, upid, slot, created_by=by, team=team, now=now)
    if slot != "active":
        _drop_from_lineups(conn, team, [upid], now)
    return r


def move_to_il(conn, team, upid, by, now):
    """Only when MLB lists him on the IL (mlb_roster_status), and only while the team has an IL spot."""
    with conn.transaction():
        _lock_team(conn, team)
        name, status = _mlb_status(conn, upid)
        if status != "il":
            raise LineupError(f"{name} can go on the IL only when MLB lists him on the IL"
                              + ("" if status else " (MLB status unknown)") + ".")
        _slot_room(conn, team, "il", "il_slots")
        return _move(conn, team, upid, "il", by, now)


def move_to_na(conn, team, upid, by, now):
    """Only for a player MLB lists in the minors (not on an MLB active roster), while the team has an NA spot."""
    with conn.transaction():
        _lock_team(conn, team)
        name, status = _mlb_status(conn, upid)
        if status != "minors":
            raise LineupError(f"{name} can go on NA only when he is not on an MLB active roster"
                              + ("" if status else " (MLB status unknown)") + ".")
        _slot_room(conn, team, "na", "na_slots")
        return _move(conn, team, upid, "na", by, now)


def activate(conn, team, upid, by, now):
    """Back to active from IL or NA. Blocked, with the reason, if it would break a roster maximum (Spec 03)."""
    with conn.transaction():
        _lock_team(conn, team)
        return _move(conn, team, upid, "active", by, now)


def release(conn, team, upid, by, now, season=None):
    """Spec 03's release. A two-way player is released as a package: both rows."""
    with conn.transaction():
        _lock_team(conn, team)
        twin = _one(conn, "select raw->>'2wayplayer' as twin from players where upid = %s", (upid,))
        ids = [upid]
        if twin and twin["twin"] and _one(conn, "select 1 as x from players where upid = %s", (twin["twin"],)):
            ids.append(twin["twin"])
        results = [actions.release(conn, u, created_by=by, team=team, season=season, now=now) for u in ids]
        _drop_from_lineups(conn, team, ids, now)
        return {"released": ids, "message": " ".join(r["message"] for r in results)}
