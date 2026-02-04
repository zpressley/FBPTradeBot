import json
import csv
from pathlib import Path

TRADE_BOT_ROOT = Path("/Users/zpressley/fbp-trade-bot")
HUB_ROOT = Path("/Users/zpressley/fbp-hub")

YAHOO_ALL_2025_JSON = TRADE_BOT_ROOT / "data/yahoo_all_players_2025.json"
YAHOO_ALL_2026_JSON = TRADE_BOT_ROOT / "data/yahoo_all_players_2026.json"
YAHOO_WITH_UPID_2026_CSV = TRADE_BOT_ROOT / "data/historical/2026/yahoo_all_players_2026_with_upid.csv"
YAHOO_OWNED_2025_CSV = TRADE_BOT_ROOT / "data/historical/2025/yahoo_owned_players_2025.csv"
COMBINED_PLAYERS_JSON = HUB_ROOT / "data/combined_players.json"
OWNERSHIP_DIFF_CSV = TRADE_BOT_ROOT / "data/historical/2026/yahoo_ownership_diff_2025_2026.csv"


def load_json_array(path: Path):
    if not path.exists():
        raise SystemExit(f"ERROR: {path} not found")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"ERROR: {path} did not contain a JSON array")
    return data


def load_yahoo_id_to_upid():
    mapping = {}
    if not YAHOO_WITH_UPID_2026_CSV.exists():
        print(f"WARN: {YAHOO_WITH_UPID_2026_CSV} not found; UPID-based mapping will be partial")
        return mapping

    with YAHOO_WITH_UPID_2026_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yahoo_id = (row.get("yahoo_player_id") or "").strip()
            upid = (row.get("upid") or "").strip()
            if not yahoo_id or not upid:
                continue
            mapping[yahoo_id] = upid
    print(f"Loaded {len(mapping)} yahoo_id→upid mappings from with_upid CSV")
    return mapping


def build_yahoo_maps(players_2025, players_2026):
    """Return (pos_2026_by_id, owner_2025_by_id, owner_2026_by_id,
    name_2025_by_id, name_2026_by_id).

    owner maps hold a tuple (ownership_type, owned_by).
    """
    pos_2026 = {}
    owner_2025 = {}
    owner_2026 = {}
    name_2025 = {}
    name_2026 = {}

    for p in players_2026:
        pid = str(p.get("player_id") or "").strip()
        if not pid:
            continue
        pos_2026[pid] = {
            "position": (p.get("position") or "").strip(),
            "eligible_positions": p.get("eligible_positions"),
            "team": (p.get("team") or "").strip(),
        }
        owner_2026[pid] = (
            (p.get("ownership_type") or "").strip(),
            (p.get("owned_by") or "").strip() or None,
        )
        name_2026[pid] = (p.get("name") or "").strip()

    for p in players_2025:
        pid = str(p.get("player_id") or "").strip()
        if not pid:
            continue
        owner_2025[pid] = (
            (p.get("ownership_type") or "").strip(),
            (p.get("owned_by") or "").strip() or None,
        )
        name_2025[pid] = (p.get("name") or "").strip()

    print(f"Positions available for {len(pos_2026)} Yahoo player_ids in 2026 JSON")
    print(f"Ownership entries: 2025={len(owner_2025)}, 2026={len(owner_2026)}")
    return pos_2026, owner_2025, owner_2026, name_2025, name_2026


def update_combined_positions(pos_2026_by_id, yahoo_id_to_upid):
    players = load_json_array(COMBINED_PLAYERS_JSON)

    updated = 0
    missing = 0

    for p in players:
        yahoo_id = str(p.get("yahoo_id") or "").strip()
        if not yahoo_id:
            continue

        pos_info = pos_2026_by_id.get(yahoo_id)
        if not pos_info:
            # If we can, try to resolve via upid
            upid = str(p.get("upid") or "").strip()
            if upid:
                # reverse lookup
                for yid, u in yahoo_id_to_upid.items():
                    if u == upid:
                        pos_info = pos_2026_by_id.get(yid)
                        if pos_info:
                            break
            if not pos_info:
                missing += 1
                continue

        new_pos = pos_info.get("position") or ""
        if not new_pos:
            continue

        old_pos = p.get("position") or ""
        if old_pos != new_pos:
            p["position"] = new_pos
            # If mlb_primary_position is empty, seed it from the first part of position
            primary = (p.get("mlb_primary_position") or "").strip()
            if not primary:
                primary = new_pos.split(",")[0].strip()
                if primary:
                    p["mlb_primary_position"] = primary
            updated += 1

    backup = COMBINED_PLAYERS_JSON.with_suffix(".positions_backup.json")
    if not backup.exists():
        backup.write_text(COMBINED_PLAYERS_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote position backup to {backup}")

    with COMBINED_PLAYERS_JSON.open("w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Updated positions for {updated} combined_players rows")
    print(f"Players with yahoo_id but no 2026 position entry: {missing}")


def load_team_mappings():
    """Return separate Yahoo team-slot→FBP mappings for 2025 and 2026.

    2025 mapping comes directly from yahoo_owned_players_2025.csv
    (team_id → abbr/manager/team_name for league 458).

    2026 mapping uses the user's provided slot mapping for league 469,
    but still reuses the same FBP abbreviations + manager names from the
    2025 CSV so we don't duplicate that metadata.
    """
    if not YAHOO_OWNED_2025_CSV.exists():
        print(f"WARN: {YAHOO_OWNED_2025_CSV} not found; manager names will be blank")
        return {}, {}

    # First, build abbr → meta from 2025
    abbr_meta = {}
    with YAHOO_OWNED_2025_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            abbr = (row.get("fbp_abbr") or "").strip()
            if not abbr:
                continue
            if abbr in abbr_meta:
                continue
            abbr_meta[abbr] = {
                "fbp_abbr": abbr,
                "team_name": (row.get("team_name") or "").strip(),
                "manager_name": (row.get("manager_name") or "").strip(),
            }

    # 2025: yahoo_team_id → meta
    mapping_2025 = {}
    with YAHOO_OWNED_2025_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                team_id = int(row.get("yahoo_team_id") or 0)
            except ValueError:
                continue
            if not team_id or team_id in mapping_2025:
                continue
            abbr = (row.get("fbp_abbr") or "").strip()
            meta = abbr_meta.get(abbr)
            if meta:
                mapping_2025[team_id] = meta

    # 2026: explicit slot → abbr mapping from user
    slot_to_abbr_2026 = {
        1: "WIZ",
        12: "WAR",
        2: "DRO",
        3: "B2J",
        6: "JEP",
        8: "LAW",
        4: "CFL",
        9: "SAD",
        5: "HAM",
        7: "LFB",
        10: "RV",
        11: "TBB",
    }

    mapping_2026 = {}
    for team_id, abbr in slot_to_abbr_2026.items():
        meta = abbr_meta.get(abbr, {"fbp_abbr": abbr, "team_name": "", "manager_name": ""})
        mapping_2026[team_id] = meta

    print(f"Loaded FBP team metadata for {len(mapping_2025)} Yahoo 2025 slots and {len(mapping_2026)} Yahoo 2026 slots")
    return mapping_2025, mapping_2026


def decode_team(owner_key, map_2025, map_2026):
    """Given a Yahoo owned_by key, return (team_id, fbp_abbr, manager_name, team_name)."""
    owner_key = (owner_key or "").strip()
    if not owner_key:
        return None, "", "", ""

    # Expect something like "458.l.15505.t.3" or "469.l.8560.t.7"
    try:
        tail = owner_key.split(".t.")[-1]
        team_id = int(tail)
    except Exception:
        return None, "", "", ""

    if owner_key.startswith("458."):
        meta = map_2025.get(team_id) or {}
    elif owner_key.startswith("469."):
        meta = map_2026.get(team_id) or {}
    else:
        meta = {}

    return team_id, meta.get("fbp_abbr", ""), meta.get("manager_name", ""), meta.get("team_name", "")


def write_ownership_diffs(owner_2025, owner_2026, name_2025, name_2026, team_index_2025, team_index_2026):
    """Write out ownership mismatches between 2025 and 2026 all-players.

    Only include rows where the actual owner changes (owned_by differs).
    """
    fieldnames = [
        "player_id",
        "player_name",
        "ownership_type_2025",
        "owned_by_2025",
        "fbp_team_2025",
        "manager_2025",
        "team_name_2025",
        "ownership_type_2026",
        "owned_by_2026",
        "fbp_team_2026",
        "manager_2026",
        "team_name_2026",
    ]

    diffs = []

    all_ids = set(owner_2025.keys()) | set(owner_2026.keys())
    for pid in sorted(all_ids, key=lambda x: int(x) if x.isdigit() else x):
        t25, o25 = owner_2025.get(pid, ("", None))
        t26, o26 = owner_2026.get(pid, ("", None))

        # Decode team slots and FBP teams
        team_id_25, fbp25, mgr25, team25 = decode_team(o25, team_index_2025, team_index_2026)
        team_id_26, fbp26, mgr26, team26 = decode_team(o26, team_index_2025, team_index_2026)

        # If both resolve to the same FBP team (even if slot changed), treat as no change
        if fbp25 and fbp25 == fbp26:
            continue

        # Also ignore rows where the raw owned_by key is unchanged
        if (o25 or None) == (o26 or None):
            continue

        # Prefer 2026 name, fall back to 2025
        name = name_2026.get(pid) or name_2025.get(pid) or ""

        diffs.append(
            {
                "player_id": pid,
                "player_name": name,
                "ownership_type_2025": t25,
                "owned_by_2025": o25 or "",
                "fbp_team_2025": fbp25,
                "manager_2025": mgr25,
                "team_name_2025": team25,
                "ownership_type_2026": t26,
                "owned_by_2026": o26 or "",
                "fbp_team_2026": fbp26,
                "manager_2026": mgr26,
                "team_name_2026": team26,
            }
        )

    if not diffs:
        print("No ownership differences detected between 2025 and 2026 JSON")
        return

    OWNERSHIP_DIFF_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OWNERSHIP_DIFF_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(diffs)

    print(f"Wrote {len(diffs)} ownership diff rows to {OWNERSHIP_DIFF_CSV}")


def main():
    players_2025 = load_json_array(YAHOO_ALL_2025_JSON)
    players_2026 = load_json_array(YAHOO_ALL_2026_JSON)

    yahoo_id_to_upid = load_yahoo_id_to_upid()
    pos_2026_by_id, owner_2025, owner_2026, name_2025, name_2026 = build_yahoo_maps(players_2025, players_2026)

    print("=== Updating combined_players positions from 2026 Yahoo data ===")
    update_combined_positions(pos_2026_by_id, yahoo_id_to_upid)

    print("=== Checking ownership differences between 2025 and 2026 ===")
    team_index_2025, team_index_2026 = load_team_mappings()
    write_ownership_diffs(owner_2025, owner_2026, name_2025, name_2026, team_index_2025, team_index_2026)


if __name__ == "__main__":
    main()
