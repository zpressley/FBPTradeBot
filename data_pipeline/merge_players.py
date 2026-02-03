import json
import os

YAHOO_FILE = "data/yahoo_players.json"
# SHEET_FILE is intentionally no longer part of the live pipeline. It
# remains in the repo as a historical snapshot of the legacy Google
# Sheets “Player Data” tab.
SHEET_FILE = "data/sheet_players.json"  # legacy/historical only
OUTPUT_FILE = "data/combined_players.json"
UPID_DB_FILE = "data/upid_database.json"
MLB_ID_CACHE_FILE = "data/mlb_id_cache.json"
MLB_TEAM_MAP_FILE = "data/mlb_team_map.json"
MANAGERS_FILE = "config/managers.json"


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def load_optional_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        return load_json(path)
    except Exception:
        return default


def merge_players(yahoo_data, _legacy_sheet_data=None):
    """Merge Yahoo roster snapshot with existing combined_players data.

    Live pipeline behavior (post-2026):
    - We NO LONGER seed from sheet_players.json or Google Sheets.
    - Existing combined_players.json is treated as the base of truth for
      contracts, prospect flags, ranks, etc.
    - Yahoo roster info (team/position/FBP owner) is layered on top.
    - FBP_Team and display manager name are driven by config/managers.json.
    - Matching prefers UPID when we have it, otherwise falls back to
      exact lowercased name.
    """

    combined_by_key = {}
    existing_name_index = {}

    # Load identity/ID helpers
    upid_db = load_optional_json(UPID_DB_FILE, {"by_upid": {}, "name_index": {}})
    by_upid = upid_db.get("by_upid", {})
    name_index = upid_db.get("name_index", {})

    mlb_id_cache = load_optional_json(MLB_ID_CACHE_FILE, {})
    team_map = load_optional_json(MLB_TEAM_MAP_FILE, {"aliases": {}, "official": {}})
    alias_map = team_map.get("aliases", {})

    managers_cfg = load_optional_json(MANAGERS_FILE, {"teams": {}})
    teams_cfg = managers_cfg.get("teams", {})

    def canonical_team(team: str) -> str:
        key = (team or "").strip().lower()
        if not key:
            return ""
        return alias_map.get(key, team)

    def find_upid_for_player(name: str, mlb_team: str) -> str:
        """Best-effort UPID lookup using UPID DB name index and team.

        - First, use exact lowercase name in name_index.
        - If multiple candidates, try to disambiguate by team using
          canonical MLB team mapping.
        """

        key = (name or "").strip().lower()
        if not key:
            return ""
        candidates = name_index.get(key, [])
        if not candidates:
            return ""
        if len(candidates) == 1 or not mlb_team:
            return candidates[0]

        canon_yahoo = canonical_team(mlb_team)
        narrowed = []
        for upid in candidates:
            rec = by_upid.get(upid) or {}
            rec_team = rec.get("team") or ""
            if not rec_team:
                narrowed.append(upid)
                continue
            if canonical_team(rec_team) == canon_yahoo:
                narrowed.append(upid)

        if len(narrowed) == 1:
            return narrowed[0]
        return ""

    def key_for(upid: str, name: str) -> tuple:
        if upid:
            return ("upid", upid)
        return ("name", name.lower())

    def owner_labels(fbp_team: str) -> tuple[str, str]:
        """Return (FBP_Team code, display manager name) for a team.

        - FBP_Team code is the canonical abbreviation (teams[key].manager
          if present, otherwise the key itself).
        - Display name is teams[key].name or the code.
        """

        meta = teams_cfg.get(fbp_team, {})
        code = (meta.get("manager") or fbp_team or "").strip() or fbp_team
        name = (meta.get("name") or code).strip()
        return code, name

    # 1) Seed from existing combined_players.json (if present)
    existing = load_optional_json(OUTPUT_FILE, [])
    for rec in existing:
        name = (rec.get("name") or "").strip()
        upid = (rec.get("upid") or "").strip()
        if not name and not upid:
            continue
        k = key_for(upid, name)
        combined_by_key[k] = rec
        if name:
            existing_name_index.setdefault(name.lower(), []).append(k)

    # 2) Layer Yahoo roster info on top
    for fbp_team, roster in yahoo_data.items():
        for player in roster:
            name = (player.get("name") or "").strip()
            pos = (player.get("position") or "").strip()
            team = (player.get("team") or "").strip()
            yahoo_id = str(player.get("yahoo_id") or "").strip()

            if not name and not yahoo_id:
                continue

            # Try to find a matching existing record by exact name
            key = None
            if name:
                for k in existing_name_index.get(name.lower(), []):
                    key = k
                    break

            if key is None:
                # Player not in existing combined file (e.g., new FA only in Yahoo).
                # Try to attach a UPID via the UPID database.
                guessed_upid = find_upid_for_player(name, team)
                mlb_id = None
                if guessed_upid and guessed_upid in mlb_id_cache:
                    mlb_id = mlb_id_cache[guessed_upid].get("mlb_id")

                # Create a new MLB record keyed off Yahoo id + name.
                key = ("yahoo", yahoo_id or name.lower())
                if key not in combined_by_key:
                    fbp_code, display_name = owner_labels(fbp_team)
                    combined_by_key[key] = {
                        "name": name,
                        "team": team,
                        "position": pos,
                        "FBP_Team": fbp_code,
                        "manager": display_name,
                        "player_type": "MLB",
                        "contract_type": "",
                        "status": "",
                        "years_simple": "",
                        "yahoo_id": yahoo_id,
                        "upid": guessed_upid or "",
                        "mlb_id": mlb_id,
                    }
                    continue

            # Update the existing record with live Yahoo + ownership info
            rec = combined_by_key[key]
            if team:
                rec["team"] = team
            if pos:
                rec["position"] = pos

            # Yahoo is the source of truth for current FBP owner; we map
            # this into FBP_Team (code) and display manager name.
            fbp_code, display_name = owner_labels(fbp_team)
            rec["FBP_Team"] = fbp_code
            rec["manager"] = display_name
            if yahoo_id:
                rec["yahoo_id"] = yahoo_id

    # Final pass: attach MLB IDs for any record with a UPID
    for rec in combined_by_key.values():
        upid_val = (rec.get("upid") or "").strip()
        if upid_val and upid_val in mlb_id_cache:
            rec["mlb_id"] = mlb_id_cache[upid_val].get("mlb_id")

    return list(combined_by_key.values())


def save_json(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Combined data saved to {path} with {len(data)} players.")


if __name__ == "__main__":
    yahoo = load_optional_json(YAHOO_FILE, {})
    # Legacy sheet data is ignored by the live pipeline, but we keep the
    # parameter for merge_players so historical/one-off workflows can
    # still pass it if needed.
    legacy_sheet = load_optional_json(SHEET_FILE, [])
    merged = merge_players(yahoo, legacy_sheet)
    save_json(merged, OUTPUT_FILE)
