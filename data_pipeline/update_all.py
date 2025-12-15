# data_pipeline/update_all.py - Fixed version

import subprocess
import os

def run_script(name):
    script_path = f"data_pipeline/{name}"
    root_path = name  # Try root directory too
    
    print(f"\n🚀 Running {name}...")
    
    # Try data_pipeline directory first
    if os.path.exists(script_path):
        result = subprocess.run(["python3", script_path], capture_output=True, text=True)
    # Fall back to root directory
    elif os.path.exists(root_path):
        result = subprocess.run(["python3", root_path], capture_output=True, text=True)
    else:
        print(f"⚠️ Script {name} not found in data_pipeline/ or root directory")
        return
    
    if result.returncode != 0:
        print(f"❌ Error running {name}:")
        print(result.stderr)
    else:
        print(f"✅ {name} completed successfully")
        if result.stdout:
            print(result.stdout)

def run_all():
    # Core data pipeline scripts
    scripts = [
        "update_yahoo_players.py",
        "update_hub_players.py", 
        "update_wizbucks.py",
        "merge_players.py",
        "save_standings.py"
    ]
    
    # Service days tracking scripts (run if they exist)
    service_scripts = [
        "build_mlb_id_cache.py",
        "track_roster_status.py", 
        "log_roster_events.py",
        "service_time/service_days_tracker.py"
    ]
    
    for script in scripts:
        run_script(script)
    
    print("\n🌱 Running service days tracking...")
    for script in service_scripts:
        if os.path.exists(f"data_pipeline/{script}") or os.path.exists(script):
            run_script(script)
        else:
            print(f"⚠️ Service script {script} not found, skipping...")
    
    print("\n✅ Data pipeline complete!")

if __name__ == "__main__":
    run_all()