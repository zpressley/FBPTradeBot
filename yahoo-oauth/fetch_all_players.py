import requests
import xmltodict
import csv
import json
from token_manager import get_access_token

# Get latest access token
ACCESS_TOKEN = get_access_token()

if not ACCESS_TOKEN:
    print("❌ No valid access token. Run get_token.py first.")
    exit()

# ✅ Replace this with the correct MLB game key from `fetch_game_key.py`
GAME_KEY = "431"  # Use the correct 2024 MLB game key found in fetch_game_key.py

# Available Positions to Loop Through (Yahoo Limits 2000 Players Per Request)
POSITIONS = ["C", "1B", "2B", "3B", "SS", "OF", "DH", "SP", "RP"]  # Fetch all player positions

# Yahoo Fantasy API Endpoint for Players with Stats (Explicitly fetching 2024 season)
BASE_URL = f"https://fantasysports.yahooapis.com/fantasy/v2/game/{GAME_KEY}/players;position={{position}};start={{start}}/stats"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"
}

# ✅ Map Yahoo Stat IDs to Real Stat Names
STAT_MAP = {
    "0": "Games Played",
    "1": "Games Played",
    "2": "Games Started",
    "3": "Batting Average",
    "4": "On-base Percentage",
    "5": "Slugging Percentage",
    "6": "At Bats",
    "7": "Runs",
    "8": "Hits",
    "9": "Singles",
    "10": "Doubles",
    "11": "Triples",
    "12": "Home Runs",
    "13": "Runs Batted In",
    "14": "Sacrifice Hits",
    "15": "Sacrifice Flys",
    "16": "Stolen Bases",
    "17": "Caught Stealing",
    "18": "Walks",
    "19": "Intentional Walks",
    "20": "Hit By Pitch",
    "21": "Strikeouts",
    "22": "Ground Into Double Play",
    "23": "Total Bases",
    "24": "Pitching Appearances",
    "25": "Games Started (Pitching)",
    "26": "Earned Run Average",
    "27": "(Walks + Hits)/ Innings Pitched",
    "28": "Wins",
    "29": "Losses",
    "30": "Complete Games",
    "31": "Shutouts",
    "32": "Saves",
    "33": "Outs",
    "34": "Hits Allowed",
    "35": "Total Batters Faced",
    "36": "Runs Allowed",
    "37": "Earned Runs",
    "38": "Home Runs Allowed",
    "39": "Walks Issued",
    "40": "Intentional Walks Issued",
    "41": "Hit Batters",
    "42": "Strikeouts (Pitching)",
    "43": "Wild Pitches",
    "44": "Balks",
    "45": "Stolen Bases Allowed",
    "46": "Batters Grounded Into Double Plays",
    "47": "Save Chances",
    "48": "Holds",
    "49": "Total Bases Allowed",
    "50": "Innings Pitched",
    "51": "Putouts",
    "52": "Assists",
    "53": "Errors",
    "54": "Fielding Percentage",
    "55": "On-base + Slugging Percentage",
    "56": "Strikeouts per Walk Ratio",
    "57": "Strikeouts per Nine Innings",
    "58": "Team",
    "59": "League",
    "60": "Hits / At Bats",
    "61": "Extra Base Hits",
    "62": "Net Stolen Bases",
    "63": "Stolen Base Percentage",
    "64": "Hitting for the Cycle",
    "65": "Plate Appearances",
    "66": "Grand Slam Home Runs",
    "67": "Pitch Count",
    "68": "Doubles Allowed",
    "69": "Triples Allowed",
    "70": "Relief Wins",
    "71": "Relief Losses",
    "72": "Pickoffs",
    "73": "Relief Appearances",
    "74": "On-base Percentage Against",
    "75": "Winning Percentage",
    "76": "Singles Allowed",
    "77": "Hits Per Nine Innings",
    "78": "Walks Per Nine Innings",
    "79": "No Hitters",
    "80": "Perfect Games",
    "81": "Save Percentage",
    "82": "Inherited Runners Scored",
    "83": "Quality Starts",
    "84": "Blown Saves",
    "85": "Net Saves",
    "86": "Outfield Assists",
    "87": "Double Plays Turned",
    "88": "Catcher Interference",
    "89": "Saves + Holds",
    "90": "Net Saves and Holds",
    "91": "Net Wins"
}


# ✅ CSV Output File
csv_filename = "all_players_with_stats.csv"

# ✅ Create CSV File & Write Header
with open(csv_filename, "w", newline="") as csv_file:
    fieldnames = ["player_id", "name", "team", "position"] + list(STAT_MAP.values())
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()

    # ✅ Fetch Players for Each Position to Avoid 2000-Player Limit
    for position in POSITIONS:
        print(f"\n🔍 Fetching Players for Position: {position}")

        players_list = []
        start = 0  # Start index for pagination
        players_per_page = 25  # Max players Yahoo returns per request

        while True:
            url = BASE_URL.format(position=position, start=start)
            print(f"Fetching players {start} - {start + players_per_page - 1} for position {position}...")

            # ✅ Make API Request
            response = requests.get(url, headers=HEADERS)

            # ✅ Print Raw API Response (First Few Characters)
            raw_response = response.text
            print("\n🔍 Raw API Response (First 500 chars):")
            print(raw_response[:500])  # Log small sample of response

            # ✅ Convert XML to JSON
            try:
                parsed_data = xmltodict.parse(response.text)

                # ✅ Extract players
                players_data = parsed_data["fantasy_content"]["game"]["players"].get("player", [])

                if not players_data:
                    print(f"✅ No more players found for {position}. Moving to next position...")
                    break  # Exit loop if no more players

                # ✅ If only one player is returned, Yahoo returns a dict instead of a list
                if isinstance(players_data, dict):
                    players_data = [players_data]

                # ✅ Extract Player Info & Stats
                for player in players_data:
                    player_info = {
                        "player_id": player.get("player_id", "N/A"),
                        "name": player.get("name", {}).get("full", "Unknown"),
                        "team": player.get("editorial_team_full_name", "Free Agent"),
                        "position": position,
                    }

                    # ✅ Extract Player Stats
                    stats = player.get("player_stats", {}).get("stats", {}).get("stat", [])

                    # ✅ Check if stats exist
                    if not stats:
                        print(f"⚠️ No stats found for {player_info['name']}. Possible missing data.")

                    for stat in stats:
                        stat_id = stat.get("stat_id")
                        stat_value = stat.get("value", "N/A")

                        # ✅ Debugging: Print Stat ID and Value
                        print(f"🔍 Stat ID: {stat_id}, Value: {stat_value}")

                        # ✅ Ensure we map correctly
                        if stat_id in STAT_MAP:
                            player_info[STAT_MAP[stat_id]] = stat_value
                        else:
                            print(f"⚠️ Unknown stat_id: {stat_id}, Value: {stat_value}")


                    players_list.append(player_info)

                # ✅ Move to the next page
                start += players_per_page

            except Exception as e:
                print(f"❌ Error fetching players: {str(e)}")
                break  # Stop if there's an error

        # ✅ Append Data to CSV
        with open(csv_filename, "a", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            for player in players_list:
                writer.writerow(player)

print(f"\n✅ Successfully saved all player stats to {csv_filename}!")

