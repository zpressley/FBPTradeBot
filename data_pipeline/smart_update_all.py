"""
Smart Data Pipeline for FBP
Uses DataSourceManager to determine which sources to update
"""

import os
import subprocess
import sys
from datetime import datetime

# Ensure project root is on sys.path so we can import data_source_manager
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from data_source_manager import DataSourceManager, SeasonPhase

class SmartDataPipeline:
    """
    Intelligent data pipeline that only updates necessary sources
    based on current season phase
    """
    
    def __init__(self):
        self.manager = DataSourceManager()
        self.phase = self.manager.current_phase
        self.results = []
    
    def run_script(self, name: str, reason: str = ""):
        """Run a data pipeline script with logging.

        `name` can be either a bare filename (e.g. "update_yahoo_players.py"),
        which will be resolved under data_pipeline/, or a relative path that
        already includes a directory (e.g. "random/build_mlb_id_cache.py").
        """
        print(f"\n🚀 Running {name}...")
        if reason:
            print(f"   Reason: {reason}")
        
        # Determine script path
        if os.sep in name:
            script_path = name  # caller provided a relative path
        else:
            script_path = f"data_pipeline/{name}"
        
        try:
            result = subprocess.run(
                ["python3", script_path],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                print(f"✅ {name} completed successfully")
                self.results.append((name, "SUCCESS", reason))
                return True
            else:
                print(f"❌ {name} failed:")
                print(result.stderr)
                self.results.append((name, "FAILED", reason))
                return False
                
        except subprocess.TimeoutExpired:
            print(f"⏱️ {name} timed out after 5 minutes")
            self.results.append((name, "TIMEOUT", reason))
            return False
        except Exception as e:
            print(f"❌ {name} error: {e}")
            self.results.append((name, "ERROR", reason))
            return False
    
    def update_player_bio_data(self):
        """
        Update player biographical data from MLB API + UPID
        Always runs to keep player database current
        """
        print("\n📋 UPDATING PLAYER BIOGRAPHICAL DATA")
        print("=" * 50)
        
        # Build MLB ID cache (includes UPID mapping). Script currently lives
        # under random/, so we call it via explicit relative path.
        self.run_script(
            "random/build_mlb_id_cache.py",
            "Sync UPID mappings and MLB IDs"
        )
        
        print("✅ Player bio data updated")
    
    def update_roster_data(self):
        """
        Update roster data from appropriate source
        Yahoo in-season, Bot data offseason
        """
        print("\n👥 UPDATING ROSTER DATA")
        print("=" * 50)
        
        if self.manager.should_update_yahoo_rosters():
            # In-season: sync from Yahoo
            print("📊 Using Yahoo rosters (in-season)")
            self.run_script(
                "update_yahoo_players.py",
                "Sync live Yahoo rosters"
            )
        else:
            # Offseason: use bot keeper data
            print("🤖 Using bot keeper data (offseason)")
            # Bot data is updated via Discord commands, not pipeline
            print("   (Bot keepers managed via Discord, no sync needed)")
        
        print("✅ Roster data updated")
    
    def update_prospect_data(self):
        """
        Update prospect data (always from bot)
        Includes service time tracking
        """
        print("\n🌱 UPDATING PROSPECT DATA")
        print("=" * 50)
        
        # Bot manages prospects, but we track service time from MLB
        self.run_script(
            "track_roster_status.py",
            "Track MLB roster status for prospects"
        )
        
        self.run_script(
            "log_roster_events.py",
            "Log call-ups and demotions"
        )
        
        print("✅ Prospect data updated")
    
    def update_wizbucks(self):
        """Update WizBucks data.

        As of 2026, WizBucks are managed exclusively by the bot's
        on-chain ledger (update_wizbucks_ledger.py and in-season
        auction flows). The legacy Google Sheets sync is disabled to
        avoid stale data and broken credentials.
        """
        print("\n💰 UPDATING WIZBUCKS")
        print("=" * 50)
        print("ℹ️ Skipping Google Sheets sync; WizBucks now sourced from bot ledger only.")
        # Record a no-op result so the pipeline summary stays accurate.
        self.results.append(("update_wizbucks (skipped)", "SUCCESS", "Using bot ledger as source of truth"))
        print("✅ WizBucks step skipped (bot ledger is source of truth)")
    
    def update_standings(self):
        """
        Update standings from Yahoo
        Only relevant during fantasy season
        """
        if self.phase not in [SeasonPhase.IN_SEASON, SeasonPhase.PLAYOFFS]:
            print("\n📊 STANDINGS: Skipped (not in active season)")
            return
        
        print("\n📊 UPDATING STANDINGS")
        print("=" * 50)
        
        self.run_script(
            "save_standings.py",
            "Get current standings and matchups from Yahoo"
        )
        
        print("✅ Standings updated")
    
    def merge_all_data(self):
        """
        Merge data from all sources into combined_players.json
        Uses source priority from DataSourceManager
        """
        print("\n🔀 MERGING DATA SOURCES")
        print("=" * 50)
        
        sources = self.manager.get_combined_player_sources()
        
        print("📋 Source mapping:")
        for field, source in sources.items():
            print(f"   {field}: {source.value}")
        
        # Update hub players (from Google Sheets keeper database)
        self.run_script(
            "update_hub_players.py",
            "Get keeper/prospect data from Google Sheets"
        )
        
        # Merge all sources
        self.run_script(
            "merge_players.py",
            "Combine Yahoo, Sheets, and MLB data"
        )
        
        print("✅ Data merged")
    
    def run_full_pipeline(self):
        """Run complete data update based on current phase"""
        print("=" * 60)
        print("🎯 FBP SMART DATA PIPELINE")
        print("=" * 60)
        
        # Print status
        self.manager.print_status_report()
        
        print("\n🔄 STARTING DATA UPDATE")
        print("=" * 60)
        
        start_time = datetime.now()
        
        # 1. Always update player bio data (MLB callups, trades, etc.)
        if self.manager.should_update_mlb_bio():
            self.update_player_bio_data()
        
        # 2. Update rosters from appropriate source
        self.update_roster_data()
        
        # 3. Update prospect tracking (service time, roster status)
        self.update_prospect_data()
        
        # 4. Update WizBucks
        self.update_wizbucks()
        
        # 5. Update standings (only in-season)
        self.update_standings()
        
        # 6. Merge everything together
        self.merge_all_data()
        
        # Summary
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print("\n" + "=" * 60)
        print("📊 PIPELINE SUMMARY")
        print("=" * 60)
        print(f"⏱️ Duration: {duration:.1f} seconds")
        print(f"📋 Scripts run: {len(self.results)}")
        print()
        
        success = sum(1 for _, status, _ in self.results if status == "SUCCESS")
        failed = sum(1 for _, status, _ in self.results if status != "SUCCESS")
        
        print(f"✅ Successful: {success}")
        if failed > 0:
            print(f"❌ Failed: {failed}")
            print("\n❌ FAILED SCRIPTS:")
            for name, status, reason in self.results:
                if status != "SUCCESS":
                    print(f"   • {name}: {status}")
        
        print("=" * 60)
        
        return failed == 0
    
    def run_offseason_only(self):
        """Run minimal updates for offseason"""
        print("\n❄️ OFFSEASON MODE - Minimal Updates")
        print("=" * 50)
        
        # Only update:
        # 1. MLB bio data (trades, signings)
        # 2. Merge with bot keeper data (WizBucks already tracked by bot)
        
        self.update_player_bio_data()
        # self.update_wizbucks()  # Legacy Sheets sync disabled; bot ledger is source of truth.
        self.merge_all_data()
        
        print("✅ Offseason update complete")
    
    def run_preseason_draft_mode(self):
        """Run updates during draft periods (PAD, PPD, Keeper Draft)"""
        print("\n📝 DRAFT MODE - Extended Updates")
        print("=" * 50)
        
        # During drafts, need everything except Yahoo rosters. WizBucks
        # remain managed by the bot ledger, so we do not hit Google
        # Sheets here either.
        self.update_player_bio_data()
        # self.update_wizbucks()  # Legacy Sheets sync disabled.
        self.update_prospect_data()
        self.merge_all_data()
        
        print("✅ Draft mode update complete")


def main():
    """Main pipeline execution"""
    pipeline = SmartDataPipeline()
    
    # Determine which update mode to use
    phase = pipeline.manager.current_phase
    
    if phase == SeasonPhase.OFFSEASON_INITIAL:
        pipeline.run_offseason_only()
    elif phase in [SeasonPhase.PAD, SeasonPhase.PPD, SeasonPhase.KEEPER_DRAFT]:
        pipeline.run_preseason_draft_mode()
    else:
        pipeline.run_full_pipeline()


if __name__ == "__main__":
    main()
