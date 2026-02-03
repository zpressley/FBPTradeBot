"""Unified data pipeline entrypoint.

Legacy helper that now delegates to smart_update_all.main(), so all
new data flows (no Google Sheets in live paths) go through the smart
pipeline.
"""

from data_pipeline.smart_update_all import main as smart_main


if __name__ == "__main__":
    smart_main()
