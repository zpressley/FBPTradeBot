import json
import os
from datetime import datetime

EVENT_FILE = "data/roster_events.json"

def count_service_days():
    if not os.path.exists(EVENT_FILE):
        print("❌ roster_events.json not found.")
        return

    with open(EVENT_FILE, "r") as f:
        events = json.load(f)

    today = datetime.today()
    summary = {}

    for name, logs in events.items():
        logs.sort(key=lambda e: e["date"])
        total_days = 0
        on_roster = False
        callup_date = None

        for event in logs:
            date = datetime.strptime(event["date"], "%Y-%m-%d")
            if event["event"] == "called_up":
                callup_date = date
                on_roster = True
            elif event["event"] == "sent_down" and callup_date:
                days = (date - callup_date).days
                total_days += days
                on_roster = False
                callup_date = None

        # If still on roster today
        if on_roster and callup_date:
            total_days += (today - callup_date).days

        summary[name] = {
            "service_days": total_days
        }

    print("📊 MLB Service Days:")
    for name, data in summary.items():
        print(f"- {name}: {data['service_days']} days")

    return summary

if __name__ == "__main__":
    count_service_days()
