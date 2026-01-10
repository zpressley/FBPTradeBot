import json
import math
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
COMBINED_PATH = ROOT / "data" / "combined_players.json"
OUTPUT_PATH = ROOT / "data" / "mlb_bio_dump.json"

BATCH_SIZE = 100
BASE_URL = "https://statsapi.mlb.com/api/v1/people"


def load_mlb_ids():
    players = json.loads(COMBINED_PATH.read_text())
    ids = {p.get("mlb_id") for p in players if p.get("mlb_id")}
    return sorted(
        int(i) for i in ids
        if isinstance(i, int) or (isinstance(i, str) and i.isdigit())
    )


def fetch_batch(ids):
    ids_str = ",".join(str(i) for i in ids)
    url = f"{BASE_URL}?personIds={ids_str}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json().get("people", [])


def main():
    mlb_ids = load_mlb_ids()
    print(f"Found {len(mlb_ids)} mlb_ids in combined_players.json")
    bio_map = {}

    num_batches = math.ceil(len(mlb_ids) / BATCH_SIZE) or 1
    for i in range(num_batches):
        batch_ids = mlb_ids[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]
        if not batch_ids:
            continue
        print(f"Fetching batch {i+1}/{num_batches} ({len(batch_ids)} ids)")
        people = fetch_batch(batch_ids)
        for person in people:
            pid = person["id"]
            bio_map[str(pid)] = {
                "mlb_id": pid,
                "fullName": person.get("fullName"),
                "birthDate": person.get("birthDate"),
                "currentAge": person.get("currentAge"),
                "birthCity": person.get("birthCity"),
                "birthStateProvince": person.get("birthStateProvince"),
                "birthCountry": person.get("birthCountry"),
                "height": person.get("height"),
                "weight": person.get("weight"),
                "primaryPosition": person.get("primaryPosition", {}),
                "batSide": person.get("batSide", {}),
                "pitchHand": person.get("pitchHand", {}),
                "draftYear": person.get("draftYear"),
            }
        time.sleep(0.25)  # be polite to the API

    OUTPUT_PATH.write_text(json.dumps(bio_map, indent=2, sort_keys=True))
    print(f"Wrote {len(bio_map)} bios to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()