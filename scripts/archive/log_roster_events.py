import os
import json
from datetime import datetime, timedelta

SNAPSHOT_DIR = "data/roster_snapshots"
EVENT_FILE = "data/roster_events.json"
DEFAULT_CALLUP_DATE = "2025-03-27"

def load_snapshot(date_str):
    path = os.path.join(SNAPSHOT_DIR, f"{date_str}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f).get("players", {})

def get_previous_date(date_str):
    return (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

def log_events(today_str):
    today_players = load_snapshot(today_str)
    prev_players = load_snapshot(get_previous_date(today_str))

    # Load existing event log
    if os.path.exists(EVENT_FILE):
        with open(EVENT_FILE, "r") as f:
            events = json.load(f)
    else:
        events = {}

    for name, data in today_players.items():
        today_status = data["on_roster"]
        mlb_id = data["mlb_id"]
        prev_status = prev_players.get(name, {}).get("on_roster")

        if name not in events:
            events[name] = []

        # New player, no prior data but already on roster → assume 3/27 call-up
        if prev_status is None and today_status:
            events[name].append({"date": DEFAULT_CALLUP_DATE, "event": "called_up"})
        elif prev_status is True and today_status is False:
            events[name].append({"date": today_str, "event": "sent_down"})
        elif prev_status is False and today_status is True:
            events[name].append({"date": today_str, "event": "called_up"})

    # Save updated events
    os.makedirs(os.path.dirname(EVENT_FILE), exist_ok=True)
    with open(EVENT_FILE, "w") as f:
        json.dump(events, f, indent=2)
    print(f"✅ Events logged to {EVENT_FILE}")

if __name__ == "__main__":
    today = datetime.today().strftime("%Y-%m-%d")
    log_events(today)
