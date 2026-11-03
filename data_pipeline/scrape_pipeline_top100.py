"""
scrape_pipeline_top100.py

Builds the MLB Pipeline Top 100 snapshot (Spec 03a).

Primary source:   https://www.mlb.com/prospects              (all 100 ranked players)
Secondary source: https://www.mlb.com/prospects/stats/top-prospects  (stats columns only;
                  skips ranked players with no games, so it is never used for the list itself)

The HTML parsing is done by pure functions (parse_rankings_html, parse_stats_html)
so it can be unit-tested without a browser. The rankings parser tries, in order:
  1. embedded JSON blobs in <script> tags (__NEXT_DATA__, window.__X__ = {...}, ld+json)
  2. anchors whose href looks like /player/<slug>-<digits> or /player/<digits>
  3. headshot images  /people/<digits>/headshot
and logs which strategy produced the rows.

Each row is matched to a UPID in data/combined_players.json by MLB ID first, then by
normalized name + org. The snapshot is written to data/pipeline_snapshots/<date>.json
and, when DATABASE_URL is set and psycopg is installed, to top100_snapshots.

Freeze rule: an existing snapshot marked official (JSON or DB) is never overwritten
unless --override-reason is given.

SETUP:  pip install playwright && playwright install --with-deps chromium
RUN:    python3 data_pipeline/scrape_pipeline_top100.py [--date YYYY-MM-DD] [--official]
            [--override-reason "..."] [--no-db] [--headful]

Exit codes: 0 ok, 1 failure (too few rows/IDs, freeze refusal, fetch failure).
"""

import argparse
import html as htmllib
import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.db.common import normalize_name  # noqa: E402

RANKINGS_URL = "https://www.mlb.com/prospects"
STATS_URL = "https://www.mlb.com/prospects/stats/top-prospects"
SOURCE = "mlb.com/prospects"
OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "pipeline_snapshots")
DEBUG_DIR = os.path.join(REPO_ROOT, "debug_html")
COMBINED_PATH = os.path.join(REPO_ROOT, "data", "combined_players.json")
BACKOFF_SECONDS = (5, 15, 45)  # one wait per attempt; no retries beyond 3 attempts
MIN_ROWS = 100
MIN_IDS = 95

HEADSHOT_ID = re.compile(r"/people/(\d+)/headshot")
PLAYER_HREF = re.compile(r"""href=["'][^"']*/player/(?:[^"'/?#]*-)?(\d+)(?:[/?#][^"']*)?["']""", re.I)
ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
IMG = re.compile(r"<img\b[^>]*>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)

# --------------------------------------------------------------------- org names

TEAM_ABBR = {
    "Arizona Diamondbacks": "AZ", "Athletics": "ATH", "Oakland Athletics": "ATH",
    "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE", "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC", "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM", "New York Yankees": "NYY",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}
ABBR_ALIASES = {"ARI": "AZ", "OAK": "ATH", "WSN": "WSH", "TBR": "TB", "KCR": "KC",
                "SDP": "SD", "SFG": "SF", "CHW": "CWS", "LA": "LAD"}
_NAME_TO_ABBR = {normalize_name(k): v for k, v in TEAM_ABBR.items()}


def canon_org(org) -> str:
    """Team name or abbreviation -> the abbreviation used in combined_players.json."""
    s = str(org or "").strip()
    if not s:
        return ""
    if normalize_name(s) in _NAME_TO_ABBR:
        return _NAME_TO_ABBR[normalize_name(s)]
    s = s.upper()
    return ABBR_ALIASES.get(s, s)


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def clean_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(TAG.sub(" ", fragment))).strip()


# --------------------------------------------------------- strategy 1: JSON blobs

ID_KEYS = ("mlbId", "mlb_id", "playerId", "player_id", "personId", "id")
RANK_KEYS = ("rank", "prospectRank", "currentRank", "ranking", "overallRank")
NAME_KEYS = ("fullName", "name", "playerName", "displayName", "player_name")


def _scalar(d: dict, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            return v
    return None


def _label(v):
    """Position/team/level values are sometimes {"abbreviation": "SS"} style dicts."""
    if isinstance(v, dict):
        return _scalar(v, ("abbreviation", "abbrev", "code", "name", "teamName"))
    return v if v not in ("",) else None


def _json_row(d: dict):
    """Return a row dict if d looks like one ranked prospect, else None."""
    rank = to_int(_scalar(d, RANK_KEYS))
    if rank is None or not 1 <= rank <= 100:
        return None
    holder = d
    for sub in ("player", "person", "prospect"):
        if isinstance(d.get(sub), dict):
            holder = {**d, **d[sub]}
            break
    mlb_id = to_int(_scalar(holder, ID_KEYS))
    if mlb_id is None:
        return None
    return {
        "rank": rank,
        "name": _scalar(holder, NAME_KEYS),
        "mlb_id": mlb_id,
        "org": _label(holder.get("team") or holder.get("org") or holder.get("teamName")
                      or holder.get("organization")),
        "position": _label(holder.get("position") or holder.get("pos")),
        "level": _label(holder.get("level")),
        "eta": _scalar(holder, ("eta", "ETA")),
        "age": _scalar(holder, ("age", "currentAge")),
    }


def _walk(node, out):
    if isinstance(node, dict):
        row = _json_row(node)
        if row:
            out.append(row)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def _json_candidates(script_attrs: str, body: str):
    """Yield parsed JSON values found in one <script> tag."""
    body = body.strip()
    if not body:
        return
    if body[0] in "{[":
        try:
            yield json.loads(body)
            return
        except ValueError:
            pass
    # window.__PRELOADED_STATE__ = {...};  /  window.__INITIAL_STATE__ = {...}
    for m in re.finditer(r"=\s*(?=[{\[])", body):
        try:
            value, _ = json.JSONDecoder().raw_decode(body, m.end())
        except ValueError:
            continue
        yield value


def parse_json_blobs(html: str) -> list[dict]:
    best = []
    for m in SCRIPT.finditer(html):
        for blob in _json_candidates(m.group(1), m.group(2)):
            found = []
            _walk(blob, found)
            by_rank = {}
            for row in found:
                by_rank.setdefault(row["rank"], row)
            rows = [by_rank[r] for r in sorted(by_rank)]
            if len(rows) > len(best):
                best = rows
    return best


# ----------------------------------------- strategies 2 and 3: anchors / headshots

def _explicit_rank(window_html: str):
    """Last standalone 1-3 digit number in the text just before an element."""
    for token in reversed(clean_text(window_html).split(" ")):
        if re.fullmatch(r"#?\d{1,3}", token):
            n = int(token.lstrip("#"))
            if 1 <= n <= 100:
                return n
            return None
    return None


def _assign_ranks(items: list[dict], windows: list[str]) -> list[dict]:
    """Use explicit rank text when it gives unique 1-100 values for every item, else document order."""
    explicit = [_explicit_rank(w) for w in windows]
    if all(r is not None for r in explicit) and len(set(explicit)) == len(explicit):
        ranks = explicit
    else:
        ranks = list(range(1, len(items) + 1))
    rows = [{**it, "rank": r} for it, r in zip(items, ranks)]
    rows.sort(key=lambda r: r["rank"])
    return rows


def _collect(html: str, matches) -> list[dict]:
    """matches: (start, end, mlb_id, name, name_is_alt) in document order. One item per MLB ID.
    Visible link text beats image alt text as the name."""
    items, windows, seen, alt_only = [], [], {}, set()
    prev_end = 0
    for start, end, mlb_id, name, is_alt in matches:
        if mlb_id in seen:
            if name and (not seen[mlb_id]["name"] or (mlb_id in alt_only and not is_alt)):
                seen[mlb_id]["name"] = name
                alt_only.discard(mlb_id)
            prev_end = end
            continue
        if is_alt and name:
            alt_only.add(mlb_id)
        item = {"mlb_id": mlb_id, "name": name or None, "org": None, "position": None,
                "level": None, "eta": None, "age": None}
        seen[mlb_id] = item
        items.append(item)
        windows.append(html[max(prev_end, start - 400):start])
        prev_end = end
    return _assign_ranks(items, windows)


def parse_anchors(html: str) -> list[dict]:
    matches = []
    for m in ANCHOR.finditer(html):
        h = PLAYER_HREF.search(m.group(1))
        if not h:
            continue
        inner = m.group(2)
        alt = re.search(r'alt=["\']([^"\']*)["\']', inner)
        text = clean_text(inner)
        alt_text = htmllib.unescape(alt.group(1)).strip() if alt else ""
        matches.append((m.start(), m.end(), int(h.group(1)), text or alt_text, not text))
    return _collect(html, matches)


def parse_headshots(html: str) -> list[dict]:
    matches = []
    for m in IMG.finditer(html):
        h = HEADSHOT_ID.search(m.group(0))
        if not h:
            continue
        alt = re.search(r'alt=["\']([^"\']*)["\']', m.group(0))
        name = htmllib.unescape(alt.group(1)).strip() if alt else ""
        matches.append((m.start(), m.end(), int(h.group(1)), name, False))
    return _collect(html, matches)


def parse_rankings_html(html: str):
    """Return (rows, strategy). Picks the first strategy that finds >= MIN_IDS rows,
    else the one with the most rows (so the failure log shows the best attempt)."""
    best, best_name = [], "none"
    for name, fn in (("json", parse_json_blobs), ("anchors", parse_anchors),
                     ("headshots", parse_headshots)):
        rows = fn(html)
        print(f"  strategy {name}: {len(rows)} rows")
        if len(rows) >= MIN_IDS:
            return rows, name
        if len(rows) > len(best):
            best, best_name = rows, name
    return best, best_name


# ------------------------------------------------------------------- stats page

def parse_stats_html(html: str) -> list[dict]:
    """Rows from the stats page tables: {rank, name, mlb_id, age, level, stats}."""
    players = []
    for table in re.findall(r"<table\b.*?</table>", html, re.I | re.S):
        headers = [clean_text(h) for h in re.findall(r"<th\b[^>]*>(.*?)</th>", table, re.I | re.S)]
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, re.I | re.S):
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.I | re.S)
            values = [clean_text(c) for c in cells]
            if not any(values):
                continue
            h = HEADSHOT_ID.search(tr)
            stats = {headers[i]: values[i] for i in range(min(len(headers), len(values))) if headers[i]}
            players.append({
                "rank": to_int(stats.get("Rk")),
                "name": stats.get("Player"),
                "mlb_id": int(h.group(1)) if h else None,
                "age": stats.get("Age"),
                "level": stats.get("L"),
                "stats": stats,
            })
    return players


def merge_stats(rows: list[dict], stats_rows: list[dict]) -> int:
    """Attach stats to ranking rows by MLB ID, then normalized name. Returns count merged."""
    by_id = {s["mlb_id"]: s for s in stats_rows if s["mlb_id"]}
    by_name = {normalize_name(s["name"]): s for s in stats_rows if s["name"]}
    merged = 0
    for row in rows:
        s = by_id.get(row["mlb_id"]) or by_name.get(normalize_name(row["name"]))
        row["stats"] = s["stats"] if s else {}
        if s:
            merged += 1
            row["level"] = row["level"] or s["level"]
            row["age"] = row["age"] or s["age"]
    return merged


# ------------------------------------------------------------------ UPID matching

def load_players(path: str = COMBINED_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def match_rows(rows: list[dict], players: list[dict]) -> list[dict]:
    """Set upid + match_method on each row; return the unmatched rows."""
    owned = [p for p in players if str(p.get("upid") or "").strip()]
    by_id, by_name = {}, {}
    for p in owned:
        pid = to_int(p.get("mlb_id"))
        if pid:
            by_id.setdefault(pid, []).append(p)
        by_name.setdefault(normalize_name(p.get("name")), []).append(p)

    unmatched = []
    for row in rows:
        row["upid"], row["match_method"] = None, None
        cands = by_id.get(to_int(row["mlb_id"]), [])
        if len(cands) > 1:  # duplicate MLB IDs exist in combined_players.json
            same = [c for c in cands if normalize_name(c["name"]) == normalize_name(row["name"])]
            cands = same or cands
        if cands:
            row["upid"], row["match_method"] = str(cands[0]["upid"]), "mlb_id"
            row["org"] = row["org"] or cands[0].get("team") or None
        else:
            cands = by_name.get(normalize_name(row["name"]), [])
            if row["org"]:
                cands = [c for c in cands if canon_org(c.get("team")) == canon_org(row["org"])]
            if len(cands) == 1:  # no org on the row: only accept an unambiguous name
                row["upid"], row["match_method"] = str(cands[0]["upid"]), "name_org"
        if not row["upid"]:
            unmatched.append({k: row[k] for k in ("rank", "name", "mlb_id", "org")})
    return unmatched


# --------------------------------------------------------------------- browser

def fetch_page(url: str, headless: bool = True, wait_selector: str = "a[href*='/player/']"):
    """Load a page with Playwright; up to 3 attempts with 5s/15s/45s backoff.

    Returns (html, json_responses) where json_responses is a list of
    (url, parsed_value) for every JSON response the page fetched while
    rendering. The rankings page is client-rendered, so the list it shows
    usually arrives as one of those responses rather than in the HTML.
    """
    from playwright.sync_api import sync_playwright  # imported here so tests need no browser

    last_error = None
    for attempt, wait in enumerate(BACKOFF_SECONDS, start=1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    page = browser.new_page()
                    json_responses = []

                    def on_response(resp):
                        try:
                            ctype = resp.headers.get("content-type", "")
                            if "json" not in ctype and not resp.url.split("?")[0].endswith(".json"):
                                return
                            body = resp.text()
                            if len(body) < 2000:
                                return
                            json_responses.append((resp.url, json.loads(body)))
                        except Exception:  # noqa: BLE001 - a bad response is not our problem
                            pass

                    page.on("response", on_response)
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        pass
                    try:
                        page.wait_for_selector(wait_selector, timeout=15000)
                    except Exception:
                        pass  # the parser decides whether the page is usable
                    for _ in range(8):  # trigger lazy-loaded rows
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(500)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    return page.content(), json_responses
                finally:
                    browser.close()
        except Exception as e:  # noqa: BLE001
            last_error = e
            print(f"⚠️  Attempt {attempt}/{len(BACKOFF_SECONDS)} for {url} failed: {e}")
            if attempt < len(BACKOFF_SECONDS):
                print(f"   waiting {wait}s before retrying")
                time.sleep(wait)
    raise RuntimeError(f"Could not load {url} after {len(BACKOFF_SECONDS)} attempts: {last_error}")


def fetch_html(url: str, headless: bool = True, wait_selector: str = "a[href*='/player/']") -> str:
    return fetch_page(url, headless, wait_selector)[0]


def parse_json_responses(json_responses) -> list[dict]:
    """Strategy 0: the JSON the page fetched while rendering (same walk as the script blobs)."""
    best = []
    for url, value in json_responses:
        found = []
        _walk(value, found)
        by_rank = {}
        for row in found:
            by_rank.setdefault(row["rank"], row)
        rows = [by_rank[r] for r in sorted(by_rank)]
        if len(rows) > len(best):
            best = rows
            print(f"  network json {url[:120]}: {len(rows)} ranked rows")
    return best


def parse_rankings(html: str, json_responses):
    rows = parse_json_responses(json_responses)
    print(f"  strategy network: {len(rows)} rows")
    if len(rows) >= MIN_IDS:
        return rows, "network"
    html_rows, name = parse_rankings_html(html)
    return (html_rows, name) if len(html_rows) > len(rows) else (rows, "network")


def save_debug_html(html: str, date: str) -> str:
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"top100_rankings_{date}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ------------------------------------------------------------------- snapshot I/O

def check_thresholds(rows: list[dict]) -> list[str]:
    errors = []
    with_id = sum(1 for r in rows if r["mlb_id"])
    if len(rows) < MIN_ROWS:
        errors.append(f"only {len(rows)} rows (need {MIN_ROWS})")
    if with_id < MIN_IDS:
        errors.append(f"only {with_id} rows have an MLB ID (need {MIN_IDS})")
    return errors


def snapshot_path(date: str) -> str:
    return os.path.join(OUTPUT_DIR, f"{date}.json")


def check_freeze(path: str, override_reason) -> None:
    """Raise SystemExit if path holds an official snapshot and no override reason was given."""
    if not os.path.exists(path) or override_reason:
        return
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except ValueError:
        return
    if existing.get("official"):
        raise SystemExit(f"❌ {path} is an official (frozen) snapshot. "
                         f"Pass --override-reason to overwrite it.")


def build_snapshot(rows, unmatched, date, official, override_reason, scraped_at) -> dict:
    players = []
    for r in rows:
        players.append({
            "rank": r["rank"], "name": r["name"], "mlb_id": r["mlb_id"], "org": r["org"],
            "position": r["position"], "level": r["level"], "eta": r["eta"], "age": r["age"],
            "upid": r["upid"], "match_method": r["match_method"], "stats": r.get("stats", {}),
        })
    snap = {"date": date, "count": len(players), "official": official, "source": SOURCE,
            "scraped_at": scraped_at, "players": players, "unmatched": unmatched}
    if override_reason:
        snap["override"] = {"reason": override_reason, "at": scraped_at}
    return snap


def write_snapshot(snap: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)
    print(f"✅ Snapshot saved to {path}")


def write_db(snap: dict, override_reason) -> None:
    """Replace the date's rows in top100_snapshots in ONE transaction (optional step)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ℹ️  DATABASE_URL not set; skipping database write.")
        return
    try:
        from psycopg.types.json import Jsonb
        from scripts.db.common import connect
    except ImportError:
        print("ℹ️  psycopg not installed; skipping database write.")
        return

    with connect(url) as conn:  # commits on clean exit, rolls back on exception
        official_rows = conn.execute(
            "select count(*) from top100_snapshots where snapshot_date = %s and official",
            (snap["date"],)).fetchone()[0]
        if official_rows and not override_reason:
            conn.rollback()
            raise SystemExit(f"❌ {official_rows} official rows already exist in top100_snapshots "
                             f"for {snap['date']}. Pass --override-reason to replace them.")
        conn.execute("delete from top100_snapshots where snapshot_date = %s", (snap["date"],))
        for p in snap["players"]:
            raw = dict(p)
            if override_reason:
                raw["override_reason"] = override_reason
            conn.execute(
                "insert into top100_snapshots (snapshot_date, rank, upid, mlb_id, name, org, "
                "position, level, eta, raw, official, source) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (snap["date"], p["rank"], p["upid"], p["mlb_id"], p["name"], p["org"],
                 p["position"], p["level"], p["eta"], Jsonb(raw), snap["official"], snap["source"]))
    print(f"✅ Wrote {len(snap['players'])} rows to top100_snapshots for {snap['date']}")


# ------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scrape the MLB Pipeline Top 100 snapshot.")
    ap.add_argument("--date", help="Snapshot date YYYY-MM-DD (default: today, America/New_York)")
    ap.add_argument("--official", action="store_true", help="Mark the snapshot official (frozen)")
    ap.add_argument("--override-reason", help="Required to overwrite an official snapshot")
    ap.add_argument("--no-db", action="store_true", help="Skip the top100_snapshots write")
    ap.add_argument("--headful", action="store_true", help="Show the browser window")
    args = ap.parse_args(argv)

    now = datetime.now(ZoneInfo("America/New_York"))
    date = args.date or now.strftime("%Y-%m-%d")
    path = snapshot_path(date)
    check_freeze(path, args.override_reason)

    print(f"🔍 Fetching rankings page {RANKINGS_URL}")
    html, json_responses = fetch_page(RANKINGS_URL, headless=not args.headful)
    rows, strategy = parse_rankings(html, json_responses)
    print(f"Strategy used: {strategy} ({len(rows)} rows, {sum(1 for r in rows if r['mlb_id'])} with MLB IDs)")
    errors = check_thresholds(rows)
    if errors:
        saved = save_debug_html(html, date)
        print(f"❌ Rankings parse failed: {'; '.join(errors)}")
        print(f"Saved fetched HTML to {saved}")
        os.makedirs(DEBUG_DIR, exist_ok=True)
        for i, (url, value) in enumerate(json_responses):
            with open(os.path.join(DEBUG_DIR, f"response_{i:02d}.json"), "w", encoding="utf-8") as f:
                json.dump({"url": url, "body": value}, f)
            print(f"  network json response {i:02d}: {url[:160]}")
        return 1

    try:
        print(f"🔍 Fetching stats page {STATS_URL}")
        stats_rows = parse_stats_html(fetch_html(STATS_URL, headless=not args.headful, wait_selector="table tbody tr"))
    except Exception as e:  # noqa: BLE001 - stats are optional
        print(f"⚠️  Stats page unavailable, continuing without stats: {e}")
        stats_rows = []
    print(f"Stats merged for {merge_stats(rows, stats_rows)} of {len(rows)} rows")

    unmatched = match_rows(rows, load_players())
    methods = {m: sum(1 for r in rows if r["match_method"] == m) for m in ("mlb_id", "name_org")}
    print(f"UPID matches: {methods}; unmatched: {len(unmatched)}")
    for u in unmatched:
        print(f"  unmatched #{u['rank']} {u['name']} (mlb_id={u['mlb_id']}, org={u['org']})")

    snap = build_snapshot(rows, unmatched, date, args.official, args.override_reason, now.isoformat())
    write_snapshot(snap, path)
    if args.no_db:
        print("ℹ️  --no-db given; skipping database write.")
    else:
        write_db(snap, args.override_reason)

    print("\nTop 5:")
    for p in snap["players"][:5]:
        print(f"  {p['rank']:>3}. {p['name']} ({p['org']}, {p['level']}) upid={p['upid']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
