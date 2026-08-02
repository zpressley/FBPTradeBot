#!/usr/bin/env python3
"""Read-only integrity cleanse for data/combined_players.json.

Checks for the failure patterns this engagement has repeatedly hit in
production: yahoo_id/mlb_id collisions across UPIDs, records missing the
FBP_Team key entirely (owned-but-invisible), FBP_Team/manager mismatches,
duplicate-person records (same name+team+position under different UPIDs --
the Luis Garcia Jr. 8696/8697 pattern, which no ID collision would catch
since one side had a blank yahoo_id), contract/status inconsistencies, and
debuted-but-still-Farm players (the McGreevy graduation-gap pattern).

Does not write anything. Prints a structured report.
"""

import json
import re
import unicodedata
from collections import Counter, defaultdict

COMBINED_FILE = "data/combined_players.json"
UPID_DB_FILE = "data/upid_database.json"

KNOWN_TEAMS = {"WIZ", "B2J", "CFL", "HAM", "RV", "SAD", "JEP", "TBB", "DRO", "DMN", "LFB", "WAR"}


def norm_name(name):
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    n = n.lower().strip()
    n = re.sub(r"[.\-']", "", n)
    n = re.sub(r"\s+jr\b", "", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def main():
    players = json.load(open(COMBINED_FILE, encoding="utf-8"))
    upid_db = json.load(open(UPID_DB_FILE, encoding="utf-8"))
    by_upid_meta = upid_db.get("by_upid", {})

    print(f"Loaded {len(players)} players from {COMBINED_FILE}\n")

    # ---------------------------------------------------------------
    # A. Schema-level issues
    # ---------------------------------------------------------------
    print("=" * 78)
    print("A. SCHEMA-LEVEL ISSUES")
    print("=" * 78)

    upid_counts = Counter(str(p.get("upid")) for p in players)
    dupe_upids = {u: c for u, c in upid_counts.items() if c > 1}
    missing_upid = [p for p in players if not p.get("upid")]
    missing_name = [p for p in players if not p.get("name")]

    print(f"Duplicate upid values (same upid on 2+ records): {len(dupe_upids)}")
    for u, c in list(dupe_upids.items())[:20]:
        print(f"  upid {u}: appears {c} times")
    print(f"Records missing upid entirely: {len(missing_upid)}")
    print(f"Records missing name: {len(missing_name)}")

    # ---------------------------------------------------------------
    # B. Ownership consistency
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("B. OWNERSHIP CONSISTENCY (FBP_Team / manager)")
    print("=" * 78)

    manager_but_no_fbpteam_key = [p for p in players if p.get("manager") and "FBP_Team" not in p]
    fbpteam_but_no_manager = [
        p for p in players
        if p.get("FBP_Team") and not p.get("manager")
    ]

    print(f"manager set but FBP_Team key missing entirely (the UPID 8696 bug pattern): {len(manager_but_no_fbpteam_key)}")
    for p in manager_but_no_fbpteam_key[:30]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} manager={p.get('manager')!r}")

    print(f"\nFBP_Team set but manager missing/blank: {len(fbpteam_but_no_manager)}")
    for p in fbpteam_but_no_manager[:30]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} FBP_Team={p.get('FBP_Team')!r} manager={p.get('manager')!r}")

    # Build empirical canonical FBP_Team -> manager map (majority vote)
    team_manager_votes = defaultdict(Counter)
    for p in players:
        ft = p.get("FBP_Team")
        mgr = p.get("manager")
        if ft and mgr:
            team_manager_votes[ft][mgr] += 1

    canonical_manager = {}
    print("\nEmpirical FBP_Team -> manager mapping (by majority):")
    for team in sorted(team_manager_votes):
        mgr, cnt = team_manager_votes[team].most_common(1)[0]
        canonical_manager[team] = mgr
        total = sum(team_manager_votes[team].values())
        print(f"  {team:5} -> {mgr!r}  ({cnt}/{total} records agree)")
        if len(team_manager_votes[team]) > 1:
            for alt_mgr, alt_cnt in team_manager_votes[team].most_common()[1:]:
                print(f"          also seen: {alt_mgr!r} x{alt_cnt}  <-- MISMATCH")

    mismatched_pairs = []
    for p in players:
        ft = p.get("FBP_Team")
        mgr = p.get("manager")
        if ft in canonical_manager and mgr and mgr != canonical_manager[ft]:
            mismatched_pairs.append(p)
    print(f"\nRecords where FBP_Team/manager pairing disagrees with the majority mapping: {len(mismatched_pairs)}")
    for p in mismatched_pairs[:30]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} FBP_Team={p.get('FBP_Team')!r} manager={p.get('manager')!r} (expected manager={canonical_manager.get(p.get('FBP_Team'))!r})")

    unknown_teams = [p for p in players if p.get("FBP_Team") and p.get("FBP_Team") not in KNOWN_TEAMS]
    print(f"\nFBP_Team values outside the known 12-team set {sorted(KNOWN_TEAMS)}: {len(unknown_teams)}")
    for p in unknown_teams[:30]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} FBP_Team={p.get('FBP_Team')!r}")

    # ---------------------------------------------------------------
    # C. Identity/ID collisions
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("C. IDENTITY COLLISIONS (same external ID on multiple UPIDs)")
    print("=" * 78)

    for id_field in ["yahoo_id", "mlb_id", "bbref_id", "fangraphs_id"]:
        by_id = defaultdict(list)
        for p in players:
            v = p.get(id_field)
            if v in (None, "", 0, "0"):
                continue
            by_id[str(v)].append(p)
        collisions = {k: v for k, v in by_id.items() if len(v) > 1}
        print(f"\n{id_field}: {len(collisions)} value(s) shared by 2+ UPIDs")
        for val, plist in list(collisions.items())[:15]:
            names = [f"upid={p.get('upid')}({p.get('name')}, {p.get('team')}, FBP_Team={p.get('FBP_Team','<missing>')!r})" for p in plist]
            print(f"  {id_field}={val!r}: {', '.join(names)}")

    # ---------------------------------------------------------------
    # D. Duplicate-person detection (name+team+position heuristic)
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("D. POSSIBLE DUPLICATE PERSON RECORDS (same name+team+position, different upid)")
    print("=" * 78)

    by_ntp = defaultdict(list)
    for p in players:
        key = (norm_name(p.get("name")), p.get("team"), p.get("position"))
        if not key[0]:
            continue
        by_ntp[key].append(p)
    ntp_dupes = {k: v for k, v in by_ntp.items() if len(v) > 1}
    print(f"Groups with 2+ UPIDs sharing name+team+position: {len(ntp_dupes)}")
    for key, plist in list(ntp_dupes.items())[:40]:
        names = [f"upid={p.get('upid')} FBP_Team={p.get('FBP_Team','<missing>')!r} manager={p.get('manager')!r} yahoo_id={p.get('yahoo_id')!r}" for p in plist]
        print(f"  {key[0]} ({key[1]}, {key[2]}):")
        for n in names:
            print(f"      {n}")

    # Looser: same normalized name only (different team/position -- could be
    # legitimately different people, but worth a lightweight scan for review)
    by_name_only = defaultdict(list)
    for p in players:
        key = norm_name(p.get("name"))
        if not key:
            continue
        by_name_only[key].append(p)
    name_only_dupes = {k: v for k, v in by_name_only.items() if len(v) > 1}
    print(f"\n(For reference only, not necessarily bugs) Same normalized name, any team/position: {len(name_only_dupes)} groups")

    # ---------------------------------------------------------------
    # E. Contract / status consistency
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("E. CONTRACT / STATUS CONSISTENCY")
    print("=" * 78)

    contract_no_years = [p for p in players if p.get("contract_type") and not p.get("years_simple")]
    years_no_contract = [p for p in players if p.get("years_simple") and not p.get("contract_type")]
    print(f"contract_type set but years_simple blank: {len(contract_no_years)}")
    for p in contract_no_years[:20]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} contract_type={p.get('contract_type')!r} FBP_Team={p.get('FBP_Team')!r}")
    print(f"\nyears_simple set but contract_type blank: {len(years_no_contract)}")
    for p in years_no_contract[:20]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} years_simple={p.get('years_simple')!r} FBP_Team={p.get('FBP_Team')!r}")

    # Empirical status convention per (contract_type, years_simple)
    combo_status_votes = defaultdict(Counter)
    for p in players:
        ct, ys, st = p.get("contract_type"), p.get("years_simple"), p.get("status")
        if ct and ys:
            combo_status_votes[(ct, ys)][st] += 1

    print(f"\nStatus-code convention by (contract_type, years_simple) -- majority vs outliers:")
    outlier_records = []
    for combo, votes in sorted(combo_status_votes.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(votes.values())
        if total < 5:
            continue
        top_status, top_cnt = votes.most_common(1)[0]
        if len(votes) > 1:
            print(f"  {combo}: majority status={top_status!r} ({top_cnt}/{total}); other values: {dict(votes.most_common()[1:])}")
            for p in players:
                if (p.get("contract_type"), p.get("years_simple")) == combo and p.get("status") != top_status:
                    outlier_records.append((p, top_status))
    print(f"\nRecords whose status disagrees with the majority for their contract/years combo: {len(outlier_records)}")
    for p, expected in outlier_records[:25]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} status={p.get('status')!r} (majority for this combo: {expected!r})")

    # ---------------------------------------------------------------
    # F. Graduation gap: debuted MLB players still marked Farm
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("F. GRADUATION GAP (debuted but still player_type=Farm)")
    print("=" * 78)

    debuted_still_farm = []
    for p in players:
        if p.get("player_type") != "Farm":
            continue
        debuted_flag = p.get("debuted")
        debut_date = p.get("debut_date")
        if debuted_flag is True or (debut_date not in (None, "")):
            debuted_still_farm.append(p)
    print(f"player_type=Farm but debuted=True or has a debut_date: {len(debuted_still_farm)}")
    for p in debuted_still_farm[:30]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} debut_date={p.get('debut_date')!r} debuted={p.get('debuted')!r} FBP_Team={p.get('FBP_Team')!r}")

    # ---------------------------------------------------------------
    # G. upid_database.json cross-check
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("G. upid_database.json TEAM/POS DISAGREEMENT WITH combined_players.json")
    print("=" * 78)

    team_mismatch = []
    for p in players:
        u = str(p.get("upid"))
        meta = by_upid_meta.get(u)
        if not meta:
            continue
        if meta.get("team") and p.get("team") and meta.get("team") != p.get("team"):
            team_mismatch.append((p, meta))
    print(f"UPIDs where upid_database.json's team differs from combined_players.json's team: {len(team_mismatch)}")
    for p, meta in team_mismatch[:30]:
        print(f"  upid={p.get('upid'):>6}  {p.get('name','?'):25} combined.team={p.get('team')!r}  upid_db.team={meta.get('team')!r}  upid_db.name={meta.get('name')!r}")

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"Total players:                                          {len(players)}")
    print(f"Duplicate upid values:                                  {len(dupe_upids)}")
    print(f"manager set, FBP_Team key missing:                      {len(manager_but_no_fbpteam_key)}")
    print(f"FBP_Team set, manager missing:                          {len(fbpteam_but_no_manager)}")
    print(f"FBP_Team/manager pairing mismatches:                    {len(mismatched_pairs)}")
    print(f"FBP_Team outside known team set:                        {len(unknown_teams)}")
    print(f"contract_type set, years_simple blank:                  {len(contract_no_years)}")
    print(f"years_simple set, contract_type blank:                  {len(years_no_contract)}")
    print(f"status disagrees w/ contract/years majority:            {len(outlier_records)}")
    print(f"Possible duplicate-person groups (name+team+position):  {len(ntp_dupes)}")
    print(f"Debuted-but-still-Farm (graduation gap):                {len(debuted_still_farm)}")
    print(f"upid_database.json team mismatch:                       {len(team_mismatch)}")


if __name__ == "__main__":
    main()
