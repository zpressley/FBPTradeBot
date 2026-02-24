"""Global lock for serialising read-modify-write operations on data/*.json files.

Every runtime code path that performs a load → mutate → save cycle on shared
data files (combined_players.json, wizbucks.json, player_log.json, etc.) MUST
hold this lock for the duration of that cycle.  The git commit worker's
``git reset --hard`` retry path also holds this lock while it resets the
working directory and restores file contents, so that no API endpoint reads
stale (post-reset, pre-restore) data from disk.

Usage::

    from data_lock import DATA_LOCK

    with DATA_LOCK:
        players = load_json("data/combined_players.json")
        # ... mutate ...
        save_json("data/combined_players.json", players)
"""

import threading

DATA_LOCK = threading.Lock()
