"""Enrich combined_players.json with MLB and Yahoo IDs.

Data sources:
  - data/razzball.csv          → Name, MLBAMID, YahooName
  - data/yahoo_all_players_2026.json → name, player_id (yahoo ID)
  - data/upid_database.json    → canonical name ↔ UPID mapping
  - data/combined_players.json → the target to enrich

Strategy:
  1. Build a bridge table: razzball Name/YahooName → (MLBAMID, yahoo_player_id)
     by cross-referencing razzball names against yahoo_all_players names.
  2. For each player in combined_players:
     a. If they already have mlb_id → look up razzball by mlb_id → fill yahoo_id
     b. If they already have yahoo_id → look up razzball by yahoo_id → fill mlb_id
     c. Otherwise match by UPID name (+ alt_names) → fill both
  3. Add razzball YahooName variants as alt_names in UPID database.

Run:
    python scripts/enrich_player_ids.py          # dry-run (prints stats)
    python scripts/enrich_player_ids.py --apply  # writes changes
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
RAZZBALL_PATH = DATA / "razzball.csv"
YAHOO_PATH = DATA / "yahoo_all_players_2026.json"


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

    # ── Load data ──
    players = load_json(COMBINED_PATH)
    upid_db = load_json(UPID_PATH)
    yahoo_all = load_json(YAHOO_PATH)

    with open(RAZZBALL_PATH, encoding="utf-8-sig") as f:
        razzball = list(csv.DictReader(f))

    by_upid = upid_db.get("by_upid", {})
    name_index = upid_db.get("name_index", {})

    print(f"Loaded: {len(players)} players, {len(razzball)} razzball, {len(yahoo_all)} yahoo, {len(by_upid)} UPIDs")

    # ── 1. Build yahoo lookup by name ──
    yahoo_by_name: dict[str, list] = {}
    for yp in yahoo_all:
        n = norm(yp.get("name"))
        if n:
            yahoo_by_name.setdefault(n, []).append(yp)

    # ── 2. Build razzball bridge: mlb_id → record, name → record ──
    razz_by_mlb: dict[str, dict] = {}
    razz_by_name: dict[str, dict] = {}

    for r in razzball:
        mlb_id = r.get("MLBAMID", "").strip()
        rname = norm(r.get("Name"))
        yname = norm(r.get("YahooName"))

        # Try to find yahoo_id by matching razzball name against yahoo data
        yahoo_id = None
        ymatch = yahoo_by_name.get(rname) or yahoo_by_name.get(yname)
        if ymatch and len(ymatch) == 1:
            yahoo_id = str(ymatch[0].get("player_id", ""))

        enriched = {
            "mlb_id": mlb_id,
            "yahoo_id": yahoo_id or "",
            "name": r.get("Name", "").strip(),
            "yahoo_name": r.get("YahooName", "").strip(),
        }

        if mlb_id:
            razz_by_mlb[mlb_id] = enriched
        if rname:
            razz_by_name[rname] = enriched
        if yname and yname != rname:
            razz_by_name[yname] = enriched

    print(f"Razzball bridge: {len(razz_by_mlb)} by MLB ID, {len(razz_by_name)} by name")

    # ── 3. Razzball Name → UPID (via name_index) → mlb_id mapping ──
    # This is the primary path: use UPID as the join key.
    upid_to_mlb: dict[str, str] = {}
    upid_to_yahoo: dict[str, str] = {}  # from razzball-yahoo bridge
    razz_upid_matched = 0

    for r in razzball:
        mlb_id = r.get("MLBAMID", "").strip()
        if not mlb_id:
            continue

        # Try razzball Name and YahooName against UPID name_index
        matched_upid = None
        for key in [norm(r.get("Name")), norm(r.get("YahooName"))]:
            if not key:
                continue
            upid_ids = name_index.get(key)
            if upid_ids:
                matched_upid = upid_ids[0]
                break

        if matched_upid and matched_upid not in upid_to_mlb:
            upid_to_mlb[matched_upid] = mlb_id
            razz_upid_matched += 1

            # Also carry the yahoo_id from the bridge if available
            bridge = razz_by_mlb.get(mlb_id)
            if bridge and bridge["yahoo_id"]:
                upid_to_yahoo[matched_upid] = bridge["yahoo_id"]

    print(f"Razzball → UPID → mlb_id: {razz_upid_matched} UPIDs matched")
    print(f"Razzball → UPID → yahoo_id (via bridge): {len(upid_to_yahoo)} UPIDs")

    # ── 4. Enrich combined_players ──
    filled_mlb = 0
    filled_yahoo = 0
    total_before_mlb = sum(1 for p in players if p.get("mlb_id"))
    total_before_yahoo = sum(1 for p in players if p.get("yahoo_id"))

    for p in players:
        cur_mlb = str(p.get("mlb_id") or "").strip()
        cur_yahoo = str(p.get("yahoo_id") or "").strip()
        upid = str(p.get("upid") or "")

        # Fill mlb_id: UPID → razzball mlb_id
        if not cur_mlb and upid in upid_to_mlb:
            p["mlb_id"] = upid_to_mlb[upid]
            cur_mlb = p["mlb_id"]
            filled_mlb += 1

        # Fill yahoo_id: UPID → razzball-yahoo bridge
        if not cur_yahoo and upid in upid_to_yahoo:
            p["yahoo_id"] = upid_to_yahoo[upid]
            cur_yahoo = p["yahoo_id"]
            filled_yahoo += 1

        # Fallback: direct name → yahoo_all_players (for players not in razzball)
        if not cur_yahoo:
            pname = norm(p.get("name"))
            names_to_try = [pname]
            if upid and upid in by_upid:
                for alt in by_upid[upid].get("alt_names", []):
                    n = norm(alt)
                    if n and n not in names_to_try:
                        names_to_try.append(n)
            for n in names_to_try:
                ymatch = yahoo_by_name.get(n)
                if ymatch and len(ymatch) == 1:
                    p["yahoo_id"] = str(ymatch[0].get("player_id", ""))
                    filled_yahoo += 1
                    break

    total_after_mlb = sum(1 for p in players if p.get("mlb_id"))
    total_after_yahoo = sum(1 for p in players if p.get("yahoo_id"))

    print(f"\n── Combined Players ID Enrichment ──")
    print(f"  MLB IDs:   {total_before_mlb} → {total_after_mlb}  (+{filled_mlb})")
    print(f"  Yahoo IDs: {total_before_yahoo} → {total_after_yahoo}  (+{filled_yahoo})")

    # ── 5. Add YahooName alt_names to UPID database ──
    alt_added = 0

    for r in razzball:
        rname = norm(r.get("Name"))
        yname = norm(r.get("YahooName"))

        if not yname or yname == rname:
            continue

        # Find UPID for this player via name_index
        upid_ids = name_index.get(rname) or name_index.get(yname)
        if not upid_ids:
            continue

        # Use first matching UPID
        uid = upid_ids[0]
        entry = by_upid.get(uid)
        if not entry:
            continue

        alt_names = entry.setdefault("alt_names", [])
        existing_lower = {norm(a) for a in alt_names}

        # Add YahooName (accented) as alt if not present
        yahoo_display = r.get("YahooName", "").strip()
        if norm(yahoo_display) not in existing_lower and norm(yahoo_display) != norm(entry.get("name", "")):
            alt_names.append(yahoo_display)
            alt_added += 1

            # Also add to name_index for future lookups
            key = norm(yahoo_display)
            if key not in name_index:
                name_index[key] = []
            if uid not in name_index[key]:
                name_index[key].append(uid)

        # Add ascii Name as alt if different from entry name and YahooName
        ascii_name = r.get("Name", "").strip()
        if norm(ascii_name) not in existing_lower and norm(ascii_name) != norm(entry.get("name", "")):
            alt_names.append(ascii_name)
            alt_added += 1

    print(f"\n── UPID Alt-Names ──")
    print(f"  Alt-names added: {alt_added}")

    # ── Save ──
    if apply:
        save_json(COMBINED_PATH, players)
        save_json(UPID_PATH, upid_db)
        print(f"\n✅ Saved changes to combined_players.json and upid_database.json")
    else:
        print(f"\n⚠️  Dry run — use --apply to write changes")


if __name__ == "__main__":
    main()
