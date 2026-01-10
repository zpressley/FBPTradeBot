import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMBINED_PATH = ROOT / "data" / "combined_players.json"
BIO_PATH = ROOT / "data" / "mlb_bio_dump.json"
OUTPUT_PATH = COMBINED_PATH  # overwrite combined_players.json in-place


def main():
    players = json.loads(COMBINED_PATH.read_text())
    bio_map = json.loads(BIO_PATH.read_text()) if BIO_PATH.exists() else {}

    enriched = []
    for p in players:
        mlb_id = p.get("mlb_id")
        bio = bio_map.get(str(mlb_id)) if mlb_id is not None else None
        if bio:
            p_new = deepcopy(p)
            p_new["birth_date"] = bio.get("birthDate")
            p_new["age"] = bio.get("currentAge")
            p_new["height"] = bio.get("height")
            p_new["weight"] = bio.get("weight")
            p_new["bats"] = (bio.get("batSide") or {}).get("code")
            p_new["throws"] = (bio.get("pitchHand") or {}).get("code")
            p_new["mlb_primary_position"] = (bio.get("primaryPosition") or {}).get(
                "abbreviation"
            )
            enriched.append(p_new)
        else:
            enriched.append(p)

    OUTPUT_PATH.write_text(json.dumps(enriched, indent=2))
    print(f"Wrote {len(enriched)} players to {OUTPUT_PATH} (combined_players.json)")


if __name__ == "__main__":
    main()