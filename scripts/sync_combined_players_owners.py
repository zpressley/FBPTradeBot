import json
import csv
from pathlib import Path

# Paths
TRADE_BOT_ROOT = Path("/Users/zpressley/fbp-trade-bot")
HUB_ROOT = Path("/Users/zpressley/fbp-hub")

YAHOO_OWNED_2025_CSV = TRADE_BOT_ROOT / "data/historical/2025/yahoo_owned_players_2025.csv"
YAHOO_WITH_UPID_2026_CSV = TRADE_BOT_ROOT / "data/historical/2026/yahoo_all_players_2026_with_upid.csv"
COMBINED_PLAYERS_JSON = HUB_ROOT / "data/combined_players.json"


def load_yahoo_id_to_upid():
    """Build mapping from yahoo_player_id -> upid using 2026 with_upid CSV.

    This lets us respect UPID as the canonical ID while still using Yahoo IDs
    from the 2025 ownership exports.
    """
    mapping = {}
    if not YAHOO_WITH_UPID_2026_CSV.exists():
        print(f"WARN: {YAHOO_WITH_UPID_2026_CSV} not found; will fall back to yahoo_id only")
        return mapping

    with YAHOO_WITH_UPID_2026_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yahoo_id = (row.get("yahoo_player_id") or "").strip()
            upid = (row.get("upid") or "").strip()
            if not yahoo_id or not upid:
                continue
            mapping[yahoo_id] = upid

    print(f"Loaded {len(mapping)} yahoo_id→upid mappings from 2026 CSV")
    return mapping


def load_upid_and_yahoo_to_fbp(yahoo_id_to_upid):
    """Build ownership maps from the 2025 yahoo_owned_players CSV.

    Returns (upid_to_fbp, yahoo_id_to_fbp).
    """
    upid_to_fbp = {}
    yahoo_to_fbp = {}

    if not YAHOO_OWNED_2025_CSV.exists():
        raise SystemExit(f"ERROR: {YAHOO_OWNED_2025_CSV} not found")

    with YAHOO_OWNED_2025_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yahoo_id = (row.get("player_id") or "").strip()
            fbp_abbr = (row.get("fbp_abbr") or "").strip()
            if not yahoo_id or not fbp_abbr:
                continue

            yahoo_to_fbp[yahoo_id] = fbp_abbr

            upid = yahoo_id_to_upid.get(yahoo_id)
            if upid:
                upid_to_fbp[upid] = fbp_abbr

    print(f"Loaded {len(yahoo_to_fbp)} yahoo_id→FBP mappings from 2025 CSV")
    print(f"Resolved {len(upid_to_fbp)} of those to UPID via 2026 CSV")
    return upid_to_fbp, yahoo_to_fbp


def load_combined_players():
    if not COMBINED_PLAYERS_JSON.exists():
        raise SystemExit(f"ERROR: {COMBINED_PLAYERS_JSON} not found")
    with COMBINED_PLAYERS_JSON.open(encoding="utf-8") as f:
        return json.load(f)


def sync_owners():
    yahoo_id_to_upid = load_yahoo_id_to_upid()
    upid_to_fbp, yahoo_to_fbp = load_upid_and_yahoo_to_fbp(yahoo_id_to_upid)

    players = load_combined_players()

    updated = 0
    mlb_count = 0
    missing_owner = 0

    for p in players:
        if p.get("player_type") != "MLB":
            # Farm / prospects already have correct owners per user
            continue
        mlb_count += 1

        upid = (str(p.get("upid")) if p.get("upid") is not None else "").strip()
        yahoo_id = (str(p.get("yahoo_id")) if p.get("yahoo_id") is not None else "").strip()

        new_team = ""
        if upid and upid in upid_to_fbp:
            new_team = upid_to_fbp[upid]
        elif yahoo_id and yahoo_id in yahoo_to_fbp:
            new_team = yahoo_to_fbp[yahoo_id]

        old_team = p.get("FBP_Team", "") or ""
        if not new_team:
            if old_team:
                # Player used to have an owner but is not in 2025 EoS list – treat as FA
                p["FBP_Team"] = ""
                updated += 1
            missing_owner += 1
            continue

        if old_team != new_team:
            p["FBP_Team"] = new_team
            updated += 1

    # Write back
    backup_path = COMBINED_PLAYERS_JSON.with_suffix(".owners_backup.json")
    if not backup_path.exists():
        backup_path.write_text(COMBINED_PLAYERS_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote backup to {backup_path}")

    with COMBINED_PLAYERS_JSON.open("w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Total MLB players: {mlb_count}")
    print(f"Players with updated FBP_Team: {updated}")
    print(f"MLB players without an owner in 2025 CSV (left as FA): {missing_owner}")


if __name__ == "__main__":
    sync_owners()
