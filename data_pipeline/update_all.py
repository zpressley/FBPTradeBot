import subprocess

def run_script(name):
    print(f"\n🚀 Running {name}...")
    subprocess.run(["python3", f"data_pipeline/{name}"])

def run_all():
    run_script("build_mlb_id_cache.py")         # new: rebuild cache from sheet
    run_script("track_roster_status.py")        # new: save daily snapshot
    run_script("log_roster_events.py")          # new: log call-ups/demotions
    run_script("update_yahoo_players.py")
    run_script("update_hub_players.py")
    run_script("update_wizbucks.py")
    run_script("merge_players.py")
    run_script("save_standings.py")


if __name__ == "__main__":
    run_all()
