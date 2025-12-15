# service_time/daily_service_tracker.py - Automated daily service tracking

import os
import sys
import subprocess
from datetime import datetime

def run_daily_service_update():
    """Run the complete daily service days update"""
    
    print(f"🚀 FBP Daily Service Tracker - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Steps to run daily
    steps = [
        ("🔄 Updating Yahoo player data", "python3 data_pipeline/update_yahoo_players.py"),
        ("📊 Updating prospect data", "python3 data_pipeline/update_hub_players.py"),
        ("📈 Tracking roster status", "python3 track_roster_status.py"),
        ("📝 Logging roster events", "python3 log_roster_events.py"),
        ("⚙️ Calculating service days", "python3 count_service_days.py"),
        ("🎯 Updating service statistics", "python3 service_time/service_days_tracker.py"),
        ("💾 Merging player data", "python3 data_pipeline/merge_players.py")
    ]
    
    success_count = 0
    
    for description, command in steps:
        print(f"\n{description}...")
        
        try:
            # Run the command
            result = subprocess.run(
                command.split(),
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode == 0:
                print(f"✅ {description} completed")
                success_count += 1
            else:
                print(f"❌ {description} failed:")
                print(f"   Error: {result.stderr}")
        
        except subprocess.TimeoutExpired:
            print(f"⏰ {description} timed out")
        except FileNotFoundError:
            print(f"⚠️ {description} - Script not found, skipping")
        except Exception as e:
            print(f"❌ {description} error: {e}")
    
    print(f"\n" + "=" * 60)
    print(f"📊 Daily Update Summary:")
    print(f"   ✅ {success_count}/{len(steps)} steps completed successfully")
    
    if success_count == len(steps):
        print("🎉 All service tracking updates completed successfully!")
    else:
        print("⚠️ Some steps failed - check logs above")
    
    return success_count == len(steps)

if __name__ == "__main__":
    success = run_daily_service_update()
    sys.exit(0 if success else 1)