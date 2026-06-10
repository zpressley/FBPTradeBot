#!/usr/bin/env python3
"""Sweep active MiLB players missing from combined_players and add them safely.

What this script does
---------------------
1) Pull active MiLB players from MLB Stats API (sportId=11, season=2026).
2) Compare against data/combined_players.json using both:
   - mlb_id match
   - normalized full-name match
3) For missing candidates:
   - Check UPID duplicates (normalized name against upid_database primary+alt names)
   - Reuse UPID when exactly one UPID matches
   - Skip as ambiguous if multiple UPIDs match
   - Create new UPID when none match
4) Search Yahoo player pool for yahoo_id (data/yahoo_all_players_2026.json).
5) Apply updates:
   - Update existing combined player rows for reused UPIDs (no duplicate UPID rows)
   - Append new Farm rows for newly created UPIDs
   - Update data/upid_database.json (by_upid + name_index)

Usage
-----
Dry run (default):
    python3 scripts/sweep_add_missing_milb_players.py

Apply changes:
    python3 scripts/sweep_add_missing_milb_players.py --apply
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

COMBINED_PATH = DATA / "combined_players.json"
UPID_PATH = DATA / "upid_database.json"
YAHOO_ALL_PATH = DATA / "yahoo_all_players_2026.json"

MLB_SPORT_ID = 1
MILB_SPORT_ID = 11
SEASON = 2026

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def norm_name(value: str | None) -> str:
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    return _NON_ALNUM.sub("", s)


def lowercase_key(value: str | None) -> str:
    return str(value or "").strip().lower()


def strip_suffix(name: str | None) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    tokens = re.findall(r"[A-Za-z0-9]+", raw)
    if not tokens:
        return raw
    while tokens and tokens[-1].lower() in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = requests.get(url, params=params, timeout=45)
    resp.raise_for_status()
    return resp.json()


def build_team_maps() -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """Return (milb_team_by_id, mlb_abbreviation_by_team_id)."""
    milb_teams = get_json(
        "https://statsapi.mlb.com/api/v1/teams",
        {"sportId": MILB_SPORT_ID, "season": SEASON},
    ).get("teams", [])

    mlb_teams = get_json(
        "https://statsapi.mlb.com/api/v1/teams",
        {"sportId": MLB_SPORT_ID, "season": SEASON},
    ).get("teams", [])

    milb_team_by_id: dict[int, dict[str, Any]] = {}
    for t in milb_teams:
        tid = t.get("id")
        if isinstance(tid, int):
            milb_team_by_id[tid] = t

    mlb_abbrev_by_id: dict[int, str] = {}
    for t in mlb_teams:
        tid = t.get("id")
        if isinstance(tid, int):
            mlb_abbrev_by_id[tid] = str(t.get("abbreviation") or "").upper().strip()

    return milb_team_by_id, mlb_abbrev_by_id


def fetch_active_milb_players() -> list[dict[str, Any]]:
    people = get_json(
        "https://statsapi.mlb.com/api/v1/sports/11/players",
        {"season": SEASON, "activeStatus": "ACTIVE"},
    ).get("people", [])
    return [p for p in people if p.get("active") is True]


def build_combined_indexes(combined: list[dict[str, Any]]) -> tuple[set[int], set[str], dict[str, dict[str, Any]]]:
    combined_mlb_ids: set[int] = set()
    combined_name_norms: set[str] = set()
    combined_by_upid: dict[str, dict[str, Any]] = {}

    for rec in combined:
        mlb_id_val = rec.get("mlb_id")
        if isinstance(mlb_id_val, int):
            combined_mlb_ids.add(mlb_id_val)
        elif isinstance(mlb_id_val, str) and mlb_id_val.isdigit():
            combined_mlb_ids.add(int(mlb_id_val))

        name_n = norm_name(rec.get("name"))
        if name_n:
            combined_name_norms.add(name_n)

        upid = str(rec.get("upid") or "").strip()
        if upid:
            combined_by_upid[upid] = rec

    return combined_mlb_ids, combined_name_norms, combined_by_upid


def build_upid_norm_map(by_upid: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    upid_norm_map: dict[str, set[str]] = defaultdict(set)
    for upid, rec in by_upid.items():
        p = norm_name(rec.get("name"))
        if p:
            upid_norm_map[p].add(str(upid))
        for alt in rec.get("alt_names") or []:
            a = norm_name(alt)
            if a:
                upid_norm_map[a].add(str(upid))
    return upid_norm_map


def build_yahoo_indexes(yahoo_rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Return two indexes:
    - exact normalized full-name -> rows
    - suffix-stripped normalized full-name -> rows
    """
    exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stripped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in yahoo_rows:
        name = row.get("name") or ""
        n = norm_name(name)
        if n:
            exact[n].append(row)
        s = norm_name(strip_suffix(name))
        if s:
            stripped[s].append(row)

    return exact, stripped


def choose_yahoo_id(
    *,
    player_name: str,
    mlb_org_abbrev: str,
    yahoo_exact_idx: dict[str, list[dict[str, Any]]],
    yahoo_suffix_idx: dict[str, list[dict[str, Any]]],
) -> tuple[str, str]:
    """Return (yahoo_id, match_method)."""
    n = norm_name(player_name)
    rows = yahoo_exact_idx.get(n, [])

    if len(rows) == 1:
        return str(rows[0].get("player_id") or ""), "exact_name"
    if len(rows) > 1:
        team_rows = [
            r for r in rows
            if str(r.get("team") or "").upper().strip() == mlb_org_abbrev
        ] if mlb_org_abbrev else []
        if len(team_rows) == 1:
            return str(team_rows[0].get("player_id") or ""), "exact_name+team"
        return "", "ambiguous_exact"

    stripped_key = norm_name(strip_suffix(player_name))
    if stripped_key:
        rows2 = yahoo_suffix_idx.get(stripped_key, [])
        if len(rows2) == 1:
            return str(rows2[0].get("player_id") or ""), "suffix_stripped"
        if len(rows2) > 1:
            team_rows = [
                r for r in rows2
                if str(r.get("team") or "").upper().strip() == mlb_org_abbrev
            ] if mlb_org_abbrev else []
            if len(team_rows) == 1:
                return str(team_rows[0].get("player_id") or ""), "suffix_stripped+team"
            return "", "ambiguous_suffix"

    return "", "not_found"


def add_name_index_mapping(name_index: dict[str, list[str]], name: str, upid: str) -> None:
    key = lowercase_key(name)
    if not key:
        return
    bucket = name_index.setdefault(key, [])
    if upid not in bucket:
        bucket.append(upid)


def ensure_alt_name(rec: dict[str, Any], alt_name: str) -> bool:
    alt_name_clean = str(alt_name or "").strip()
    if not alt_name_clean:
        return False
    primary_norm = norm_name(rec.get("name"))
    alt_norm = norm_name(alt_name_clean)
    if not alt_norm or alt_norm == primary_norm:
        return False

    alts = rec.setdefault("alt_names", [])
    existing_norms = {norm_name(a) for a in alts}
    if alt_norm in existing_norms:
        return False
    alts.append(alt_name_clean)
    return True


def next_upid_start(by_upid: dict[str, dict[str, Any]]) -> int:
    numeric = [int(k) for k in by_upid.keys() if str(k).isdigit()]
    if not numeric:
        return 1
    return max(numeric) + 1


def duplicate_upid_row_count(rows: list[dict[str, Any]]) -> int:
    upids = [
        str(r.get("upid") or "").strip()
        for r in rows
        if str(r.get("upid") or "").strip()
    ]
    if not upids:
        return 0
    counts: dict[str, int] = defaultdict(int)
    for u in upids:
        counts[u] += 1
    return sum(n - 1 for n in counts.values() if n > 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Add missing active MiLB players into combined + UPID.")
    parser.add_argument("--apply", action="store_true", help="Write changes to data files.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max candidate count to process.")
    args = parser.parse_args()

    combined: list[dict[str, Any]] = load_json(COMBINED_PATH)
    upid_db: dict[str, Any] = load_json(UPID_PATH)
    yahoo_rows: list[dict[str, Any]] = load_json(YAHOO_ALL_PATH)

    by_upid: dict[str, dict[str, Any]] = upid_db.get("by_upid") or {}
    name_index: dict[str, list[str]] = upid_db.get("name_index") or {}
    baseline_dup_rows = duplicate_upid_row_count(combined)

    combined_mlb_ids, combined_name_norms, combined_by_upid = build_combined_indexes(combined)
    upid_norm_map = build_upid_norm_map(by_upid)
    yahoo_exact_idx, yahoo_suffix_idx = build_yahoo_indexes(yahoo_rows)

    milb_team_by_id, mlb_abbrev_by_id = build_team_maps()
    active_milb = fetch_active_milb_players()

    candidates: list[dict[str, Any]] = []
    for p in active_milb:
        pid = p.get("id")
        n = norm_name(p.get("fullName"))
        if (isinstance(pid, int) and pid in combined_mlb_ids) or (n and n in combined_name_norms):
            continue

        team_id = (p.get("currentTeam") or {}).get("id")
        milb_team = milb_team_by_id.get(team_id, {})
        parent_org_id = milb_team.get("parentOrgId")
        mlb_org_abbrev = mlb_abbrev_by_id.get(parent_org_id, "")

        candidates.append(
            {
                "mlb_id": pid,
                "name": str(p.get("fullName") or "").strip(),
                "norm_name": n,
                "primary_pos": str((p.get("primaryPosition") or {}).get("abbreviation") or "").strip(),
                "birth_date": p.get("birthDate"),
                "age": p.get("currentAge"),
                "height": p.get("height"),
                "weight": p.get("weight"),
                "bats": (p.get("batSide") or {}).get("code"),
                "throws": (p.get("pitchHand") or {}).get("code"),
                "debut_date": p.get("mlbDebutDate"),
                "milb_team_id": team_id,
                "milb_team_name": milb_team.get("name"),
                "parent_org_id": parent_org_id,
                "team": mlb_org_abbrev,
            }
        )

    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]

    print(f"Active MiLB players: {len(active_milb)}")
    print(f"Candidates missing from combined: {len(candidates)}")

    # Detect candidate internal normalized-name collisions
    candidate_norm_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        if c["norm_name"]:
            candidate_norm_buckets[c["norm_name"]].append(c)

    internal_dupe_norms = {
        k: rows for k, rows in candidate_norm_buckets.items() if len(rows) > 1
    }
    if internal_dupe_norms:
        print(f"⚠️ Candidate normalized-name collisions: {len(internal_dupe_norms)} (will skip these names)")

    next_upid = next_upid_start(by_upid)

    added_combined = 0
    updated_existing_combined = 0
    created_upids = 0
    reused_upids = 0
    skipped_ambiguous_upid = 0
    skipped_internal_duplicate_name = 0
    yahoo_matched = 0
    yahoo_unmatched = 0
    yahoo_ambiguous = 0

    skipped_details: list[dict[str, Any]] = []
    processed_rows: list[dict[str, Any]] = []

    for cand in candidates:
        norm_n = cand["norm_name"]
        if not norm_n:
            skipped_internal_duplicate_name += 1
            skipped_details.append(
                {"name": cand["name"], "reason": "empty_normalized_name"}
            )
            continue

        if norm_n in internal_dupe_norms:
            skipped_internal_duplicate_name += 1
            skipped_details.append(
                {"name": cand["name"], "reason": "candidate_name_collision"}
            )
            continue

        existing_upids = sorted(upid_norm_map.get(norm_n, []))
        if len(existing_upids) > 1:
            skipped_ambiguous_upid += 1
            skipped_details.append(
                {
                    "name": cand["name"],
                    "reason": "ambiguous_upid_match",
                    "upids": existing_upids,
                }
            )
            continue

        yahoo_id, yahoo_method = choose_yahoo_id(
            player_name=cand["name"],
            mlb_org_abbrev=cand["team"],
            yahoo_exact_idx=yahoo_exact_idx,
            yahoo_suffix_idx=yahoo_suffix_idx,
        )
        if yahoo_id:
            yahoo_matched += 1
        else:
            if yahoo_method.startswith("ambiguous"):
                yahoo_ambiguous += 1
            else:
                yahoo_unmatched += 1

        if len(existing_upids) == 1:
            # Reuse existing UPID; update existing combined row to avoid duplicate UPIDs.
            reused_upids += 1
            upid = existing_upids[0]
            upid_rec = by_upid.get(upid)
            if not upid_rec:
                skipped_details.append(
                    {
                        "name": cand["name"],
                        "reason": "upid_missing_from_by_upid",
                        "upid": upid,
                    }
                )
                continue

            add_name_index_mapping(name_index, cand["name"], upid)
            ensure_alt_name(upid_rec, cand["name"])

            row = combined_by_upid.get(upid)
            if not row:
                skipped_details.append(
                    {
                        "name": cand["name"],
                        "reason": "combined_row_missing_for_reused_upid",
                        "upid": upid,
                    }
                )
                continue

            row_changed = False
            canonical_name = str(upid_rec.get("name") or cand["name"]).strip()
            row_name_norm = norm_name(row.get("name"))
            candidate_name_norm = norm_name(cand["name"])
            canonical_name_norm = norm_name(canonical_name)
            name_mismatch = row_name_norm not in {candidate_name_norm, canonical_name_norm}
            if name_mismatch:
                row["name"] = canonical_name
                row_changed = True

            # Enrich IDs/metadata. If row identity was mismatched, align fields to candidate.
            cand_mlb_id = cand.get("mlb_id")
            if isinstance(cand_mlb_id, int):
                existing_mlb = row.get("mlb_id")
                if not existing_mlb or name_mismatch:
                    row["mlb_id"] = cand_mlb_id
                    row_changed = True
                elif str(existing_mlb).isdigit() and int(existing_mlb) != cand_mlb_id:
                    skipped_details.append(
                        {
                            "name": cand["name"],
                            "reason": "mlb_id_conflict_on_reused_upid",
                            "upid": upid,
                            "existing_mlb_id": existing_mlb,
                            "candidate_mlb_id": cand_mlb_id,
                        }
                    )

            existing_yahoo = str(row.get("yahoo_id") or "").strip()
            if yahoo_id and existing_yahoo != yahoo_id:
                row["yahoo_id"] = yahoo_id
                row_changed = True
            elif name_mismatch and existing_yahoo and not yahoo_id:
                # If a mismatched identity row was repaired and we have no
                # Yahoo hit for the canonical player, clear stale Yahoo id.
                row["yahoo_id"] = ""
                row_changed = True

            for src, dst in [
                ("team", "team"),
                ("primary_pos", "position"),
                ("primary_pos", "mlb_primary_position"),
                ("birth_date", "birth_date"),
                ("height", "height"),
                ("weight", "weight"),
                ("age", "age"),
                ("bats", "bats"),
                ("throws", "throws"),
            ]:
                if cand.get(src) and (not row.get(dst) or name_mismatch):
                    row[dst] = cand[src]
                    row_changed = True

            if cand.get("debut_date") and (not row.get("debut_date") or name_mismatch):
                row["debut_date"] = cand["debut_date"]
                row_changed = True
            if cand.get("debut_date") and (not row.get("debuted") or name_mismatch):
                row["debuted"] = str(cand["debut_date"])[:4]
                row_changed = True

            if row_changed:
                updated_existing_combined += 1

            processed_rows.append(
                {
                    "name": cand["name"],
                    "upid": upid,
                    "action": "reused_upid_updated_existing",
                    "name_mismatch_repaired": name_mismatch,
                    "yahoo_id": yahoo_id or "",
                    "yahoo_method": yahoo_method,
                }
            )
            continue

        # Create new UPID + new combined row.
        upid = str(next_upid)
        next_upid += 1
        created_upids += 1

        by_upid[upid] = {
            "upid": upid,
            "name": cand["name"],
            "team": cand.get("team") or "",
            "pos": cand.get("primary_pos") or "",
            "alt_names": [],
            "approved_dupes": "",
        }
        add_name_index_mapping(name_index, cand["name"], upid)
        upid_norm_map[norm_n].add(upid)

        combined_row = {
            "name": cand["name"],
            "team": cand.get("team") or "",
            "position": cand.get("primary_pos") or "",
            "mlb_primary_position": cand.get("primary_pos") or "",
            "FBP_Team": "",
            "manager": "",
            "player_type": "Farm",
            "contract_type": "",
            "status": "P",
            "years_simple": "P",
            "fypd": "",
            "yahoo_id": yahoo_id or "",
            "upid": upid,
            "mlb_id": cand.get("mlb_id"),
            "birth_date": cand.get("birth_date"),
            "height": cand.get("height"),
            "weight": cand.get("weight"),
            "age": cand.get("age"),
            "bats": cand.get("bats"),
            "throws": cand.get("throws"),
        }

        debut_date = cand.get("debut_date")
        if debut_date:
            combined_row["debut_date"] = debut_date
            combined_row["debuted"] = str(debut_date)[:4]

        combined.append(combined_row)
        combined_by_upid[upid] = combined_row
        added_combined += 1

        processed_rows.append(
            {
                "name": cand["name"],
                "upid": upid,
                "action": "new_upid_new_combined",
                "yahoo_id": yahoo_id or "",
                "yahoo_method": yahoo_method,
            }
        )

    # Final integrity checks.
    combined_upids = [str(p.get("upid") or "").strip() for p in combined if str(p.get("upid") or "").strip()]
    upid_dupes_in_combined = len(combined_upids) - len(set(combined_upids))

    print()
    print("Summary")
    print("-------")
    print(f"Processed candidates: {len(processed_rows)}")
    print(f"Skipped (ambiguous UPID): {skipped_ambiguous_upid}")
    print(f"Skipped (candidate name collisions/invalid): {skipped_internal_duplicate_name}")
    print(f"UPIDs created: {created_upids}")
    print(f"UPIDs reused: {reused_upids}")
    print(f"Combined rows added: {added_combined}")
    print(f"Existing combined rows updated: {updated_existing_combined}")
    print(f"Yahoo IDs matched: {yahoo_matched}")
    print(f"Yahoo IDs unmatched: {yahoo_unmatched}")
    print(f"Yahoo IDs ambiguous: {yahoo_ambiguous}")
    print(f"Baseline combined UPID duplicate rows: {baseline_dup_rows}")
    print(f"Combined UPID duplicates after run: {upid_dupes_in_combined}")

    if skipped_details:
        print()
        print("Skipped detail sample (up to 25)")
        for row in skipped_details[:25]:
            print(row)

    if not args.apply:
        print("\nDry run only. No files written.")
        return

    if upid_dupes_in_combined > baseline_dup_rows:
        raise SystemExit(
            "Refusing to write: run would introduce NEW duplicate UPIDs "
            f"(baseline={baseline_dup_rows}, after={upid_dupes_in_combined})"
        )

    # Backups
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    combined_backup = DATA / f"combined_players_before_milb_sweep_{ts}.json"
    upid_backup = DATA / f"upid_database_before_milb_sweep_{ts}.json"
    save_json(combined_backup, load_json(COMBINED_PATH))
    save_json(upid_backup, load_json(UPID_PATH))

    upid_db["by_upid"] = by_upid
    upid_db["name_index"] = name_index

    save_json(COMBINED_PATH, combined)
    save_json(UPID_PATH, upid_db)

    report = {
        "season": SEASON,
        "active_milb_players": len(active_milb),
        "missing_candidates": len(candidates),
        "processed_candidates": len(processed_rows),
        "skipped_ambiguous_upid": skipped_ambiguous_upid,
        "skipped_internal_duplicate_name": skipped_internal_duplicate_name,
        "created_upids": created_upids,
        "reused_upids": reused_upids,
        "added_combined_rows": added_combined,
        "updated_existing_combined_rows": updated_existing_combined,
        "yahoo_matched": yahoo_matched,
        "yahoo_unmatched": yahoo_unmatched,
        "yahoo_ambiguous": yahoo_ambiguous,
        "combined_upid_duplicates_after": upid_dupes_in_combined,
        "processed_rows": processed_rows,
        "skipped_details": skipped_details,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    report_path = DATA / "milb_missing_sweep_report.json"
    save_json(report_path, report)

    print()
    print(f"✅ Wrote {COMBINED_PATH}")
    print(f"✅ Wrote {UPID_PATH}")
    print(f"🧾 Wrote {report_path}")
    print(f"📦 Backup: {combined_backup.name}")
    print(f"📦 Backup: {upid_backup.name}")


if __name__ == "__main__":
    main()
