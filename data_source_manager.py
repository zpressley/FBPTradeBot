"""
FBP Data Source Manager
Orchestrates data flow between Yahoo, MLB API, Bot Data, and UPID sources
based on the current phase of the FBP season
"""

from datetime import datetime, date
from enum import Enum
from typing import Dict, List, Optional, Any
import json
import os

class SeasonPhase(Enum):
    """Define phases of the FBP season with different data source priorities"""
    
    # Offseason (Nov - Feb 9)
    OFFSEASON_INITIAL = "offseason_initial"  # Post-season, pre-PAD
    
    # Pre-season phases
    PAD = "pad"                              # Feb 10
    POST_PAD_PRE_PPD = "post_pad_pre_ppd"   # Feb 11-16
    PPD = "ppd"                              # Feb 17
    POST_PPD_PRE_FT = "post_ppd_pre_ft"     # Feb 18
    FRANCHISE_TAG = "franchise_tag"          # Feb 19
    TRADE_WINDOW = "trade_window"            # Feb 20-27
    POST_TRADE_PRE_KD = "post_trade_pre_kd"  # Feb 28 morning
    KEEPER_DEADLINE = "keeper_deadline"      # Feb 28 EOD
    POST_KD_PRE_DRAFT = "post_kd_pre_draft"  # Mar 1-7
    KEEPER_DRAFT = "keeper_draft"            # Mar 8
    POST_DRAFT_PRE_DIV = "post_draft_pre_div" # Mar 9
    DIVISION_DRAFT = "division_draft"        # Mar 10
    POST_DIV_PRE_SEASON = "post_div_pre_season" # Mar 11-16
    
    # In-season phases
    WEEK_1_START = "week_1_start"            # Mar 17
    IN_SEASON = "in_season"                  # Mar 18 - Aug 31
    PLAYOFFS = "playoffs"                    # Sep 1-14
    
    # Post-season
    POST_SEASON = "post_season"              # Sep 15+

class DataSource(Enum):
    """Available data sources"""
    YAHOO_ROSTERS = "yahoo_rosters"          # Live Yahoo rosters (in-season only)
    YAHOO_ALL_PLAYERS = "yahoo_all_players"  # Complete Yahoo player database
    MLB_API = "mlb_api"                      # MLB Stats API (always available)
    UPID_LIST = "upid_list"                  # Prospect ID database
    BOT_DATA_KEEPERS = "bot_data_keepers"    # Keeper tracking in bot
    BOT_DATA_PROSPECTS = "bot_data_prospects" # Prospect system data
    BOT_DATA_DRAFT = "bot_data_draft"        # Draft results
    BOT_DATA_TRADES = "bot_data_trades"      # Trade history
    LEGACY_DATABASE = "legacy_database"      # Retired players archive

class DataSourceManager:
    """
    Manages which data sources are authoritative for different data types
    based on the current season phase
    """
    
    def __init__(self, config_file: str = "config/season_dates.json"):
        self.config_file = config_file
        self.season_dates = self.load_season_dates()
        self.current_phase = self.determine_current_phase()
        
    def load_season_dates(self) -> Dict[str, str]:
        """Load season dates from config file, with safe defaults.

        The config file can override any of the known keys. If some keys are
        missing, we fall back to sensible defaults so downstream code does not
        crash with KeyError.
        """
        # Base defaults (update annually if needed)
        defaults: Dict[str, str] = {
            "pad_date": "2026-02-10",
            "ppd_date": "2026-02-17",
            "franchise_tag_date": "2026-02-19",
            "trade_window_start": "2026-02-20",
            "trade_window_end": "2026-02-27",
            "keeper_deadline": "2026-02-28",
            "keeper_draft": "2026-03-08",
            "division_draft": "2026-03-10",
            "week_1_start": "2026-03-17",
            "regular_season_end": "2026-08-31",
            "playoffs_end": "2026-09-14",
        }

        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                raw = json.load(f)

            # Start with defaults, then override with any matching keys in config
            merged = defaults.copy()
            for key, value in raw.items():
                if key in merged:
                    merged[key] = value

            # Map legacy/alternate keys if present
            # e.g. config uses "preseason-trade deadline" for trade window end
            if "preseason-trade deadline" in raw:
                merged["trade_window_end"] = raw["preseason-trade deadline"]
            # KAP open/end can approximate trade window + keeper deadline
            if "kap_open_date" in raw:
                merged["trade_window_start"] = raw["kap_open_date"]
            if "kap_end_date" in raw:
                merged["keeper_deadline"] = raw["kap_end_date"]

            return merged

        return defaults
    
    def determine_current_phase(self) -> SeasonPhase:
        """Determine which phase of the season we're currently in"""
        today = date.today()
        dates = self.season_dates
        
        # Convert string dates to date objects
        pad = datetime.strptime(dates["pad_date"], "%Y-%m-%d").date()
        ppd = datetime.strptime(dates["ppd_date"], "%Y-%m-%d").date()
        ft = datetime.strptime(dates["franchise_tag_date"], "%Y-%m-%d").date()
        trade_start = datetime.strptime(dates["trade_window_start"], "%Y-%m-%d").date()
        trade_end = datetime.strptime(dates["trade_window_end"], "%Y-%m-%d").date()
        kd = datetime.strptime(dates["keeper_deadline"], "%Y-%m-%d").date()
        keeper_draft = datetime.strptime(dates["keeper_draft"], "%Y-%m-%d").date()
        div_draft = datetime.strptime(dates["division_draft"], "%Y-%m-%d").date()
        week_1 = datetime.strptime(dates["week_1_start"], "%Y-%m-%d").date()
        season_end = datetime.strptime(dates["regular_season_end"], "%Y-%m-%d").date()
        playoffs_end = datetime.strptime(dates["playoffs_end"], "%Y-%m-%d").date()
        
        # Determine phase based on date ranges
        if today == pad:
            return SeasonPhase.PAD
        elif today == ppd:
            return SeasonPhase.PPD
        elif today == ft:
            return SeasonPhase.FRANCHISE_TAG
        elif trade_start <= today <= trade_end:
            return SeasonPhase.TRADE_WINDOW
        elif today == kd:
            return SeasonPhase.KEEPER_DEADLINE
        elif today == keeper_draft:
            return SeasonPhase.KEEPER_DRAFT
        elif today == div_draft:
            return SeasonPhase.DIVISION_DRAFT
        elif today == week_1:
            return SeasonPhase.WEEK_1_START
        elif week_1 < today <= season_end:
            return SeasonPhase.IN_SEASON
        elif season_end < today <= playoffs_end:
            return SeasonPhase.PLAYOFFS
        elif today > playoffs_end:
            return SeasonPhase.POST_SEASON
        
        # Pre-season transitions
        elif pad < today < ppd:
            return SeasonPhase.POST_PAD_PRE_PPD
        elif ppd < today < ft:
            return SeasonPhase.POST_PPD_PRE_FT
        elif trade_end < today < kd:
            return SeasonPhase.POST_TRADE_PRE_KD
        elif kd < today < keeper_draft:
            return SeasonPhase.POST_KD_PRE_DRAFT
        elif keeper_draft < today < div_draft:
            return SeasonPhase.POST_DRAFT_PRE_DIV
        elif div_draft < today < week_1:
            return SeasonPhase.POST_DIV_PRE_SEASON
        else:
            return SeasonPhase.OFFSEASON_INITIAL
    
    def get_roster_source(self) -> DataSource:
        """
        Determine which source is authoritative for roster data
        based on current season phase
        """
        phase = self.current_phase
        
        # In-season: Yahoo rosters are live and authoritative
        if phase in [SeasonPhase.IN_SEASON, SeasonPhase.PLAYOFFS]:
            return DataSource.YAHOO_ROSTERS
        
        # Offseason/Pre-season: Bot data tracks keeper changes
        # Yahoo rosters frozen at last in-season state
        return DataSource.BOT_DATA_KEEPERS
    
    def get_prospect_source(self) -> DataSource:
        """Determine which source is authoritative for prospect data"""
        # Prospects are ALWAYS managed by bot data
        # (Yahoo doesn't track farm system)
        return DataSource.BOT_DATA_PROSPECTS
    
    def get_player_bio_source(self) -> List[DataSource]:
        """
        Get prioritized list of sources for player biographical data
        Returns sources in order of preference
        """
        phase = self.current_phase
        
        # Always check these sources in order:
        # 1. MLB API (most current, includes minors)
        # 2. UPID List (comprehensive prospect coverage)
        # 3. Yahoo All Players (MLB players in fantasy)
        # 4. Legacy Database (retired players)
        
        return [
            DataSource.MLB_API,
            DataSource.UPID_LIST,
            DataSource.YAHOO_ALL_PLAYERS,
            DataSource.LEGACY_DATABASE
        ]
    
    def get_stats_source(self) -> DataSource:
        """Determine which source is authoritative for player statistics"""
        # MLB API is always the source for current season stats
        return DataSource.MLB_API
    
    def should_update_yahoo_rosters(self) -> bool:
        """Check if Yahoo rosters should be synced"""
        phase = self.current_phase
        
        # Only sync Yahoo rosters during active fantasy season
        return phase in [
            SeasonPhase.WEEK_1_START,
            SeasonPhase.IN_SEASON,
            SeasonPhase.PLAYOFFS
        ]
    
    def should_update_mlb_bio(self) -> bool:
        """Check if MLB biographical data should be refreshed"""
        # Always keep MLB bio data fresh (new callups, trades, etc.)
        return True
    
    def should_use_bot_rosters(self) -> bool:
        """Check if bot-managed roster data is authoritative"""
        phase = self.current_phase
        
        # Bot rosters are authoritative during offseason/pre-season
        return phase not in [
            SeasonPhase.IN_SEASON,
            SeasonPhase.PLAYOFFS
        ]
    
    def get_combined_player_sources(self) -> Dict[str, DataSource]:
        """
        Get source mapping for building combined_players.json
        Returns which source to use for each data field
        """
        phase = self.current_phase
        in_season = self.should_update_yahoo_rosters()
        
        return {
            # Core player data (always MLB API + UPID)
            "player_id": DataSource.MLB_API,
            "name": DataSource.MLB_API,
            "position": DataSource.MLB_API,
            "mlb_team": DataSource.MLB_API,
            "age": DataSource.MLB_API,
            "upid": DataSource.UPID_LIST,
            
            # Roster assignment
            "manager": DataSource.YAHOO_ROSTERS if in_season else DataSource.BOT_DATA_KEEPERS,
            "player_type": DataSource.BOT_DATA_PROSPECTS,  # MLB vs Farm
            
            # Contract info (always bot data)
            "contract_type": DataSource.BOT_DATA_KEEPERS,
            "years_simple": DataSource.BOT_DATA_KEEPERS,
            "salary": DataSource.BOT_DATA_KEEPERS,
            
            # Fantasy data
            "yahoo_id": DataSource.YAHOO_ALL_PLAYERS,
            
            # Stats (always current)
            "current_stats": DataSource.MLB_API
        }
    
    def get_data_refresh_schedule(self) -> Dict[str, str]:
        """
        Get recommended refresh frequency for each data source
        based on current phase
        """
        phase = self.current_phase
        
        if phase in [SeasonPhase.IN_SEASON, SeasonPhase.PLAYOFFS]:
            # In-season: frequent updates
            return {
                "yahoo_rosters": "daily",
                "mlb_api_stats": "daily",
                "mlb_api_bio": "weekly",
                "upid_list": "weekly",
                "bot_data": "on_event"  # trades, auctions
            }
        elif phase in [SeasonPhase.TRADE_WINDOW, SeasonPhase.PAD, SeasonPhase.PPD]:
            # Active transaction periods
            return {
                "yahoo_rosters": "none",  # Frozen
                "mlb_api_stats": "weekly",
                "mlb_api_bio": "weekly",
                "upid_list": "weekly",
                "bot_data": "on_event"
            }
        else:
            # Quiet periods
            return {
                "yahoo_rosters": "none",
                "mlb_api_stats": "monthly",
                "mlb_api_bio": "weekly",
                "upid_list": "monthly",
                "bot_data": "manual"
            }
    
    def validate_data_consistency(self) -> Dict[str, bool]:
        """
        Check if data sources are consistent with expected state
        Returns validation results
        """
        checks = {}
        
        # Check 1: Yahoo rosters should only update in-season
        checks["yahoo_sync_appropriate"] = (
            self.should_update_yahoo_rosters() == 
            os.path.exists("data/.yahoo_sync_enabled")
        )
        
        # Check 2: Bot data should exist for keeper tracking
        checks["bot_keepers_exist"] = os.path.exists("data/bot_keepers.json")
        
        # Check 3: Prospect data should exist
        checks["bot_prospects_exist"] = os.path.exists("data/bot_prospects.json")
        
        # Check 4: Combined players should be recent
        if os.path.exists("data/combined_players.json"):
            mtime = os.path.getmtime("data/combined_players.json")
            age_hours = (datetime.now().timestamp() - mtime) / 3600
            checks["combined_players_fresh"] = age_hours < 24
        else:
            checks["combined_players_fresh"] = False
        
        return checks
    
    def get_phase_description(self) -> str:
        """Get human-readable description of current phase"""
        descriptions = {
            SeasonPhase.OFFSEASON_INITIAL: "Offseason - Post-playoffs, pre-PAD",
            SeasonPhase.PAD: "Prospect Assignment Day - Managers update farm systems",
            SeasonPhase.POST_PAD_PRE_PPD: "Between PAD and Prospect Draft",
            SeasonPhase.PPD: "Prospect Draft Day",
            SeasonPhase.POST_PPD_PRE_FT: "Between Prospect Draft and Franchise Tags",
            SeasonPhase.FRANCHISE_TAG: "Franchise Tag Deadline",
            SeasonPhase.TRADE_WINDOW: "Pre-season Trade Window OPEN",
            SeasonPhase.POST_TRADE_PRE_KD: "Between Trade Window and Keeper Deadline",
            SeasonPhase.KEEPER_DEADLINE: "Keeper Deadline Day",
            SeasonPhase.POST_KD_PRE_DRAFT: "Between Keeper Deadline and Draft",
            SeasonPhase.KEEPER_DRAFT: "Keeper Draft Day",
            SeasonPhase.POST_DRAFT_PRE_DIV: "Between Keeper Draft and Division Draft",
            SeasonPhase.DIVISION_DRAFT: "Division Draft Day",
            SeasonPhase.POST_DIV_PRE_SEASON: "Pre-season Final Prep",
            SeasonPhase.WEEK_1_START: "Week 1 Starting - Season Launch",
            SeasonPhase.IN_SEASON: "Regular Season - Live Yahoo Rosters",
            SeasonPhase.PLAYOFFS: "Playoffs",
            SeasonPhase.POST_SEASON: "Post-season"
        }
        return descriptions.get(self.current_phase, "Unknown phase")
    
    def print_status_report(self):
        """Print comprehensive status report"""
        print("=" * 60)
        print("FBP DATA SOURCE MANAGER - STATUS REPORT")
        print("=" * 60)
        print(f"📅 Current Date: {date.today()}")
        print(f"🎯 Current Phase: {self.current_phase.value}")
        print(f"📋 Description: {self.get_phase_description()}")
        print()
        
        print("📊 AUTHORITATIVE DATA SOURCES:")
        print(f"  Rosters: {self.get_roster_source().value}")
        print(f"  Prospects: {self.get_prospect_source().value}")
        print(f"  Stats: {self.get_stats_source().value}")
        print(f"  Bio Data: {', '.join([s.value for s in self.get_player_bio_source()])}")
        print()
        
        print("🔄 UPDATE SCHEDULE:")
        schedule = self.get_data_refresh_schedule()
        for source, frequency in schedule.items():
            print(f"  {source}: {frequency}")
        print()
        
        print("✅ DATA CONSISTENCY CHECKS:")
        validation = self.validate_data_consistency()
        for check, passed in validation.items():
            status = "✅" if passed else "❌"
            print(f"  {status} {check}")
        print()
        
        print("⚙️ OPERATIONAL FLAGS:")
        print(f"  Use Yahoo Rosters: {self.should_update_yahoo_rosters()}")
        print(f"  Use Bot Rosters: {self.should_use_bot_rosters()}")
        print(f"  Update MLB Bio: {self.should_update_mlb_bio()}")
        print("=" * 60)


def main():
    """Demo the data source manager"""
    manager = DataSourceManager()
    manager.print_status_report()
    
    # Show combined_players.json source mapping
    print("\n📋 COMBINED_PLAYERS.JSON SOURCE MAPPING:")
    sources = manager.get_combined_player_sources()
    for field, source in sources.items():
        print(f"  {field}: {source.value}")


if __name__ == "__main__":
    main()
