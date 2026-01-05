import json
import csv

# Load the JSON data
with open("players.json", "r") as json_file:
    players_data = json.load(json_file)

# Define CSV file name
csv_filename = "players.csv"

# Open CSV file for writing
with open(csv_filename, "w", newline="") as csv_file:
    fieldnames = ["Player Name", "Team", "Position"]
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)

    # Write header
    writer.writeheader()

    # Write player data
    for player in players_data:
        writer.writerow({
            "Player Name": player["name"],
            "Team": player["team"],
            "Position": ", ".join(player["position"]) if isinstance(player["position"], list) else player["position"]
        })

print(f"\n✅ Successfully saved data to {csv_filename}!")
