"""Enrich combined_players.json with IDs from the SFBB Player ID Map.

Matching priority:
  1. UPID column from the CSV (already mapped)
  2. PLAYERNAME → UPID database name_index → disambiguate by team/position
  3. Fallback: match via MLBID or YAHOOID against combined_players

Fields added to combined_players:
  - bbref_id     (IDPLAYER, col A)
  - fangraphs_id (IDFANGRAPHS, col I)
  - fangraphs_name (FANGRAPHSNAME, col J)
  - yahoo_id     (YAHOOID, col X) — only if missing
  - mlb_id       (MLBID, col K) — only if missing
  - birth_date   (BIRTHDATE, col C) — only if missing

UPID database updates:
  - FANGRAPHSNAME added as alt_name if it differs from existing names
  - YAHOONAME added as alt_name if it differs from existing names

Run:
    python scripts/enrich_sfbb_ids.py          # dry-run
    python scripts/enrich_sfbb_ids.py --apply  # write changes
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

COMBINED_PATH = DATA / "combined_players.json"
UPID_PATH = DATA / "upid_database.json"
SFBB_PATH = DATA / "historical" / "2026" / "SFBB ID Map - SFBB Player ID Map - PLAYERIDMAP + UPID.csv"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def norm(s):
    return (s or "").strip().lower()


def main():
    apply = "--apply" in sys.argv

    players = load_json(COMBINED_PATH)
    upid_db = load_json(UPID_PATH)
    by_upid = upid_db.get("by_upid", {})
    name_index = upid_db.get("name_index", {})

    with open(SFBB_PATH, encoding="utf-8-sig") as f:
        sfbb = list(csv.DictReader(f))

    print(f"Loaded: {len(players)} players, {len(sfbb)} SFBB rows, {len(by_upid)} UPIDs")

    # Build combined_players indexes for fallback matching
    cp_by_upid = {str(p.get("upid", "")): p for p in players if p.get("upid")}
    cp_by_mlb = {}
    cp_by_yahoo = {}
    for p in players:
        mid = str(p.get("mlb_id") or "").strip()
        yid = str(p.get("yahoo_id") or "").strip()
        if mid:
            cp_by_mlb[mid] = p
        if yid:
            cp_by_yahoo[yid] = p

    # Stats
    filled = {"bbref_id": 0, "fangraphs_id": 0, "fangraphs_name": 0,
              "yahoo_id": 0, "mlb_id": 0, "birth_date": 0}
    alt_added = 0
    matched_count = 0

    for row in sfbb:
        csv_upid = row.get("UPID", "").strip()
        playername = row.get("PLAYERNAME", "").strip()
        mlbid = row.get("MLBID", "").strip()
        yahooid = row.get("YAHOOID", "").strip()
        bbref = row.get("IDPLAYER", "").strip()
        fgid = row.get("IDFANGRAPHS", "").strip()
        fgname = row.get("FANGRAPHSNAME", "").strip()
        yahooname = row.get("YAHOONAME", "").strip()
        birthdate = row.get("BIRTHDATE", "").strip()
        team = row.get("TEAM", "").strip().upper()
        pos = row.get("POS", "").strip()

        # ── Match to combined_players record ──

        target = None
        matched_upid = None

        # 1. Direct UPID from CSV
        if csv_upid and csv_upid != "NO UPID" and csv_upid in cp_by_upid:
            target = cp_by_upid[csv_upid]
            matched_upid = csv_upid

        # 2. PLAYERNAME → UPID name_index → disambiguate
        if not target:
            upid_hits = name_index.get(norm(playername), [])
            unique_upids = list(dict.fromkeys(upid_hits))

            if len(unique_upids) == 1 and unique_upids[0] in cp_by_upid:
                target = cp_by_upid[unique_upids[0]]
                matched_upid = unique_upids[0]
            elif len(unique_upids) > 1:
                # Disambiguate by team then position
                for uid in unique_upids:
                    rec = cp_by_upid.get(uid)
                    if not rec:
                        continue
                    rec_team = (rec.get("mlb_team") or rec.get("team") or "").strip().upper()
                    if rec_team == team:
                        target = rec
                        matched_upid = uid
                        break
                if not target:
                    for uid in unique_upids:
                        rec = cp_by_upid.get(uid)
                        if not rec:
                            continue
                        rec_pos = (rec.get("position") or "").strip().upper()
                        if pos and rec_pos and pos[0] == rec_pos[0]:  # rough match (P/P, C/C, etc)
                            target = rec
                            matched_upid = uid
                            break

        # 3. Fallback: match by MLBID or YAHOOID
        if not target and mlbid and mlbid in cp_by_mlb:
            target = cp_by_mlb[mlbid]
            matched_upid = str(target.get("upid", ""))

        if not target and yahooid and yahooid in cp_by_yahoo:
            target = cp_by_yahoo[yahooid]
            matched_upid = str(target.get("upid", ""))

        if not target:
            continue

        matched_count += 1

        # ── Fill missing fields on combined_players ──

        if bbref and not target.get("bbref_id"):
            target["bbref_id"] = bbref
            filled["bbref_id"] += 1

        if fgid and not target.get("fangraphs_id"):
            target["fangraphs_id"] = fgid
            filled["fangraphs_id"] += 1

        if fgname and not target.get("fangraphs_name"):
            target["fangraphs_name"] = fgname
            filled["fangraphs_name"] += 1

        if yahooid and not str(target.get("yahoo_id") or "").strip():
            target["yahoo_id"] = yahooid
            filled["yahoo_id"] += 1

        if mlbid and not str(target.get("mlb_id") or "").strip():
            target["mlb_id"] = mlbid
            filled["mlb_id"] += 1

        if birthdate and not target.get("birth_date"):
            target["birth_date"] = birthdate
            filled["birth_date"] += 1

        # ── Add alt-names to UPID database ──

        if matched_upid and matched_upid in by_upid:
            entry = by_upid[matched_upid]
            alt_names = entry.setdefault("alt_names", [])
            existing = {norm(a) for a in alt_names} | {norm(entry.get("name", ""))}

            for variant in [fgname, yahooname]:
                if not variant:
                    continue
                if norm(variant) in existing:
                    continue
                alt_names.append(variant)
                alt_added += 1
                existing.add(norm(variant))

                # Add to name_index for future lookups
                key = norm(variant)
                if key not in name_index:
                    name_index[key] = []
                if matched_upid not in name_index[key]:
                    name_index[key].append(matched_upid)

    # ── Report ──
    print(f"\nMatched {matched_count} / {len(sfbb)} SFBB rows to combined_players")
    print(f"\n── Fields filled ──")
    for field, count in filled.items():
        print(f"  {field:20s} +{count}")
    print(f"\n── UPID alt-names added: {alt_added} ──")

    before_bbref = sum(1 for p in players if p.get("bbref_id"))
    before_fg = sum(1 for p in players if p.get("fangraphs_id"))
    print(f"\n── Final coverage ({len(players)} players) ──")
    print(f"  bbref_id:       {before_bbref}")
    print(f"  fangraphs_id:   {before_fg}")
    print(f"  yahoo_id:       {sum(1 for p in players if p.get('yahoo_id'))}")
    print(f"  mlb_id:         {sum(1 for p in players if p.get('mlb_id'))}")
    print(f"  birth_date:     {sum(1 for p in players if p.get('birth_date'))}")

    if apply:
        save_json(COMBINED_PATH, players)
        save_json(UPID_PATH, upid_db)
        print(f"\n✅ Saved changes")
    else:
        print(f"\n⚠️  Dry run — use --apply to write changes")


if __name__ == "__main__":
    main()
