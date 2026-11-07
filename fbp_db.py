"""App-side Postgres access for the 2027 rebuild (specs/01_neon_foundation.md).

The bot and the API are one long-running process on Railway, so this module keeps
a small psycopg 3 connection pool over the **pooled** DATABASE_URL and hands out
connections with autocommit on. `auction/service.py` (and every other 2027
service) opens its own `with conn.transaction():`, and the module docstring there
requires an idle connection so that transaction is the outermost one and commits
on exit -- which is exactly what an autocommit connection from this pool is.

Nothing here reads or writes league data. The feature flags below decide whether
the app uses the Postgres path at all; every one of them is **off by default**,
so an unset environment keeps the 2026 JSON behaviour.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Mapping

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:  # same pattern as scripts/db/*.py
    sys.path.insert(0, str(REPO_ROOT))

# --------------------------------------------------------------------- flags

AUCTION_FLAG = "FBP_AUCTION_DB"

_TRUTHY = {"1", "true", "yes", "on"}


def flag_enabled(name: str) -> bool:
    """A feature flag is on only for an explicit truthy value. Unset means off."""
    return (os.getenv(name) or "").strip().lower() in _TRUTHY


def auction_enabled() -> bool:
    """True when the Spec 02 Postgres auction path is switched on (FBP_AUCTION_DB).

    Off by default: the JSON auction (`auction_manager.py`) stays the live path
    until Zach flips this, per specs/BUILD_PLAN_2027.md step 3.
    """
    return flag_enabled(AUCTION_FLAG)


# ---------------------------------------------------------------------- pool

_pool = None
_pool_lock = threading.Lock()


def pool():
    """The process-wide connection pool, built on first use.

    Built lazily on purpose: with every flag off the app never needs a database,
    so an unset DATABASE_URL must not stop the bot from starting.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool

            from scripts.db.common import database_url, load_env_files

            load_env_files()
            _pool = ConnectionPool(
                database_url(),
                min_size=1,
                max_size=4,
                kwargs={"autocommit": True},
                open=True,
                name="fbp-app",
            )
        return _pool


@contextmanager
def connection() -> Iterator:
    """An idle, autocommit connection from the pool.

    Use it as the `conn` argument to the 2027 services:

        with fbp_db.connection() as conn:
            board = auction.service.get_board(conn, now, calendar)
    """
    with pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """Close the pool (process shutdown, and between tests)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


# ------------------------------------------------------- auction config feeds

_SEASON_DATES = REPO_ROOT / "config" / "season_dates.json"
_STANDINGS = REPO_ROOT / "data" / "standings.json"


def auction_calendar() -> Mapping:
    """The calendar `auction.rules.phase_for` needs: start, all_star_break_start,
    restart, playoffs_start, from config/season_dates.json.

    NOTE for go-live (open question 23): `start` must be the **Monday of the
    pre-season auction week**, not Opening Day. The 2027 value is Zach's to set.
    """
    with _SEASON_DATES.open(encoding="utf-8") as fh:
        cal = json.load(fh).get("auction")
    if not cal:
        raise RuntimeError("config/season_dates.json has no 'auction' calendar")
    return cal


def priority_order() -> List[str]:
    """Bid priority, worst standings first.

    STOPGAP, and a go-live blocker. Spec 02 ("Priority order") says 2027 priority
    comes from FBP's own standings, and from the division draft seeding until
    those exist -- neither of which is in the data yet (there is no division-draft
    seeding file, and the FBP scoring engine is Spec 08, unwritten). So this keeps
    the order the league actually played 2026 under: data/standings.json, worst
    rank first. It changes no rule by itself, but it is the 2026 Yahoo-derived
    order that Spec 02 bug 8 says we are leaving.

    Zach decides the real 2027 source before the flag is flipped. Until then an
    admin can override any week with `auction.service.admin_set_priority`, which
    is the documented escape hatch.
    """
    with _STANDINGS.open(encoding="utf-8") as fh:
        rows = json.load(fh).get("standings") or []
    ranked = [r for r in rows if r.get("team") and r.get("rank") is not None]
    ranked.sort(key=lambda r: r["rank"], reverse=True)  # worst first
    return [str(r["team"]) for r in ranked]
