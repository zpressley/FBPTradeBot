"""Shared helpers for the database scripts (migrate, import, reconcile).

Nothing in here reads or writes data/*.json; it only knows how to connect
and how to normalize the few value shapes the importer and reconcile share.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def load_env_files() -> None:
    """Load .env then .env.local from the repo root without overriding real env vars.

    `neon deploy` / `neon env pull` write DATABASE_URL and DATABASE_URL_UNPOOLED to
    .env.local (or .env if it exists); both files are git-ignored.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is in requirements.txt; tolerate a bare install
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for name in (".env", ".env.local"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            load_dotenv(path, override=False)


def database_url(explicit: str | None = None) -> str:
    load_env_files()
    url = explicit or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. Run `neon env pull` (writes .env.local) or put the pooled "
            "Neon connection string in .env, or pass --database-url. See db/README.md."
        )
    return url


def direct_database_url(explicit: str | None = None) -> str:
    """Direct (non-pooled) string for migrations: DATABASE_URL_DIRECT, Neon's
    DATABASE_URL_UNPOOLED, then DATABASE_URL.

    When DATABASE_URL comes from the real environment (a test pointing at a local
    database, CI) and no direct variable does, the env files are not consulted
    for a direct string: otherwise .env.local could silently send migrations to
    a different server than the imports.
    """
    if explicit:
        return explicit
    real = os.environ
    if "DATABASE_URL" in real and "DATABASE_URL_DIRECT" not in real and "DATABASE_URL_UNPOOLED" not in real:
        return real["DATABASE_URL"]
    load_env_files()
    return (os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL_UNPOOLED")
            or database_url())


def connect(url: str | None = None) -> psycopg.Connection:
    """Open a connection with autocommit off. Callers manage transactions."""
    import psycopg  # lazy: the Top 100 scraper imports this module without psycopg

    return psycopg.connect(database_url(url), autocommit=False)


# ----------------------------------------------------------------- normalizing

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """lowercase + accents stripped + punctuation removed + single spaces.

    'José Rodríguez Jr.' -> 'jose rodriguez jr'. Used for player_aliases and
    for matching Top 100 names when an MLB ID is missing.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _PUNCT.sub(" ", s.lower())
    return _WS.sub(" ", s).strip()


def parse_ts(value, *, assume_tz=ET) -> datetime | None:
    """Parse the timestamp shapes found in data/*.json into an aware datetime.

    Naive values are treated as America/New_York (spec 01, conventions).
    Returns None for blanks and unparseable values; the importer reports those.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = None
            for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(timezone.utc)


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def to_int(value) -> int | None:
    if value is None or value == "" or value == "None":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        try:
            return int(float(str(value).strip()))
        except ValueError:
            return None


def to_num(value):
    if value is None or value == "" or value == "None":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def to_bool(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None


def blank(value) -> bool:
    """True for the JSON's assorted 'nothing here' values."""
    return value is None or (isinstance(value, str) and value.strip() in ("", "None", "null"))


# Contract value mapping from spec 01 ("Value mapping from combined_players.json")
CONTRACT_TYPE_MAP = {
    "Keeper Contract": "keeper",
    "Purchased Contract": "purchased",
    "Blue Chip Contract": "blue_chip",
    "Development Cont.": "development",
}

CONTRACT_TIER_MAP = {
    "P": "P",
    "TC R": "TC-R",
    "TC BC-1": "TC-BC-1",
    "TC BC-2": "TC-BC-2",
    "TC 1": "TC-1",
    "TC 2": "TC-2",
    "VC 1": "VC-1",
    "VC 2": "VC-2",
    "FC 1": "FC-1",
    "FC 2": "FC-2",
}
