import csv
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "historical" / "Transactions"
OUTPUT_FILE = DATA_DIR / "transactions_history.json"

HISTORY_CSV_FILES = [
    HISTORY_DIR / "FBP History Book - Page 1.csv",
    HISTORY_DIR / "FBP History Book - Page 2.csv",
    HISTORY_DIR / "FBP History Book - Page 3.csv",
    HISTORY_DIR / "FBP History Book - Page 4.csv",
]


@dataclass
class TransactionRecord:
    id: str
    season: Optional[int]
    source: str
    admin: str
    timestamp: str
    upid: str
    player_name: str
    team: str
    pos: str
    age: Optional[int]
    level: str
    team_rank: Optional[int]
    rank: Optional[int]
    eta: str
    player_type: str
    owner: str
    contract: str
    status: str
    years: str
    update_type: str
    event: str


DATE_FORMATS = [
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]


def parse_int(value: str) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def infer_season(dt: Optional[datetime], fallback: str) -> Optional[int]:
    if dt is not None:
        return dt.year
    # crude fallback: look for 4-digit year in the raw string
    for token in fallback.split():
        if len(token) == 4 and token.isdigit():
            try:
                year = int(token)
            except ValueError:
                continue
            if 1990 <= year <= 2100:
                return year
    return None


def normalize_row(row: dict, source: str, row_index: int) -> TransactionRecord:
    admin = (row.get("Admin") or "").strip()
    dt_raw = (row.get("Date & Time") or "").strip()
    dt = parse_date(dt_raw)
    season = infer_season(dt, dt_raw)

    upid = (row.get("UPID") or "").strip()
    player_name = (row.get("Player Name") or "").strip()
    team = (row.get("Team") or "").strip()
    pos = (row.get("Pos") or "").strip()
    age = parse_int(row.get("Age") or "")
    level = (row.get("Level") or "").strip()
    team_rank = parse_int(row.get("Team Rank") or "")
    rank = parse_int(row.get("Rank") or "")
    eta = (row.get("ETA") or "").strip()
    player_type = (row.get("Player Type") or "").strip()
    owner = (row.get("Owner") or "").strip()
    contract = (row.get("Contract") or "").strip()
    status = (row.get("Status") or "").strip()
    years = (row.get("Years") or "").strip()
    update_type = (row.get("Update Type") or "").strip()
    event = (row.get("Event") or "").strip()

    # Prefer a stable, mostly unique ID. Include season, timestamp, upid/name, and update_type.
    base_parts: List[str] = []
    if season is not None:
        base_parts.append(str(season))
    if dt is not None:
        base_parts.append(dt.isoformat())
    elif dt_raw:
        base_parts.append(dt_raw)
    if upid:
        base_parts.append(f"UPID_{upid}")
    elif player_name:
        base_parts.append(player_name)
    if update_type:
        base_parts.append(update_type)
    base_parts.append(source)
    base_parts.append(str(row_index))

    rec_id = "-".join(p.replace(" ", "_") for p in base_parts if p)

    timestamp = dt.isoformat() if dt is not None else dt_raw

    return TransactionRecord(
        id=rec_id,
        season=season,
        source=source,
        admin=admin,
        timestamp=timestamp,
        upid=upid,
        player_name=player_name,
        team=team,
        pos=pos,
        age=age,
        level=level,
        team_rank=team_rank,
        rank=rank,
        eta=eta,
        player_type=player_type,
        owner=owner,
        contract=contract,
        status=status,
        years=years,
        update_type=update_type,
        event=event,
    )


def build_history() -> None:
    records: List[TransactionRecord] = []

    for csv_path in HISTORY_CSV_FILES:
        if not csv_path.exists():
            print(f"[WARN] History CSV not found: {csv_path}")
            continue

        source = csv_path.stem  # e.g. "FBP History Book - Page 1"
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):  # 2 = first data row after header
                # Skip completely empty rows
                if not any((v or "").strip() for v in row.values()):
                    continue

                rec = normalize_row(row, source=source, row_index=idx)
                records.append(rec)

    # Sort newest first by timestamp (best-effort)
    def sort_key(rec: TransactionRecord):
        dt = parse_date(rec.timestamp)
        return dt or datetime.min

    records.sort(key=sort_key, reverse=True)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in records], f, indent=2)

    print(f"Wrote {len(records)} historical transactions to {OUTPUT_FILE}")


if __name__ == "__main__":
    build_history()
