"""Resolve the current week's prospect auction.

Intended to be run by GitHub Actions around Sunday 2pm ET.
"""

from __future__ import annotations

from datetime import datetime

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
