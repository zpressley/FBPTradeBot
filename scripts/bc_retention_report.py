"""BC retention report (Spec 03a).

Owned Blue Chip prospects: is he in the Top 100 snapshot?
  Retained (free)           in the snapshot (matched by UPID or MLB ID)
  Must repurchase at PAD    not in the snapshot
  Already graduated         years_simple starts with "TC BC"

Usage:
  python3 scripts/bc_retention_report.py [--snapshot data/pipeline_snapshots/2026-11-01.json]
      [--out scripts/output/bc_retention_<date>.csv] [--allow-unofficial]
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RETAINED, REPURCHASE, GRADUATED = "Retained (free)", "Must repurchase at PAD", "Already graduated"


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def classify(players: list[dict], snapshot: dict) -> list[dict]:
    by_upid = {str(p["upid"]): p["rank"] for p in snapshot["players"] if p.get("upid")}
    by_mlb = {_int(p["mlb_id"]): p["rank"] for p in snapshot["players"] if _int(p.get("mlb_id"))}
    rows = []
    for p in players:
        team = str(p.get("FBP_Team") or "").strip()
        graduated = str(p.get("years_simple") or "").startswith("TC BC")
        if team in ("", "None") or not (graduated or p.get("contract_type") == "Blue Chip Contract"):
            continue
        upid = str(p.get("upid") or "").strip()
        rank = by_upid.get(upid) or by_mlb.get(_int(p.get("mlb_id")))
        if graduated:
            result = GRADUATED
        elif rank:
            result = RETAINED
        else:
            result = REPURCHASE
        rows.append({"team": team, "upid": upid, "name": p.get("name"), "org": p.get("team") or "",
                     "snapshot_rank": rank or "", "result": result})
    rows.sort(key=lambda r: (r["team"], r["result"], r["name"] or ""))
    return rows


def discord_text(rows: list[dict], date: str) -> str:
    by_team = defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)
    lines = [f"**Blue Chip retention - Top 100 snapshot {date}**"]
    for team, items in sorted(by_team.items()):
        lines.append(f"\n**{team}**")
        for r in items:
            rank = f" (#{r['snapshot_rank']})" if r["snapshot_rank"] else ""
            lines.append(f"- {r['name']}{rank}: {r['result']}")
    counts = {k: sum(1 for r in rows if r["result"] == k) for k in (RETAINED, REPURCHASE, GRADUATED)}
    lines.append("\n" + " | ".join(f"{k}: {v}" for k, v in counts.items()))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", default=os.path.join(REPO_ROOT, "data/pipeline_snapshots/2026-11-01.json"))
    ap.add_argument("--players", default=os.path.join(REPO_ROOT, "data/combined_players.json"))
    ap.add_argument("--out", help="CSV path (default scripts/output/bc_retention_<date>.csv)")
    ap.add_argument("--allow-unofficial", action="store_true")
    args = ap.parse_args(argv)

    with open(args.snapshot, encoding="utf-8") as f:
        snapshot = json.load(f)
    if not snapshot.get("official") and not args.allow_unofficial:
        print(f"❌ {args.snapshot} is not marked official. Use --allow-unofficial for a dry run.")
        return 1
    with open(args.players, encoding="utf-8") as f:
        players = json.load(f)

    rows = classify(players, snapshot)
    out = args.out or os.path.join(REPO_ROOT, "scripts/output", f"bc_retention_{snapshot['date']}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["team", "upid", "name", "org", "snapshot_rank", "result"])
        w.writeheader()
        w.writerows(rows)
    print(f"✅ Wrote {len(rows)} rows to {out}\n")
    print(discord_text(rows, snapshot["date"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
