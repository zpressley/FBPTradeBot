"""
scrape_pipeline_top100.py

Scrapes MLB Pipeline's Top 100 Prospects tables at:
    https://www.mlb.com/prospects/stats/top-prospects

The page is client-rendered — there's no public JSON API (confirmed by inspecting
live network traffic: every request is an image, ad pixel, or analytics ping, and
there's no __NEXT_DATA__ / __INITIAL_STATE__ style embedded blob either). It holds
TWO separate <table> elements sharing one 1-100 rank sequence: one for batters, one
for pitchers. This script reads both, tags each row with its type, and merges
everything back into a single ranked list.

Each row's MLB player ID is pulled directly from the headshot <img> src
(pattern: /people/{mlb_id}/headshot/...) rather than via a separate name-matching
step, so results should slot straight into mlb_id_cache.json / combined_players.json
without a fuzzy-match pass.

KNOWN LIMITATIONS (verified against a live run on 2026-08-06):
  - This is a STATS table, so a Top 100 prospect with zero games played this
    season (injured all year, not yet stateside, etc.) has no row here even
    though they're still ranked. Last live check: 97 of 100 slots populated.
    If you need full 100/100 coverage, the separate rankings page at
    https://www.mlb.com/prospects is worth checking too — not yet scraped or
    verified by this script.
  - Do NOT swap the URL for the "Top Prospect Stats" nav-menu variant
    (?type=all&minPA=1) — that's a different, much larger leaderboard (870+
    rows covering every qualified minor leaguer), not the curated Top 100.
  - Team abbreviation isn't reliably scrapable as text — that column renders
    as a logo image with no alt text, so it's left out of the output.
    Cross-reference mlb_id against combined_players.json for current org.
  - Column-to-stat mapping (everything past rank/name/age/level) is built by
    reading the table's own <thead> at runtime and zipping it positionally
    with each row's <td> cells — nothing is hardcoded, so it should self-correct
    if MLB adds, removes, or reorders stat columns. Spot-check one known row
    on your first real run (current #1 should show AVG .290 as of this
    writing) before trusting it unattended.
  - I verified all of the above through a live browser session, but I can't
    actually execute Playwright myself (no network access in my sandbox), so
    this hasn't been run end-to-end yet. Test it once — ideally with
    headless=False so you can watch it — before wiring it into automation.

SETUP:
    pip install playwright --break-system-packages
    playwright install --with-deps chromium

RUN STANDALONE:
    python3 scrape_pipeline_top100.py

This only collects the raw snapshot. Turning repeated snapshots into
entered_top_100 / exited_top_100 events (the roster_events.json pattern) is a
separate follow-up step, not part of this script.
"""

import json
import os
import re
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

URL = "https://www.mlb.com/prospects/stats/top-prospects"
OUTPUT_DIR = "data/pipeline_snapshots"
HEADSHOT_ID_PATTERN = re.compile(r"/people/(\d+)/headshot")


def scrape_top_100(headless: bool = True) -> list[dict]:
    players = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("table tbody tr", timeout=15000)

            tables = page.query_selector_all("table")
            print(f"🔍 Found {len(tables)} tables on the page")

            for table in tables:
                headers = [h.inner_text().strip() for h in table.query_selector_all("thead th")]
                player_type = "pitcher" if "IP" in headers else "batter"

                for row in table.query_selector_all("tbody tr"):
                    cells = row.query_selector_all("td")
                    values = [c.inner_text().strip() for c in cells]
                    if not any(values):
                        continue  # spacer row between entries

                    mlb_id = None
                    img = row.query_selector("img[src*='headshot']")
                    if img:
                        match = HEADSHOT_ID_PATTERN.search(img.get_attribute("src") or "")
                        if match:
                            mlb_id = int(match.group(1))

                    stats = {
                        headers[i]: values[i]
                        for i in range(min(len(headers), len(values)))
                        if headers[i]
                    }
                    rk = stats.get("Rk", "")

                    players.append({
                        "rank": int(rk) if rk.isdigit() else None,
                        "name": stats.get("Player"),
                        "age": stats.get("Age"),
                        "level": stats.get("L"),
                        "player_type": player_type,
                        "mlb_id": mlb_id,
                        "stats": stats,
                        "raw_cells": values,  # positional fallback for manual spot-checks
                    })
        finally:
            browser.close()

    players.sort(key=lambda pl: pl["rank"] if pl["rank"] is not None else 999)
    batters = sum(1 for pl in players if pl["player_type"] == "batter")
    pitchers = sum(1 for pl in players if pl["player_type"] == "pitcher")
    print(f"✅ Parsed {len(players)} ranked players ({batters} batters, {pitchers} pitchers)")
    return players


def save_snapshot(players: list[dict]) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.today().strftime("%Y-%m-%d")
    path = os.path.join(OUTPUT_DIR, f"{today}.json")
    with open(path, "w") as f:
        json.dump({"date": today, "count": len(players), "players": players}, f, indent=2)
    print(f"✅ Snapshot saved to {path}")
    return path


if __name__ == "__main__":
    players = scrape_top_100()

    missing_ranks = sorted(set(range(1, 101)) - {pl["rank"] for pl in players if pl["rank"]})
    if missing_ranks:
        print(f"ℹ️  Ranks with no row this run (likely no games played yet): {missing_ranks}")

    print("\nTop 5 by rank:")
    for pl in players[:5]:
        print(f"  {pl['rank']:>3}. {pl['name']} ({pl['player_type']}, age {pl['age']}, {pl['level']})")

    save_snapshot(players)

    # Exit non-zero on a too-small result instead of silently committing a
    # bad/incomplete snapshot -- this is a monthly, unattended job, so a
    # silent partial failure (e.g. MLB.com changing table selectors) could
    # go unnoticed for months. Added 2026-08-08 after the Yahoo pipeline
    # incident made the cost of "succeeds but the data is garbage" concrete.
    if len(players) < 90:
        print(f"❌ Only found {len(players)} players — expected close to 100. "
              f"Site structure may have changed; check selectors before trusting this run.")
        sys.exit(1)
