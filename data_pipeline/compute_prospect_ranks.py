import json
import csv
import math
import os
from collections import defaultdict
from statistics import mean, pstdev

COMBINED_PATH = "data/combined_players.json"
FYPD_RANKS_PATH = "data/fypd_2026_rankings.json"
TOP100_PATH = "data/historical/2025/top100_prospects.json"
UPID_DB_PATH = "data/upid_database.json"
BAT_STATS_PATH = "data/stats/prospect_stats/mlb_prospect_batstats_2025.csv"
PITCH_STATS_PATH = "data/stats/prospect_stats/mlb_prospect_pitchstats_2025.csv"


def load_json(path, default):
    if not os.path.exists(path):
        print(f"⚠️ {path} not found; using default")
        return default
    with open(path, "r") as f:
        return json.load(f)


def zscores(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {}
    mu = mean(vals)
    sigma = pstdev(vals) or 1.0
    return {i: (v - mu) / sigma for i, v in enumerate(values) if v is not None}


def main():
    # 1) Load base data
    combined = load_json(COMBINED_PATH, [])
    fypd_raw = load_json(FYPD_RANKS_PATH, {"players": []})
    top100 = load_json(TOP100_PATH, [])
    upid_db = load_json(UPID_DB_PATH, {"by_upid": {}}).get("by_upid", {})

    # Map UPID -> base player record (Farm prospects only)
    prospects = {}
    for p in combined:
        if p.get("player_type") != "Farm":
            continue
        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue
        prospects[upid] = p

    # FYPD ranks
    fypd_rank_by_upid = {}
    for row in fypd_raw.get("players", []):
        upid = str(row.get("upid") or "").strip()
        r = row.get("rank")
        if upid and isinstance(r, int):
            fypd_rank_by_upid[upid] = r

    # Top 100 global ranks (fixed 1..100)
    top100_rank_by_upid = {}
    for row in top100:
        upid = str(row.get("upid") or "").strip()
        r = row.get("rank")
        if upid and isinstance(r, int):
            top100_rank_by_upid[upid] = r

    # Stats: build UPID <-stats-> via mlb_id / name
    def build_stats_index(csv_path, is_batting):
        if not os.path.exists(csv_path):
            print(f"⚠️ {csv_path} not found; skipping")
            return {}, {}
        by_upid = {}
        team_rank_by_upid = {}

        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                full_name = (row.get("full_name") or "").strip()
                player_id = row.get("playerId") or row.get("player_id")
                team_rank = row.get("rank")

                # Find matching UPID via UPID database name/alt_names
                matched_upid = None
                norm_full = full_name.lower()
                for upid, meta in upid_db.items():
                    name = (meta.get("name") or "").strip().lower()
                    if name == norm_full:
                        matched_upid = upid
                        break
                    for alt in meta.get("alt_names", []):
                        if (alt or "").strip().lower() == norm_full:
                            matched_upid = upid
                            break
                    if matched_upid:
                        break

                if not matched_upid or matched_upid not in prospects:
                    continue

                # Attach row
                by_upid[matched_upid] = row
                if team_rank and team_rank.isdigit():
                    tr = int(team_rank)
                    existing = team_rank_by_upid.get(matched_upid)
                    team_rank_by_upid[matched_upid] = tr if existing is None else min(existing, tr)

        kind = "batting" if is_batting else "pitching"
        print(f"✅ Mapped {len(by_upid)} {kind} stat rows to UPIDs")
        return by_upid, team_rank_by_upid

    bat_stats_by_upid, bat_team_rank = build_stats_index(BAT_STATS_PATH, True)
    pit_stats_by_upid, pit_team_rank = build_stats_index(PITCH_STATS_PATH, False)

    # Merge team_rank from both
    team_rank_by_upid = {}
    for upid in set(list(bat_team_rank.keys()) + list(pit_team_rank.keys())):
        ranks = []
        if upid in bat_team_rank:
            ranks.append(bat_team_rank[upid])
        if upid in pit_team_rank:
            ranks.append(pit_team_rank[upid])
        if ranks:
            team_rank_by_upid[upid] = min(ranks)

    # --- Build z-scores for hitters ---
    bat_upids = [u for u in bat_stats_by_upid.keys() if u not in top100_rank_by_upid]
    bat_metrics = {
        "hits": [],
        "homeRuns": [],
        "avg": [],
        "ops": [],
        "baseOnBalls": [],
        "stolenBases": [],
    }
    for u in bat_upids:
        row = bat_stats_by_upid[u]
        for k in bat_metrics.keys():
            v = row.get(k) or row.get(k.lower())
            try:
                bat_metrics[k].append(float(v))
            except (TypeError, ValueError):
                bat_metrics[k].append(None)

    bat_z_by_upid = defaultdict(list)
    for metric, vals in bat_metrics.items():
        idx_to_z = zscores(vals)
        for idx, z in idx_to_z.items():
            u = bat_upids[idx]
            bat_z_by_upid[u].append(z)

    bat_score = {}
    for u, zs in bat_z_by_upid.items():
        if zs:
            bat_score[u] = sum(zs) / len(zs)

    # --- Z-scores for pitchers ---
    pit_upids = [u for u in pit_stats_by_upid.keys() if u not in top100_rank_by_upid]
    pit_metrics_pos = {"strikeOuts": [], "inningsPitched": []}
    pit_metrics_neg = {"earnedRuns": [], "era": [], "whip": []}

    def _num(row, key):
        v = row.get(key) or row.get(key.lower())
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for u in pit_upids:
        row = pit_stats_by_upid[u]
        for k in pit_metrics_pos.keys():
            pit_metrics_pos[k].append(_num(row, k))
        for k in pit_metrics_neg.keys():
            pit_metrics_neg[k].append(_num(row, k))

    pit_z_raw = defaultdict(list)
    for metric, vals in pit_metrics_pos.items():
        idx_to_z = zscores(vals)
        for idx, z in idx_to_z.items():
            u = pit_upids[idx]
            pit_z_raw[u].append(z)
    for metric, vals in pit_metrics_neg.items():
        idx_to_z = zscores(vals)
        for idx, z in idx_to_z.items():
            u = pit_upids[idx]
            pit_z_raw[u].append(-idx_to_z[idx])  # invert: lower is better

    pit_score = {}
    for u, zs in pit_z_raw.items():
        if zs:
            pit_score[u] = sum(zs) / len(zs)

    # --- Cross-type normalisation ---
    bat_vals = list(bat_score.values())
    pit_vals = list(pit_score.values())
    if bat_vals:
        mu_b, sd_b = mean(bat_vals), pstdev(bat_vals) or 1.0
    else:
        mu_b, sd_b = 0.0, 1.0
    if pit_vals:
        mu_p, sd_p = mean(pit_vals), pstdev(pit_vals) or 1.0
    else:
        mu_p, sd_p = 0.0, 1.0

    stats_z = {}
    for u, z in bat_score.items():
        stats_z[u] = (z - mu_b) / sd_b
    for u, z in pit_score.items():
        z_norm = (z - mu_p) / sd_p
        if u in stats_z:
            stats_z[u] = (stats_z[u] + z_norm) / 2.0
        else:
            stats_z[u] = z_norm

    # Convert stats_z into 0..1 score (0 best)
    if stats_z:
        ordered = sorted(stats_z.items(), key=lambda x: x[1], reverse=True)
        n = len(ordered)
        stats_score = {}
        for idx, (u, _) in enumerate(ordered):
            stats_score[u] = idx / (n - 1) if n > 1 else 0.0
    else:
        stats_score = {}

    # Team-rank score 0..1 (0 best)
    if team_rank_by_upid:
        max_tr = max(team_rank_by_upid.values())
    else:
        max_tr = 1
    team_rank_score = {}
    for u in prospects.keys():
        tr = team_rank_by_upid.get(u)
        if tr is None or max_tr <= 1:
            team_rank_score[u] = 0.5
        else:
            team_rank_score[u] = (tr - 1) / (max_tr - 1)

    # FYPD score 0..1 (0 best)
    if fypd_rank_by_upid:
        max_fr = max(fypd_rank_by_upid.values())
    else:
        max_fr = 1
    fypd_score = {}
    for u, r in fypd_rank_by_upid.items():
        if max_fr <= 1:
            fypd_score[u] = 0.5
        else:
            fypd_score[u] = (r - 1) / (max_fr - 1)

    # Composite scores for non-top-100 prospects
    composite = {}
    for upid, p in prospects.items():
        if upid in top100_rank_by_upid:
            continue
        is_fypd = upid in fypd_rank_by_upid
        tr_s = team_rank_score.get(upid, 0.5)

        if is_fypd:
            # 60% team rank, 40% FYPD rank
            fr_s = fypd_score.get(upid, 0.5)
            comp = 0.6 * tr_s + 0.4 * fr_s
        else:
            s_s = stats_score.get(upid)
            if s_s is None:
                # No stats, no FYPD: push to bottom; tiebreak by name later
                comp = 1.0
            else:
                comp = 0.6 * tr_s + 0.4 * s_s
        composite[upid] = comp

    # Turn composite scores into ranks starting at 101
    # Sort by (score asc, name asc)
    rest = []
    for upid, score in composite.items():
        name = (prospects[upid].get("name") or "").strip()
        rest.append((score, name.lower(), upid))
    rest.sort(key=lambda x: (x[0], x[1]))

    rank_by_upid = {}
    current_rank = 101
    for _score, _name, upid in rest:
        rank_by_upid[upid] = current_rank
        current_rank += 1

    # Apply ranks/fypd_rank back into combined_players.json
    for p in combined:
        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue
        if upid in top100_rank_by_upid:
            p["rank"] = top100_rank_by_upid[upid]
        elif upid in rank_by_upid:
            p["rank"] = rank_by_upid[upid]
        # else leave existing rank if present, or None

        if upid in fypd_rank_by_upid:
            p["fypd_rank"] = fypd_rank_by_upid[upid]

    with open(COMBINED_PATH, "w") as f:
        json.dump(combined, f, indent=2)

    print(f"✅ Updated combined_players.json with composite ranks for {len(rank_by_upid)} prospects")


if __name__ == "__main__":
    main()
