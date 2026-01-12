import requests
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
import importlib.util

# Load token_manager.py from project root explicitly (supports a few known locations)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
possible_tm_paths = [
    os.path.join(ROOT_DIR, "token_manager.py"),
    os.path.join(ROOT_DIR, "data_pipeline", "token_manager.py"),
    os.path.join(ROOT_DIR, "random", "token_manager.py"),
]

TOKEN_MANAGER_PATH = None
for _path in possible_tm_paths:
    if os.path.exists(_path):
        TOKEN_MANAGER_PATH = _path
        break

if TOKEN_MANAGER_PATH is None:
    raise FileNotFoundError(f"Could not locate token_manager.py in any known location: {possible_tm_paths}")

spec = importlib.util.spec_from_file_location("token_manager_local", TOKEN_MANAGER_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load token_manager.py from {TOKEN_MANAGER_PATH}")
_token_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_token_manager)
get_access_token = _token_manager.get_access_token

# Get latest access token
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# League / season configuration
# Default season is 2025. Override by passing a season year on the command line,
# e.g. `python fetch_players_with_stats.py 2024`.
MLB_GAME_IDS = {
    2025: 458,
    2024: 404,
}
DEFAULT_SEASON = 2025

season = DEFAULT_SEASON
if len(sys.argv) > 1:
    try:
        season_arg = int(sys.argv[1])
        if season_arg in MLB_GAME_IDS:
            season = season_arg
        else:
            print(f"⚠️ Season {season_arg} not configured; defaulting to {DEFAULT_SEASON}.")
    except ValueError:
        print(f"⚠️ Invalid season '{sys.argv[1]}'; defaulting to {DEFAULT_SEASON}.")

GAME_ID = MLB_GAME_IDS[season]
LEAGUE_NUM = "15505"
LEAGUE_KEY = f"{GAME_ID}.l.{LEAGUE_NUM}"

# Yahoo Fantasy API Endpoints
BASE_URL = (
    f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
    f"{LEAGUE_KEY}/players;start={{start}}/stats"
)
SETTINGS_URL = (
    f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
    f"{LEAGUE_KEY}/settings"
)

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# Fetch stat definitions so we can map stat_id -> human-readable label
print(f"Fetching stat categories for league {LEAGUE_KEY}...")
settings_resp = requests.get(SETTINGS_URL, headers=HEADERS)
stat_id_to_label: dict[str, str] = {}
ordered_labels: list[str] = []

try:
    if settings_resp.status_code != 200:
        raise RuntimeError(f"HTTP {settings_resp.status_code} from settings endpoint")

    ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
    root = ET.fromstring(settings_resp.text)

    # League settings → stat_categories → stats → stat
    stats_section = root.findall(".//y:stat_categories/y:stats/y:stat", ns)

    for stat_def in stats_section:
        stat_id_el = stat_def.find("y:stat_id", ns)
        if stat_id_el is None or not stat_id_el.text:
            continue
        stat_id = stat_id_el.text.strip()

        name_el = stat_def.find("y:name", ns)
        disp_el = stat_def.find("y:display_name", ns)
        name_val = name_el.text.strip() if name_el is not None and name_el.text else None
        disp_val = disp_el.text.strip() if disp_el is not None and disp_el.text else None

        # Prefer display_name, then name, then fallback
        label = disp_val or name_val or f"stat_{stat_id}"

        # Avoid duplicate labels; keep first occurrence per stat_id
        if stat_id not in stat_id_to_label:
            stat_id_to_label[stat_id] = label
            ordered_labels.append(label)

    print(f"Found {len(ordered_labels)} stat categories.")
except Exception as e:
    print(f"⚠️ Could not parse stat categories cleanly: {e}")
    # Fallback: will still work but columns will be stat_<id>

# Pagination: Yahoo returns only 25 players per request
players_list = []
start = 0  # Start index for pagination
players_per_page = 25  # Max players Yahoo returns per request

ns = {"y": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}

while True:
    url = BASE_URL.format(start=start)

    print(f"Fetching players {start} - {start + players_per_page - 1}...")

    # Make API Request
    response = requests.get(url, headers=HEADERS)

    try:
        if response.status_code != 200:
            print(f"❌ HTTP {response.status_code} while fetching players")
            break

        root = ET.fromstring(response.text)
        players_nodes = root.findall(".//y:player", ns)

        if not players_nodes:
            print("No more players found. Exiting loop.")
            break  # Exit loop if no more players

        # Extract Player Info & Stats
        for player in players_nodes:
            # Core identifiers
            pid_el = player.find("y:player_id", ns)
            player_id = pid_el.text if pid_el is not None else "N/A"

            name_el = player.find(".//y:full", ns)
            name = name_el.text if name_el is not None else "Unknown"

            team_el = player.find("y:editorial_team_full_name", ns)
            team = team_el.text if team_el is not None else "Free Agent"

            # Eligible positions
            pos_nodes = player.findall(".//y:eligible_positions/y:position", ns)
            positions = [p.text for p in pos_nodes if p is not None and p.text]
            position_str = ", ".join(positions) if positions else "N/A"

            player_info = {
                "player_id": player_id,
                "name": name,
                "team": team,
                "position": position_str,
            }

            # Extract Player Stats
            stats_parent = player.find("y:player_stats", ns)
            if stats_parent is not None:
                for stat in stats_parent.findall(".//y:stat", ns):
                    stat_id_el = stat.find("y:stat_id", ns)
                    value_el = stat.find("y:value", ns)
                    if stat_id_el is None or value_el is None:
                        continue
                    stat_id = stat_id_el.text
                    stat_value = value_el.text

                    if stat_id is None:
                        continue
                    stat_id = stat_id.strip()

                    # Map to human-readable label when possible
                    label = stat_id_to_label.get(stat_id)
                    if not label:
                        label = f"stat_{stat_id}"
                        if label not in ordered_labels:
                            ordered_labels.append(label)

                    player_info[label] = stat_value

            players_list.append(player_info)

        # Move to the next page
        start += players_per_page

    except Exception as e:
        print(f"Error fetching players: {str(e)}")
        break  # Stop if there's an error

# Save to CSV under data/
os.makedirs("data", exist_ok=True)
csv_filename = os.path.join("data", f"yahoo_players_{season}_stats.csv")

# Use discovered stat labels in the order returned by settings
stat_columns = ordered_labels if ordered_labels else sorted(
    {k for p in players_list for k in p.keys() if k.startswith("stat_")}
)

with open(csv_filename, "w", newline="") as csv_file:
    fieldnames = ["player_id", "name", "team", "position"] + stat_columns
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)

    writer.writeheader()
    for player in players_list:
        writer.writerow(player)

print(f"\n✅ Successfully saved all {season} player stats to {csv_filename}!")
