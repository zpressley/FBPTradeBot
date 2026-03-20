"""
calculate_baselines.py
----------------------
Lives in the email-digest repo. Runs daily in the FBP data pipeline
AFTER update_yahoo_players.py.

Architecture:
  - Daily snapshots → email-digest repo: data/baselines/YYYY-MM-DD.json
    Each snapshot stores SEASON-CUMULATIVE totals per stat (not daily increments).
    This is the right thing to store because windowed baselines are derived
    by diffing two snapshots: window_total = snapshot[today] - snapshot[today - N].

  - league_baselines.json → fbp-hub repo (read by Chrome extension)
    Five timeframes: today, last7, last14, last30, season
    All values are raw totals averaged per rostered player for that window.
    Rate stats (ERA, K/9, H/9, BB/9) are IP-weighted, not averaged.

Window math example (Last 7 K/9):
  K_last7  = total_K[today]  - total_K[today-7]
  IP_last7 = total_IP[today] - total_IP[today-7]
  K/9_last7 = (K_last7 / IP_last7) * 9
  baseline  = K/9_last7   ← the per-league-average K/9 for that window

"Today" timeframe = season stats fetched at today's date with no diffing.
"""

import json
import os
import sys
import requests
from datetime import date, timedelta
from xml.etree import ElementTree as ET

# ── Repo paths ───────────────────────────────────────────────────────────────
# Override via env vars in GitHub Actions when repos are checked out separately.
# email-digest repo — where daily snapshots are stored
EMAIL_DIGEST_DATA  = os.getenv("EMAIL_DIGEST_DATA_DIR", "../email-digest/data")
BASELINES_DIR      = os.path.join(EMAIL_DIGEST_DATA, "baselines")

# fbp-hub repo — where the extension reads league_baselines.json from
HUB_DATA_DIR       = os.getenv("FBP_HUB_DATA_DIR", "../fbp-hub/data")
BASELINES_OUT      = os.path.join(HUB_DATA_DIR, "league_baselines.json")

# fbp-trade-bot repo — yahoo_players.json lives here
YAHOO_FILE         = "data/yahoo_players.json"

# ── Yahoo API ────────────────────────────────────────────────────────────────
GAME_KEY_2026 = "469"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_manager import get_access_token

# ── Stat ID map ──────────────────────────────────────────────────────────────
# Yahoo MLB stat IDs. NA = derived from other stats, not fetched directly.
BATTER_STAT_IDS = {
    "R":   "12",
    "H":   "8",
    "HR":  "16",
    "RBI": "13",
    "SB":  "21",
    "BB":  "18",
    "K":   "27",
    "AVG": "3",
    "OPS": "55",
    # TB not directly available from Yahoo — stubbed, see note in compute fn
}

PITCHER_STAT_IDS = {
    "APP": "48",
    "IP":  "50",   # not scored, needed for rate weighting
    "ER":  "58",
    "HR":  "59",
    "K":   "62",
    "QS":  "82",
    # Components for derived rate stats:
    "H_allowed":  "57",
    "BB_allowed": "61",
    # ERA, K/9, H/9, BB/9 derived from above — not fetched
    # TB allowed — not cleanly available, stubbed
}

# All real (non-derived) stat IDs to request in one API call
FETCH_STAT_IDS = sorted(set(
    v for v in list(BATTER_STAT_IDS.values()) + list(PITCHER_STAT_IDS.values())
    if v != "NA"
))

PITCHER_POSITIONS = {"SP", "RP", "P"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_pitcher(position: str) -> bool:
    return bool({p.strip() for p in position.split(",")} & PITCHER_POSITIONS)

def safe_div(n: float, d: float, default=0.0) -> float:
    return n / d if d else default

def r3(v: float) -> float:
    return round(v, 3)

def get_stat(stat_map: dict, stat_id: str) -> float:
    return float(stat_map.get(stat_id, 0) or 0)


# ── Yahoo fetch ───────────────────────────────────────────────────────────────

def fetch_stats_batch(yahoo_ids: list, stat_type: str) -> dict:
    """
    Fetch stats for up to 25 players. Returns {yahoo_id: {stat_id: value}}.
    stat_type: "season" for full season cumulative totals.
    Yahoo doesn't expose true rolling windows via the players endpoint cleanly,
    so we always fetch season totals and derive windows from snapshot diffs.
    """
    token   = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    keys    = ",".join(f"{GAME_KEY_2026}.p.{pid}" for pid in yahoo_ids)
    url     = (
        f"https://fantasysports.yahooapis.com/fantasy/v2/players;"
        f"player_keys={keys};out=stats(type={stat_type})"
    )

    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"  ⚠️  Yahoo fetch failed ({stat_type}): {resp.status_code}")
        return {}

    results = {}
    try:
        ns   = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
        root = ET.fromstring(resp.text)
        for player_el in root.findall(".//y:player", ns):
            pid_el = player_el.find("y:player_id", ns)
            if pid_el is None:
                continue
            pid      = pid_el.text
            stat_map = {}
            for stat_el in player_el.findall(".//y:stat", ns):
                sid = stat_el.findtext("y:stat_id", default="", namespaces=ns)
                val = stat_el.findtext("y:value",   default="0",  namespaces=ns)
                try:
                    stat_map[sid] = float(val or 0)
                except ValueError:
                    stat_map[sid] = 0.0
            results[pid] = stat_map
    except ET.ParseError as e:
        print(f"  ⚠️  XML parse error: {e}")

    return results


def fetch_all_stats(yahoo_ids: list, stat_type: str = "season") -> dict:
    all_results = {}
    for i in range(0, len(yahoo_ids), 25):
        batch = yahoo_ids[i:i+25]
        print(f"  → batch {i//25 + 1} ({len(batch)} players)...")
        all_results.update(fetch_stats_batch(batch, stat_type))
    return all_results


# ── Aggregate raw totals ──────────────────────────────────────────────────────
# Snapshots store raw league-wide totals (sum across all rostered players),
# not per-player averages. This makes windowed diffs straightforward.
# Per-player averages are computed at output time by dividing by player count.

def aggregate_batter_totals(stat_list: list) -> dict:
    """Sum raw counting stats across all rostered batters."""
    t = {k: 0.0 for k in ["R","H","HR","RBI","SB","BB","K","AVG","OPS"]}
    for s in stat_list:
        t["R"]   += get_stat(s, BATTER_STAT_IDS["R"])
        t["H"]   += get_stat(s, BATTER_STAT_IDS["H"])
        t["HR"]  += get_stat(s, BATTER_STAT_IDS["HR"])
        t["RBI"] += get_stat(s, BATTER_STAT_IDS["RBI"])
        t["SB"]  += get_stat(s, BATTER_STAT_IDS["SB"])
        t["BB"]  += get_stat(s, BATTER_STAT_IDS["BB"])
        t["K"]   += get_stat(s, BATTER_STAT_IDS["K"])
        t["AVG"] += get_stat(s, BATTER_STAT_IDS["AVG"])
        t["OPS"] += get_stat(s, BATTER_STAT_IDS["OPS"])
        # TB: stubbed — Yahoo doesn't expose cleanly; add stat ID when confirmed
        # t["TB"] += get_stat(s, BATTER_STAT_IDS["TB"])
    t["TB"] = 0.0   # placeholder
    return t


def aggregate_pitcher_totals(stat_list: list) -> dict:
    """
    Sum raw totals across all rostered pitchers.
    Stores IP, H_allowed, BB_allowed as raw totals so rate stats
    can be IP-weighted correctly during windowed diffs.
    """
    t = {k: 0.0 for k in ["APP","IP","ER","HR","K","QS","H_allowed","BB_allowed"]}
    for s in stat_list:
        t["APP"]       += get_stat(s, PITCHER_STAT_IDS["APP"])
        t["IP"]        += get_stat(s, PITCHER_STAT_IDS["IP"])
        t["ER"]        += get_stat(s, PITCHER_STAT_IDS["ER"])
        t["HR"]        += get_stat(s, PITCHER_STAT_IDS["HR"])
        t["K"]         += get_stat(s, PITCHER_STAT_IDS["K"])
        t["QS"]        += get_stat(s, PITCHER_STAT_IDS["QS"])
        t["H_allowed"] += get_stat(s, PITCHER_STAT_IDS["H_allowed"])
        t["BB_allowed"]+= get_stat(s, PITCHER_STAT_IDS["BB_allowed"])
    t["TB"] = 0.0   # placeholder
    return t


# ── Derive baselines from raw totals ──────────────────────────────────────────

def batter_baseline_from_totals(totals: dict, n_players: int) -> dict:
    """Convert league-wide totals to per-player averages (the baseline)."""
    if not n_players:
        return {}
    return {
        "R":   r3(safe_div(totals["R"],   n_players)),
        "H":   r3(safe_div(totals["H"],   n_players)),
        "HR":  r3(safe_div(totals["HR"],  n_players)),
        "RBI": r3(safe_div(totals["RBI"], n_players)),
        "SB":  r3(safe_div(totals["SB"],  n_players)),
        "BB":  r3(safe_div(totals["BB"],  n_players)),
        "K":   r3(safe_div(totals["K"],   n_players)),
        "TB":  0.0,
        "AVG": r3(safe_div(totals["AVG"], n_players)),
        "OPS": r3(safe_div(totals["OPS"], n_players)),
    }


def pitcher_baseline_from_totals(totals: dict, n_players: int) -> dict:
    """
    Convert league-wide pitcher totals to baselines.
    Counting stats → per-player average.
    Rate stats → IP-weighted from raw components (not averaged).
    """
    if not n_players:
        return {}
    ip = totals["IP"]
    return {
        "APP":  r3(safe_div(totals["APP"], n_players)),
        "ER":   r3(safe_div(totals["ER"],  n_players)),
        "HR":   r3(safe_div(totals["HR"],  n_players)),
        "K":    r3(safe_div(totals["K"],   n_players)),
        "QS":   r3(safe_div(totals["QS"],  n_players)),
        "TB":   0.0,
        # IP-weighted rates — the right way to compute league-average K/9 etc.
        "ERA":  r3(safe_div(totals["ER"]        * 9,  ip)),
        "K/9":  r3(safe_div(totals["K"]         * 9,  ip)),
        "H/9":  r3(safe_div(totals["H_allowed"] * 9,  ip)),
        "BB/9": r3(safe_div(totals["BB_allowed"]* 9,  ip)),
    }


# ── Snapshot diff for windowed baselines ──────────────────────────────────────

def load_snapshot(d: date) -> dict | None:
    path = os.path.join(BASELINES_DIR, f"{d.isoformat()}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def diff_totals(a: dict, b: dict) -> dict:
    """a - b for every key. Used to get window totals from cumulative snapshots."""
    return {k: r3(a.get(k, 0.0) - b.get(k, 0.0)) for k in a}


def window_baseline(today_snap: dict, past_snap: dict | None, window_label: str) -> dict:
    """
    Derive batter + pitcher baselines for a window by diffing two snapshots.
    If no past snapshot exists (early season), falls back to today's season totals.
    """
    if past_snap is None:
        print(f"  ⚠️  No snapshot found for {window_label} lookback — using season totals")
        past_bat  = {k: 0.0 for k in today_snap["batter_totals"]}
        past_pit  = {k: 0.0 for k in today_snap["pitcher_totals"]}
        past_n_b  = 0
        past_n_p  = 0
    else:
        past_bat  = past_snap["batter_totals"]
        past_pit  = past_snap["pitcher_totals"]
        past_n_b  = past_snap["n_batters"]
        past_n_p  = past_snap["n_pitchers"]

    # Use today's player count (roster composition may shift day-to-day;
    # today's count is the most accurate representation of "current league")
    n_b = today_snap["n_batters"]
    n_p = today_snap["n_pitchers"]

    bat_window = diff_totals(today_snap["batter_totals"],  past_bat)
    pit_window = diff_totals(today_snap["pitcher_totals"], past_pit)

    return {
        "batters":  batter_baseline_from_totals(bat_window, n_b),
        "pitchers": pitcher_baseline_from_totals(pit_window, n_p),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    print(f"\n📊 FBP Baseline Calculator — {today.isoformat()}")
    print("=" * 55)

    # 1. Load rostered players from yahoo_players.json
    if not os.path.exists(YAHOO_FILE):
        print(f"❌ {YAHOO_FILE} not found — run update_yahoo_players.py first.")
        return

    with open(YAHOO_FILE) as f:
        yahoo_data = json.load(f)

    batter_ids  = []
    pitcher_ids = []
    for roster in yahoo_data.values():
        for player in roster:
            pid = player.get("yahoo_id", "")
            pos = player.get("position", "")
            if not pid:
                continue
            (pitcher_ids if is_pitcher(pos) else batter_ids).append(pid)

    print(f"✅ Roster: {len(batter_ids)} batters, {len(pitcher_ids)} pitchers")

    # 2. Fetch season cumulative stats from Yahoo
    print("\n🔄 Fetching season stats from Yahoo...")
    bat_raw  = fetch_all_stats(batter_ids,  "season")
    pit_raw  = fetch_all_stats(pitcher_ids, "season")

    bat_stats = [bat_raw[pid] for pid in batter_ids  if pid in bat_raw]
    pit_stats = [pit_raw[pid] for pid in pitcher_ids if pid in pit_raw]
    print(f"  Got: {len(bat_stats)}/{len(batter_ids)} batters, "
          f"{len(pit_stats)}/{len(pitcher_ids)} pitchers")

    # 3. Aggregate raw totals
    bat_totals = aggregate_batter_totals(bat_stats)
    pit_totals = aggregate_pitcher_totals(pit_stats)

    # 4. Write today's snapshot (cumulative season totals — NOT per-player avgs)
    os.makedirs(BASELINES_DIR, exist_ok=True)
    today_snap = {
        "date":           today.isoformat(),
        "n_batters":      len(bat_stats),
        "n_pitchers":     len(pit_stats),
        "batter_totals":  bat_totals,
        "pitcher_totals": pit_totals,
    }
    snap_path = os.path.join(BASELINES_DIR, f"{today.isoformat()}.json")
    with open(snap_path, "w") as f:
        json.dump(today_snap, f, indent=2)
    print(f"\n✅ Snapshot saved → {snap_path}")

    # 5. Load past snapshots for windowed diffs
    snap_7  = load_snapshot(today - timedelta(days=7))
    snap_14 = load_snapshot(today - timedelta(days=14))
    snap_30 = load_snapshot(today - timedelta(days=30))

    # 6. Derive baselines for each timeframe
    #    "today" = season totals at today's roster composition (no diff needed)
    today_baseline = {
        "batters":  batter_baseline_from_totals(bat_totals, len(bat_stats)),
        "pitchers": pitcher_baseline_from_totals(pit_totals, len(pit_stats)),
    }
    last7_baseline  = window_baseline(today_snap, snap_7,  "last7")
    last14_baseline = window_baseline(today_snap, snap_14, "last14")
    last30_baseline = window_baseline(today_snap, snap_30, "last30")

    # 7. Write league_baselines.json (read by Chrome extension)
    os.makedirs(HUB_DATA_DIR, exist_ok=True)
    league_baselines = {
        "_generated":     today.isoformat(),
        "_n_batters":     len(bat_stats),
        "_n_pitchers":    len(pit_stats),
        "batters": {
            "season": today_baseline["batters"],
            "last30": last30_baseline["batters"],
            "last14": last14_baseline["batters"],
            "last7":  last7_baseline["batters"],
            "today":  today_baseline["batters"],  # same as season at daily granularity
        },
        "pitchers": {
            "season": today_baseline["pitchers"],
            "last30": last30_baseline["pitchers"],
            "last14": last14_baseline["pitchers"],
            "last7":  last7_baseline["pitchers"],
            "today":  today_baseline["pitchers"],
        }
    }

    with open(BASELINES_OUT, "w") as f:
        json.dump(league_baselines, f, indent=2)
    print(f"✅ league_baselines.json → {BASELINES_OUT}")

    # 8. Print summary
    print(f"\n📈 Season batter baselines (per player):")
    for k, v in today_baseline["batters"].items():
        print(f"   {k:<6} {v}")
    print(f"\n⚾ Season pitcher baselines (per player, rates IP-weighted):")
    for k, v in today_baseline["pitchers"].items():
        print(f"   {k:<6} {v}")


if __name__ == "__main__":
    main()
