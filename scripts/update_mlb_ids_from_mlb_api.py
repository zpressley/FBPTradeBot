#!/usr/bin/env python3
"""Update mlb_id_cache.json and combined_players.json using MLB Stats API.

This script is intended to be run manually when you want to expand MLB ID
coverage beyond what already exists in data/mlb_id_cache.json.

High-level flow
---------------
1) Load:
   - data/combined_players.json
   - data/upid_database.json (by_upid)
   - data/mlb_id_cache.json
   - data/mlb_team_map.json (for team alias -> official mapping)

2) For each player in combined_players.json without an mlb_id:
   - Look up their UPID in upid_database.by_upid to get:
     * canonical name
     * alt_names
     * team
   - Build a list of candidate names (base + alt + combined_players name).
   - Determine a canonical team code using mlb_team_map.json.
   - For each candidate name, query MLB Stats API:
       https://statsapi.mlb.com/api/v1/people/search?names=<name>
   - Filter results by:
       * normalized name equality (fullName vs candidate name), and
       * canonical team equality when currentTeam.name can be mapped.
   - If there is EXACTLY one strong match, accept that player's id as mlb_id.

3) For each accepted match:
   - Add/update data/mlb_id_cache.json at key upid: {"name": name, "mlb_id": id}.
   - Set mlb_id on the corresponding combined_players record.

4) Write backups and updated files:
   - data/combined_players_mlb_api_backup.json
   - data/mlb_id_cache_backup.json
   - Overwrite data/combined_players.json and data/mlb_id_cache.json in place.

Safety / Notes
--------------
- The script is conservative: it only writes mlb_id when it finds a single,
  unambiguous Stats API match.
- It uses mlb_team_map.json to normalize team names when possible; if it
  cannot determine a canonical team, it falls back to name-only matching and
  still requires uniqueness.
- Rate limiting is controlled via --sleep between API calls.

Usage
-----
From the repo root:

    python3 scripts/update_mlb_ids_from_mlb_api.py            # full run
    python3 scripts/update_mlb_ids_from_mlb_api.py --limit 200
    python3 scripts/update_mlb_ids_from_mlb_api.py --dry-run

"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import urllib.parse
import urllib.request

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

STATS_API_SEARCH_URL = "https://statsapi.mlb.com/api/v1/people/search"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def build_team_alias_map(team_map: dict) -> Dict[str, str]:
    """Return alias -> official team code mapping from mlb_team_map.json."""

    alias_to_official: Dict[str, str] = {}
    for official, info in (team_map.get("official") or {}).items():
        official_u = official.upper()
        alias_to_official[official_u] = official_u
        for a in info.get("aliases", []):
            alias_to_official[str(a).upper()] = official_u
    return alias_to_official


def canon_team(code: Optional[str], alias_map: Dict[str, str]) -> str:
    if not code:
        return ""
    c = str(code).upper().strip()
    return alias_map.get(c, c)


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    return _NON_ALNUM.sub("", s)


def mlb_search(name: str) -> Optional[dict]:
    """Call MLB Stats API search endpoint for a given name.

    Returns the decoded JSON or None on HTTP error.
    """

    qs = urllib.parse.urlencode({"names": name})
    url = f"{STATS_API_SEARCH_URL}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  MLB API error for name='{name}': {exc}")
        return None


def choose_mlb_id_for_player(
    upid: str,
    upid_rec: dict,
    combined_rec: dict,
    alias_map: Dict[str, str],
    sleep: float,
) -> Optional[int]:
    """Attempt to find a unique MLB personId for this player.

    Uses:
      - upid_rec["name"] and upid_rec["alt_names"]
      - upid_rec["team"] (canonicalized)
      - combined_rec["team"] as a fallback for team

    Returns an int mlb_id or None.
    """

    base_name = upid_rec.get("name") or upid_rec.get("Name") or ""
    alt_names = upid_rec.get("alt_names") or []
    if not isinstance(alt_names, list):
        alt_names = []

    cp_name = combined_rec.get("name") or ""

    # Candidate names (normalized later)
    name_candidates: List[str] = []
    for n in [base_name, cp_name, *alt_names]:
        if n and n not in name_candidates:
            name_candidates.append(n)

    if not name_candidates:
        return None

    # Determine canonical team code
    team_code = (
        upid_rec.get("team")
        or upid_rec.get("Team")
        or combined_rec.get("team")
        or ""
    )
    canonical_team = canon_team(team_code, alias_map)

    # Precompute normalized candidate names
    norm_candidates = {norm_name(n): n for n in name_candidates if norm_name(n)}
    if not norm_candidates:
        return None

    # Aggregate candidate MLB IDs from all name searches
    accepted_ids: Set[int] = set()

    for raw_name in name_candidates:
        if not raw_name:
            continue

        search_json = mlb_search(raw_name)
        if not search_json:
            continue

        people = search_json.get("people") or []
        if not people:
            continue

        for person in people:
            try:
                pid = person.get("id")
                full = person.get("fullName") or ""
            except AttributeError:
                continue

            if not isinstance(pid, int):
                continue

            # Name check: require normalized fullName to be one of our candidates.
            full_norm = norm_name(full)
            if full_norm not in norm_candidates:
                continue

            # Team check (when we have both sides)
            if canonical_team:
                current_team = (person.get("currentTeam") or {}).get("name") or ""
                if current_team:
                    current_team_canon = canon_team(current_team, alias_map)
                    if current_team_canon and current_team_canon != canonical_team:
                        # Different MLB team than expected -> skip
                        continue

            accepted_ids.add(pid)

        # Respect simple rate limit between calls
        if sleep > 0:
            time.sleep(sleep)

    if len(accepted_ids) == 1:
        mlb_id = next(iter(accepted_ids))
        print(f"  ✅ UPID {upid}: resolved MLB id {mlb_id} from names={name_candidates!r} team={canonical_team!r}")
        return mlb_id

    if accepted_ids:
        print(
            f"  ⚠️  UPID {upid}: multiple MLB candidates {sorted(accepted_ids)} "
            f"for names={name_candidates!r}, team={canonical_team!r}; skipping",
        )
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill mlb_id via MLB Stats API using UPID + name/team.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of players to process (0 = no limit)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.3,
        help="Seconds to sleep between MLB API calls (default: 0.3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write any files; just log what would be updated.",
    )
    args = parser.parse_args()

    combined: List[dict] = load_json(DATA_DIR / "combined_players.json")
    upid_db: dict = load_json(DATA_DIR / "upid_database.json")
    mlb_cache: dict = load_json(DATA_DIR / "mlb_id_cache.json")
    team_map: dict = load_json(DATA_DIR / "mlb_team_map.json")

    by_upid: dict = upid_db.get("by_upid") or {}
    alias_map = build_team_alias_map(team_map)

    print(f"Loaded {len(combined)} combined_players records")
    print(f"Loaded {len(by_upid)} UPID records from upid_database")
    print(f"Loaded {len(mlb_cache)} mlb_id_cache entries")

    # Select players missing mlb_id and having a valid UPID entry
    todo: List[Tuple[str, dict, dict]] = []  # (upid, upid_rec, combined_rec)

    for p in combined:
        mlb_id_val = p.get("mlb_id")
        if isinstance(mlb_id_val, int) and mlb_id_val > 0:
            continue

        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue
        upid_rec = by_upid.get(upid)
        if not upid_rec:
            continue

        todo.append((upid, upid_rec, p))

    print(f"Players missing mlb_id but with UPID record: {len(todo)}")

    if args.limit and args.limit > 0:
        todo = todo[: args.limit]
        print(f"Processing first {len(todo)} players due to --limit")

    updated_cache = 0
    updated_combined = 0

    for idx, (upid, urec, crec) in enumerate(todo, start=1):
        print(f"[{idx}/{len(todo)}] Looking up UPID {upid} ({urec.get('name')})...")

        mlb_id = choose_mlb_id_for_player(
            upid=upid,
            upid_rec=urec,
            combined_rec=crec,
            alias_map=alias_map,
            sleep=args.sleep,
        )
        if mlb_id is None:
            continue

        # Update mlb_id_cache
        cache_entry = mlb_cache.get(upid)
        if cache_entry is None:
            mlb_cache[upid] = {"name": urec.get("name") or urec.get("Name") or crec.get("name") or "", "mlb_id": mlb_id}
            updated_cache += 1
        else:
            if cache_entry.get("mlb_id") != mlb_id:
                print(
                    f"  ⚠️  mlb_id_cache mismatch for UPID {upid}: existing={cache_entry.get('mlb_id')} "
                    f"new={mlb_id}; leaving existing in place",
                )

        # Update combined_players record
        if crec.get("mlb_id") != mlb_id:
            crec["mlb_id"] = mlb_id
            updated_combined += 1

    print()
    print(f"Summary:")
    print(f"  Updated mlb_id_cache entries:   {updated_cache}")
    print(f"  Updated combined_players rows: {updated_combined}")

    if args.dry_run:
        print("\nDry-run mode; not writing any files.")
        return

    # Backups
    cp_backup = DATA_DIR / "combined_players_mlb_api_backup.json"
    cache_backup = DATA_DIR / "mlb_id_cache_backup.json"

    save_json(cp_backup, combined)
    save_json(cache_backup, mlb_cache)
    print(f"📦 Backups written to {cp_backup} and {cache_backup}")

    # Overwrite live files
    save_json(DATA_DIR / "combined_players.json", combined)
    save_json(DATA_DIR / "mlb_id_cache.json", mlb_cache)
    print("💾 combined_players.json and mlb_id_cache.json updated in place")


if __name__ == "__main__":
    main()
