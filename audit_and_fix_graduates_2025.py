#!/usr/bin/env python3
"""Audit and fix 2025 prospect graduations.

Goals
-----
- Re-evaluate all players that were graduated by data_pipeline/graduate_prospects_2025.py
  using the *fixed* Fangraphs aggregation (no double-count across leaderboards).
- For any player who no longer meets graduation criteria, restore their original
  prospect status/contract/player_type using the "before_*" fields in
  data/historical/2025/graduates_2025.csv.
- Remove the corresponding incorrect Graduate entries from data/player_log.json.
- Run a second pass over all current prospects to find any players that *should*
  graduate but were missed previously, and apply graduations + player_log
  entries for them.
- Rewrite data/historical/2025/graduates_2025.csv to reflect the final, corrected
  set of graduates and their metrics.

This script supports a dry-run mode by default (no files are modified). Use
"--apply" to actually write changes.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Re-use the core graduation logic and paths
from data_pipeline.graduate_prospects_2025 import (  # type: ignore
    COMBINED_PATH,
    DATA_DIR,
    EVENT_LABEL,
    MLBUsage,
    SEASON,
    SOURCE,
    build_mlb_usage,
    classify_graduation,
    load_combined_players,
)
from player_log import append_entry  # type: ignore

ROOT = Path(__file__).resolve().parent
HIST_2025_DIR = DATA_DIR / "historical" / "2025"
GRADS_CSV_PATH = HIST_2025_DIR / "graduates_2025.csv"
PLAYER_LOG_PATH = DATA_DIR / "player_log.json"
UPID_DB_PATH = DATA_DIR / "upid_database.json"


def load_player_log() -> List[Dict[str, Any]]:
    if not PLAYER_LOG_PATH.exists():
        return []
    with PLAYER_LOG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_player_log(entries: List[Dict[str, Any]]) -> None:
    with PLAYER_LOG_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def load_graduates_csv() -> List[Dict[str, Any]]:
    if not GRADS_CSV_PATH.exists():
        raise SystemExit(f"❌ Graduates CSV not found: {GRADS_CSV_PATH}")
    with GRADS_CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


def save_graduates_csv(rows: List[Dict[str, Any]]) -> None:
    HIST_2025_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Still write an empty file with no header for transparency
        GRADS_CSV_PATH.write_text("", encoding="utf-8")
        return

    with GRADS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_upid_database() -> Dict[str, Any]:
    if not UPID_DB_PATH.exists():
        return {}
    with UPID_DB_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_indices(
    players: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[int, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """Return (by_upid, by_mlb_id, by_name) indices for combined_players.

    - by_upid: upid (str) -> player
    - by_mlb_id: mlb_id (int) -> [players]
    - by_name: normalized name (lowercase) -> [players]
    """

    by_upid: Dict[str, Dict[str, Any]] = {}
    by_mlb_id: Dict[int, List[Dict[str, Any]]] = {}
    by_name: Dict[str, List[Dict[str, Any]]] = {}

    for p in players:
        upid = str(p.get("upid") or "").strip()
        if upid:
            by_upid[upid] = p

        mlb_id = p.get("mlb_id")
        if isinstance(mlb_id, int):
            by_mlb_id.setdefault(mlb_id, []).append(p)

        name = str(p.get("name") or "").strip().lower()
        if name:
            by_name.setdefault(name, []).append(p)

    return by_upid, by_mlb_id, by_name


def build_alt_name_index(upid_db: Dict[str, Any]) -> Dict[str, str]:
    """Map normalized alt-name -> UPID from upid_database.json.

    Returns {name_lower: upid}.
    """

    alt_to_upid: Dict[str, str] = {}
    by_upid = upid_db.get("by_upid") or {}
    for upid, info in by_upid.items():
        base_name = str(info.get("name") or "").strip().lower()
        if base_name:
            alt_to_upid.setdefault(base_name, upid)
        for alt in info.get("alt_names", []) or []:
            alt_lower = str(alt or "").strip().lower()
            if alt_lower:
                alt_to_upid.setdefault(alt_lower, upid)
    return alt_to_upid


def find_player_for_grad_row(
    row: Dict[str, Any],
    by_upid: Dict[str, Dict[str, Any]],
    by_mlb_id: Dict[int, List[Dict[str, Any]]],
    by_name: Dict[str, List[Dict[str, Any]]],
    alt_to_upid: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Best-effort match of a graduates_2025 row to a combined_players entry.

    Matching order:
    1) CSV upid -> combined_players[upid]
    2) CSV mlb_id -> combined_players by mlb_id (unique match only)
    3) CSV name (normalized) -> combined_players by name (unique match only)
    4) CSV name via upid_database alt-names -> upid -> combined_players
    """

    # 1) UPID from CSV
    upid_raw = row.get("upid") or row.get("UPID")
    upid = str(upid_raw or "").strip()
    if upid and upid in by_upid:
        return by_upid[upid]

    # 2) mlb_id from CSV
    mlb_id_val = row.get("mlb_id") or row.get("MLBID") or row.get("MLB_ID")
    try:
        mlb_id = int(mlb_id_val) if mlb_id_val not in (None, "") else None
    except (TypeError, ValueError):
        mlb_id = None

    if mlb_id is not None:
        candidates = by_mlb_id.get(mlb_id) or []
        if len(candidates) == 1:
            return candidates[0]

    # 3) Name direct match
    name = str(row.get("name") or row.get("player_name") or "").strip().lower()
    if name:
        candidates = by_name.get(name) or []
        if len(candidates) == 1:
            return candidates[0]

    # 4) Name via UPID alt-names
    if name and name in alt_to_upid:
        upid_from_alt = alt_to_upid[name]
        if upid_from_alt in by_upid:
            return by_upid[upid_from_alt]

    return None


def backup_file(path: Path, suffix: str) -> Path:
    backup = path.with_name(path.name + suffix)
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and fix 2025 prospect graduations")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply fixes to combined_players.json, player_log.json, and graduates_2025.csv (default is dry-run)",
    )
    args = parser.parse_args()

    print("\n=== 2025 Graduation Audit (Fangraphs de-dup fix) ===\n")

    # Load core data
    combined_players = load_combined_players()
    player_log = load_player_log()
    grads_rows = load_graduates_csv()
    usage_map = build_mlb_usage()
    upid_db = load_upid_database()

    by_upid, by_mlb_id, by_name = build_indices(combined_players)
    alt_to_upid = build_alt_name_index(upid_db)

    print(f"Combined players : {len(combined_players)}")
    print(f"Player log       : {len(player_log)} entries")
    print(f"Graduates CSV    : {len(grads_rows)} rows")
    print(f"MLB usage map    : {len(usage_map)} players with stats")
    print()

    # Pass 1: re-evaluate existing graduates
    reverted_upids: List[str] = []
    updated_grad_rows: List[Dict[str, Any]] = []

    missing_player_matches = 0

    for row in grads_rows:
        player = find_player_for_grad_row(row, by_upid, by_mlb_id, by_name, alt_to_upid)
        if not player:
            missing_player_matches += 1
            continue

        mlb_id = player.get("mlb_id")
        usage = usage_map.get(mlb_id, MLBUsage()) if isinstance(mlb_id, int) else MLBUsage()

        should_grad, reason, metrics = classify_graduation(player, usage)

        if not should_grad:
            # This player should not be a graduate under corrected rules.
            upid = str(player.get("upid") or "").strip()
            reverted_upids.append(upid)

            if args.apply:
                # Restore original prospect state from CSV's before_* columns.
                player["player_type"] = row.get("before_player_type")
                player["contract_type"] = row.get("before_contract_type")
                player["status"] = row.get("before_status")
                player["years_simple"] = row.get("before_years")

            continue  # do not keep this row in the final grads CSV

        # Still a valid graduate: refresh metrics/reason for CSV output
        row = dict(row)  # copy to avoid mutating original list
        row["reason"] = reason
        row["age"] = metrics.get("age")
        row["pa"] = metrics.get("pa")
        row["g_bat"] = metrics.get("g_bat")
        row["ip"] = metrics.get("ip")
        row["g_pitch"] = metrics.get("g_pitch")
        updated_grad_rows.append(row)

    print(f"Re-evaluated existing graduates: {len(grads_rows)}")
    print(f"  ➤ Still graduates : {len(updated_grad_rows)}")
    print(f"  ➤ Reverted to P   : {len(reverted_upids)}")
    if missing_player_matches:
        print(f"  ➤ No combined match: {missing_player_matches} (left as-is in CSV)")
    print()

    # Pass 2: wipe incorrect Graduate logs for reverted players
    reverted_set = {u for u in reverted_upids if u}

    if reverted_set:
        before_log_count = len(player_log)
        filtered_log: List[Dict[str, Any]] = []
        removed_logs = 0

        for entry in player_log:
            if (
                entry.get("update_type") == "Graduate"
                and entry.get("event") == EVENT_LABEL
                and entry.get("season") == SEASON
                and entry.get("admin") == "2025_graduation"
                and str(entry.get("upid") or "").strip() in reverted_set
            ):
                removed_logs += 1
                continue
            filtered_log.append(entry)

        print(f"Player log entries before : {before_log_count}")
        print(f"  ➤ Removed incorrect Graduate logs: {removed_logs}")
        print(f"  ➤ After                         : {len(filtered_log)}")

        if args.apply:
            player_log = filtered_log
        print()

    # Pass 3: second graduation pass to catch missed graduates
    existing_grad_upids = {
        str(r.get("upid") or "").strip()
        for r in updated_grad_rows
    }

    new_grads: List[Dict[str, Any]] = []

    for p in combined_players:
        years = (p.get("years_simple") or "").strip().upper()
        player_type = (p.get("player_type") or "").strip()

        # Only consider prospect-like records
        if years != "P" and player_type.upper() != "FARM":
            continue

        upid = str(p.get("upid") or "").strip()
        if upid in existing_grad_upids:
            # Already in the graduates CSV and still valid; skip
            continue

        mlb_id = p.get("mlb_id")
        usage = usage_map.get(mlb_id, MLBUsage()) if isinstance(mlb_id, int) else MLBUsage()

        should_grad, reason, metrics = classify_graduation(p, usage)
        if not should_grad:
            continue

        # Apply graduation effects mirroring graduate_prospects_2025
        owned = bool((p.get("manager") or "").strip())
        before = {
            "player_type": p.get("player_type"),
            "contract_type": p.get("contract_type"),
            "status": p.get("status"),
            "years_simple": p.get("years_simple"),
        }

        if args.apply:
            if owned:
                p["player_type"] = "MLB"
                p["contract_type"] = "Keeper Contract"
                p["status"] = "[6] TCR"
                p["years_simple"] = "TC R"
            else:
                p["player_type"] = "MLB"
                p["contract_type"] = None
                p["status"] = "[5] TC1"
                p["years_simple"] = "TC 1"

            # Log to player_log
            append_entry(
                season=SEASON,
                source=SOURCE,
                upid=str(p.get("upid") or ""),
                player_name=p.get("name") or "",
                team=p.get("team") or "",
                pos=p.get("position") or "",
                age=p.get("age"),
                level=str(p.get("level") or ""),
                team_rank=p.get("team_rank"),
                rank=p.get("rank"),
                eta=str(p.get("eta") or ""),
                player_type=p.get("player_type") or "",
                owner=p.get("manager") or "",
                contract=p.get("contract_type") or "",
                status=p.get("status") or "",
                years=p.get("years_simple") or "",
                update_type="Graduate",
                event=EVENT_LABEL,
                admin="2025_graduation",
            )

        # Build row for graduates CSV
        new_grads.append(
            {
                "upid": p.get("upid"),
                "name": p.get("name"),
                "manager": p.get("manager") or "",
                "owned": owned,
                "mlb_id": mlb_id if isinstance(mlb_id, int) else None,
                "reason": reason,
                **metrics,
                "before_player_type": before["player_type"],
                "before_contract_type": before["contract_type"],
                "before_status": before["status"],
                "before_years": before["years_simple"],
                "after_player_type": p.get("player_type") if args.apply else before["player_type"],
                "after_contract_type": p.get("contract_type") if args.apply else before["contract_type"],
                "after_status": p.get("status") if args.apply else before["status"],
                "after_years": p.get("years_simple") if args.apply else before["years_simple"],
            }
        )

    print(f"New graduates found on second pass: {len(new_grads)}")
    print()

    # Final graduates CSV rows = updated existing grads + new grads
    final_rows = updated_grad_rows + new_grads

    print("Final graduates summary:")
    print(f"  ➤ Total graduates after fix: {len(final_rows)}")
    print(f"  ➤ Still grads from first run: {len(updated_grad_rows)}")
    print(f"  ➤ Newly added graduates     : {len(new_grads)}")

    if not args.apply:
        print("\nDry-run complete. No files were modified.")
        print("Run again with --apply to write changes:")
        print("  python3 audit_and_fix_graduates_2025.py --apply")
        return

    # Apply changes: backups then writes
    print("\nApplying changes (backups will be created)...\n")

    combined_backup = backup_file(COMBINED_PATH, "_bak_graduation_fix.json")
    log_backup = backup_file(PLAYER_LOG_PATH, "_bak_graduation_fix.json")
    grads_backup = backup_file(GRADS_CSV_PATH, "_bak_graduation_fix.csv")

    print(f"  ➤ combined_players backup : {combined_backup}")
    print(f"  ➤ player_log backup       : {log_backup}")
    print(f"  ➤ graduates_2025 backup   : {grads_backup}")

    # Save combined_players
    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        json.dump(combined_players, f, indent=2)

    # Save player_log
    save_player_log(player_log)

    # Save graduates CSV
    save_graduates_csv(final_rows)

    print("\n✅ Graduation audit + fix applied.")


if __name__ == "__main__":
    main()
