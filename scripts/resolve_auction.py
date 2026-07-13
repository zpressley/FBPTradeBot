"""Resolve the current week's prospect auction.

Intended to be run by GitHub Actions around Sunday 2pm ET.
"""

from __future__ import annotations
import os
import sys

from datetime import datetime
from pathlib import Path

# Ensure repo root is importable when this file is executed as a script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from auction_manager import AuctionManager, ET


def main() -> None:
    manager = AuctionManager()
    now = datetime.now(tz=ET)
    result = manager.resolve_week(now=now)
    print("Auction resolution status:", result.get("status"))
    print("Winners:")
    for pid, info in (result.get("winners") or {}).items():
        print(f"  {pid}: {info['team']} for ${info['amount']}")


if __name__ == "__main__":
    main()
