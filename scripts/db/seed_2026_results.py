"""2026 final standings (Yahoo, 10/28) -> team_seasons.final_rank / bracket.

Source of truth: specs/03_rosters_contracts_wizbucks.md, "2026 final standings".
The import truncates team_seasons on every run, so import_json.py applies this
dict to the rows it builds (apply_to_bundle); `seed(conn)` does the same as an
upsert against a live database. This dict is the one place the facts live.

Brackets (confirmed 10/28): finish 1-4 championship, 5-8 consolation,
9-12 elimination.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SEASON = 2026

# abbr -> (final_rank, bracket)
FINAL_STANDINGS_2026: dict[str, tuple[int, str]] = {
    "HAM": (1, "championship"),
    "WIZ": (2, "championship"),
    "LFB": (3, "championship"),
    "DMN": (4, "championship"),
    "SAD": (5, "consolation"),
    "JEP": (6, "consolation"),
    "B2J": (7, "consolation"),
    "TBB": (8, "consolation"),
    "CFL": (9, "elimination"),
    "WAR": (10, "elimination"),
    "RV": (11, "elimination"),
    "DRO": (12, "elimination"),
}


def apply_to_bundle(b) -> None:
    """Set final_rank + bracket on the 2026 team_seasons rows of an import Bundle."""
    from scripts.db.import_json import TABLE_COLUMNS  # local: avoids a circular import

    cols = TABLE_COLUMNS["team_seasons"]
    i_team, i_season = cols.index("team_abbr"), cols.index("season")
    i_rank, i_bracket = cols.index("final_rank"), cols.index("bracket")
    seen = set()
    rows = b.rows["team_seasons"]
    for n, row in enumerate(rows):
        abbr = row[i_team]
        if row[i_season] == SEASON and abbr in FINAL_STANDINGS_2026:
            r = list(row)
            r[i_rank], r[i_bracket] = FINAL_STANDINGS_2026[abbr]
            rows[n] = tuple(r)
            seen.add(abbr)
    for abbr, (rank, bracket) in FINAL_STANDINGS_2026.items():
        if abbr not in seen:
            b.add("team_seasons", {"team_abbr": abbr, "season": SEASON, "final_rank": rank, "bracket": bracket})


def seed(conn) -> int:
    """Upsert final_rank/bracket for the 12 teams on a live database."""
    n = 0
    with conn.cursor() as cur:
        for abbr, (rank, bracket) in FINAL_STANDINGS_2026.items():
            cur.execute(
                """insert into team_seasons (team_abbr, season, final_rank, bracket) values (%s, %s, %s, %s)
                   on conflict (team_abbr, season) do update
                     set final_rank = excluded.final_rank, bracket = excluded.bracket""",
                (abbr, SEASON, rank, bracket),
            )
            n += cur.rowcount
    return n


if __name__ == "__main__":
    from scripts.db.common import connect

    with connect() as conn:
        with conn.transaction():
            print(f"upserted {seed(conn)} team_seasons rows for {SEASON}")
