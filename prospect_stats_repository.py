"""
Prospect Stats Repository - Comprehensive MLB stats database
Integrates with existing MLB API connections to build rich prospect profiles
"""

import json
import os
import requests
from datetime import datetime
from typing import Dict, List, Optional
import time


class ProspectStatsRepository:
    """
    Manages comprehensive prospect statistics database.
    
    Features:
    - Fetches stats from MLB Stats API
    - Caches results in JSON database
    - Tracks ownership (Available/FC/PC/DC)
    - Supports both batters and pitchers
    - Incremental updates
    """
    
    def __init__(self, data_dir: str = "data/prospect_stats"):
        self.base_url = "https://statsapi.mlb.com/api/v1"
        self.data_dir = data_dir
        self.current_season = 2025  # Will adjust if needed
        
        # Ensure data directory exists
        os.makedirs(data_dir, exist_ok=True)
        
        # Database files
        self.db_file = os.path.join(data_dir, "prospect_database.json")
        self.cache_file = os.path.join(data_dir, "stats_cache.json")
        self.metadata_file = os.path.join(data_dir, "metadata.json")
        
        # Load existing data
        self.database = self._load_database()
        self.stats_cache = self._load_cache()
        self.metadata = self._load_metadata()
        
        # Load MLB ID mappings
        self.mlb_id_cache = self._load_mlb_id_cache()
        
        # Load ownership data
        self.ownership_data = self._load_ownership_data()
    
    # ========== DATA LOADING ==========
    
    def _load_database(self) -> Dict:
        """Load main prospect database"""
        if os.path.exists(self.db_file):
            with open(self.db_file, 'r') as f:
                return json.load(f)
        return {}
    
    def _load_cache(self) -> Dict:
        """Load stats cache for faster lookups"""
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        return {}
    
    def _load_metadata(self) -> Dict:
        """Load metadata (last update times, etc)"""
        if os.path.exists(self.metadata_file):
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        return {
            "last_full_update": None,
            "last_incremental_update": None,
            "total_prospects": 0,
            "prospects_with_stats": 0
        }
    
    def _load_mlb_id_cache(self) -> Dict:
        """Load MLB ID mappings from existing cache"""
        cache_path = "data/mlb_id_cache.json"
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                return json.load(f)
        return {}
    
    def _load_ownership_data(self) -> Dict:
        """Load ownership data from combined_players.json"""
        combined_path = "data/combined_players.json"
        if os.path.exists(combined_path):
            with open(combined_path, 'r') as f:
                players = json.load(f)
            
            # Create lookup by name
            ownership = {}
            for player in players:
                if player.get('player_type') == 'Farm':
                    name = player['name']
                    ownership[name] = {
                        'manager': player.get('manager', 'UC'),
                        'contract_type': player.get('years_simple', 'UC'),
                        'upid': player.get('upid', ''),
                        'position': player.get('position', ''),
                        'team': player.get('team', '')
                    }
            
            return ownership
        return {}
    
    # ========== MLB API CALLS ==========
    
    def fetch_player_stats(self, mlb_id: int, player_name: str) -> Optional[Dict]:
        """
        Fetch comprehensive stats for a player from MLB API.
        
        Returns:
            Dict with batting/pitching stats or None if failed
        """
        
        # Check cache first (avoid API spam)
        cache_key = f"{mlb_id}_{self.current_season}"
        if cache_key in self.stats_cache:
            cache_entry = self.stats_cache[cache_key]
            # Cache valid for 1 day
            cache_time = datetime.fromisoformat(cache_entry['cached_at'])
            if (datetime.now() - cache_time).days < 1:
                return cache_entry['stats']
        
        print(f"🔍 Fetching stats for {player_name} (MLB ID: {mlb_id})...")
        
        try:
            # Get player info with stats
            url = f"{self.base_url}/people/{mlb_id}"
            params = {
                'hydrate': 'stats(group=[hitting,pitching],type=[season,career]),currentTeam',
                'season': self.current_season
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code != 200:
                print(f"⚠️ API returned {response.status_code} for {player_name}")
                return None
            
            data = response.json()
            
            if not data.get('people'):
                return None
            
            player = data['people'][0]
            
            # Extract stats
            stats = self._extract_player_stats(player)
            
            # Cache the result
            self.stats_cache[cache_key] = {
                'stats': stats,
                'cached_at': datetime.now().isoformat()
            }
            self._save_cache()
            
            return stats
            
        except Exception as e:
            print(f"❌ Error fetching stats for {player_name}: {e}")
            return None
    
    def _extract_player_stats(self, player_data: Dict) -> Dict:
        """Extract relevant stats from MLB API response"""
        
        stats = {
            'player_id': player_data.get('id'),
            'full_name': player_data.get('fullName', ''),
            'age': player_data.get('currentAge'),
            'position': player_data.get('primaryPosition', {}).get('abbreviation', ''),
            'current_team': player_data.get('currentTeam', {}).get('name', 'Free Agent'),
            'current_team_abbr': player_data.get('currentTeam', {}).get('abbreviation', 'FA'),
            'mlb_debut': player_data.get('mlbDebutDate'),
            'batting': {},
            'pitching': {},
            'last_updated': datetime.now().isoformat()
        }
        
        # Extract stats from API response
        for stat_group in player_data.get('stats', []):
            group = stat_group.get('group', {}).get('displayName', '')
            stat_type = stat_group.get('type', {}).get('displayName', '')
            
            for split in stat_group.get('splits', []):
                stat = split.get('stat', {})
                
                # Season hitting stats
                if group == 'hitting' and stat_type == 'season':
                    stats['batting']['season'] = {
                        'games': stat.get('gamesPlayed', 0),
                        'at_bats': stat.get('atBats', 0),
                        'runs': stat.get('runs', 0),
                        'hits': stat.get('hits', 0),
                        'doubles': stat.get('doubles', 0),
                        'triples': stat.get('triples', 0),
                        'home_runs': stat.get('homeRuns', 0),
                        'rbi': stat.get('rbi', 0),
                        'stolen_bases': stat.get('stolenBases', 0),
                        'caught_stealing': stat.get('caughtStealing', 0),
                        'walks': stat.get('baseOnBalls', 0),
                        'strikeouts': stat.get('strikeOuts', 0),
                        'avg': float(stat.get('avg', 0)),
                        'obp': float(stat.get('obp', 0)),
                        'slg': float(stat.get('slg', 0)),
                        'ops': float(stat.get('ops', 0)),
                        'plate_appearances': stat.get('plateAppearances', 0)
                    }
                
                # Career hitting stats
                elif group == 'hitting' and stat_type == 'career':
                    stats['batting']['career'] = {
                        'games': stat.get('gamesPlayed', 0),
                        'at_bats': stat.get('atBats', 0),
                        'home_runs': stat.get('homeRuns', 0),
                        'rbi': stat.get('rbi', 0),
                        'stolen_bases': stat.get('stolenBases', 0),
                        'avg': float(stat.get('avg', 0)),
                        'ops': float(stat.get('ops', 0))
                    }
                
                # Season pitching stats
                elif group == 'pitching' and stat_type == 'season':
                    stats['pitching']['season'] = {
                        'games': stat.get('gamesPlayed', 0),
                        'games_started': stat.get('gamesStarted', 0),
                        'wins': stat.get('wins', 0),
                        'losses': stat.get('losses', 0),
                        'saves': stat.get('saves', 0),
                        'holds': stat.get('holds', 0),
                        'innings_pitched': float(stat.get('inningsPitched', 0)),
                        'hits': stat.get('hits', 0),
                        'runs': stat.get('runs', 0),
                        'earned_runs': stat.get('earnedRuns', 0),
                        'walks': stat.get('baseOnBalls', 0),
                        'strikeouts': stat.get('strikeOuts', 0),
                        'home_runs': stat.get('homeRuns', 0),
                        'era': float(stat.get('era', 0)),
                        'whip': float(stat.get('whip', 0)),
                        'batters_faced': stat.get('battersFaced', 0)
                    }
                
                # Career pitching stats
                elif group == 'pitching' and stat_type == 'career':
                    stats['pitching']['career'] = {
                        'games': stat.get('gamesPlayed', 0),
                        'wins': stat.get('wins', 0),
                        'losses': stat.get('losses', 0),
                        'saves': stat.get('saves', 0),
                        'innings_pitched': float(stat.get('inningsPitched', 0)),
                        'strikeouts': stat.get('strikeOuts', 0),
                        'era': float(stat.get('era', 0)),
                        'whip': float(stat.get('whip', 0))
                    }
        
        return stats
    
    # ========== DATABASE MANAGEMENT ==========
    
    def update_prospect(self, player_name: str, force_refresh: bool = False) -> bool:
        """
        Update a single prospect's stats in the database.
        
        Args:
            player_name: Full player name
            force_refresh: Force API call even if cached
            
        Returns:
            True if successful, False otherwise
        """
        
        # Get ownership info
        ownership = self.ownership_data.get(player_name, {})
        
        # Get MLB ID
        upid = ownership.get('upid', '')
        mlb_id = None
        
        if upid:
            cache_entry = self.mlb_id_cache.get(str(upid))
            if cache_entry:
                mlb_id = cache_entry.get('mlb_id')
        
        if not mlb_id:
            print(f"⚠️ No MLB ID found for {player_name}")
            return False
        
        # Clear cache if force refresh
        if force_refresh:
            cache_key = f"{mlb_id}_{self.current_season}"
            if cache_key in self.stats_cache:
                del self.stats_cache[cache_key]
        
        # Fetch stats
        stats = self.fetch_player_stats(mlb_id, player_name)
        
        if not stats:
            return False
        
        # Create prospect entry
        prospect_entry = {
            **stats,
            'ownership': {
                'status': ownership.get('contract_type', 'UC'),
                'manager': ownership.get('manager', 'Available'),
                'upid': upid
            },
            'metadata': {
                'last_updated': datetime.now().isoformat(),
                'has_mlb_stats': bool(stats.get('batting', {}).get('season') or 
                                     stats.get('pitching', {}).get('season'))
            }
        }
        
        # Update database
        self.database[player_name] = prospect_entry
        
        return True
    
    def bulk_update_prospects(self, player_names: Optional[List[str]] = None, 
                             delay: float = 0.5) -> Dict:
        """
        Update multiple prospects (or all if none specified).
        
        Args:
            player_names: List of player names to update (None = all)
            delay: Delay between API calls (be nice to MLB API)
            
        Returns:
            Dict with update statistics
        """
        
        if player_names is None:
            # Update all prospects in ownership data
            player_names = list(self.ownership_data.keys())
        
        print(f"🔄 Starting bulk update of {len(player_names)} prospects...")
        
        stats = {
            'total': len(player_names),
            'successful': 0,
            'failed': 0,
            'skipped': 0
        }
        
        for i, name in enumerate(player_names, 1):
            print(f"📊 [{i}/{len(player_names)}] Updating {name}...")
            
            if self.update_prospect(name):
                stats['successful'] += 1
            else:
                stats['failed'] += 1
            
            # Rate limiting
            if i < len(player_names):
                time.sleep(delay)
        
        # Save database
        self._save_database()
        
        # Update metadata
        self.metadata['last_full_update'] = datetime.now().isoformat()
        self.metadata['total_prospects'] = len(self.database)
        self.metadata['prospects_with_stats'] = sum(
            1 for p in self.database.values() 
            if p.get('metadata', {}).get('has_mlb_stats')
        )
        self._save_metadata()
        
        print(f"\n✅ Bulk update complete!")
        print(f"   Successful: {stats['successful']}")
        print(f"   Failed: {stats['failed']}")
        print(f"   Total in DB: {len(self.database)}")
        
        return stats
    
    # ========== QUERYING ==========
    
    def get_prospect(self, player_name: str) -> Optional[Dict]:
        """Get a single prospect's full profile"""
        return self.database.get(player_name)
    
    def get_all_prospects(self) -> Dict:
        """Get all prospects in database"""
        return self.database
    
    def get_available_prospects(self) -> Dict:
        """Get only available (unowned) prospects"""
        return {
            name: data for name, data in self.database.items()
            if data.get('ownership', {}).get('status') == 'UC'
        }
    
    def get_owned_prospects(self, manager: Optional[str] = None) -> Dict:
        """Get owned prospects, optionally filtered by manager"""
        result = {}
        for name, data in self.database.items():
            ownership = data.get('ownership', {})
            if ownership.get('status') != 'UC':
                if manager is None or ownership.get('manager') == manager:
                    result[name] = data
        return result
    
    def get_by_position(self, position: str) -> List[Dict]:
        """Get all prospects at a specific position"""
        return [
            {**data, 'name': name}
            for name, data in self.database.items()
            if data.get('position', '').upper() == position.upper()
        ]
    
    def search_prospects(self, **filters) -> List[Dict]:
        """
        Search prospects with flexible filters.
        
        Example:
            search_prospects(position='SP', manager='WIZ', min_strikeouts=50)
        """
        results = []
        
        for name, data in self.database.items():
            match = True
            
            # Check each filter
            for key, value in filters.items():
                if key == 'position':
                    if data.get('position', '').upper() != value.upper():
                        match = False
                elif key == 'manager':
                    if data.get('ownership', {}).get('manager') != value:
                        match = False
                elif key == 'status':
                    if data.get('ownership', {}).get('status') != value:
                        match = False
                elif key.startswith('min_'):
                    # Min value filters (e.g., min_strikeouts)
                    stat_name = key[4:]  # Remove 'min_'
                    stat_value = self._get_nested_stat(data, stat_name)
                    if stat_value is None or stat_value < value:
                        match = False
                elif key.startswith('max_'):
                    # Max value filters (e.g., max_era)
                    stat_name = key[4:]  # Remove 'max_'
                    stat_value = self._get_nested_stat(data, stat_name)
                    if stat_value is None or stat_value > value:
                        match = False
            
            if match:
                results.append({**data, 'name': name})
        
        return results
    
    def _get_nested_stat(self, data: Dict, stat_name: str) -> Optional[float]:
        """Helper to get nested stat values"""
        # Try batting season stats
        batting_season = data.get('batting', {}).get('season', {})
        if stat_name in batting_season:
            return batting_season[stat_name]
        
        # Try pitching season stats
        pitching_season = data.get('pitching', {}).get('season', {})
        if stat_name in pitching_season:
            return pitching_season[stat_name]
        
        return None
    
    # ========== FORMATTING ==========
    
    def format_prospect_card(self, player_name: str) -> str:
        """Format a prospect's stats as a card for Discord"""
        prospect = self.get_prospect(player_name)
        
        if not prospect:
            return f"❌ {player_name} not found in database"
        
        # Header
        lines = []
        lines.append(f"**{prospect['full_name']}**")
        lines.append(f"{prospect['position']} | {prospect['current_team_abbr']} | Age {prospect['age']}")
        
        # Ownership
        ownership = prospect['ownership']
        if ownership['status'] == 'UC':
            lines.append(f"✅ **AVAILABLE**")
        else:
            lines.append(f"❌ **{ownership['status']}** ({ownership['manager']})")
        
        lines.append("")
        
        # Batting stats if available
        batting_season = prospect.get('batting', {}).get('season', {})
        if batting_season and batting_season.get('at_bats', 0) > 0:
            lines.append("**⚾ 2025 Batting Stats**")
            lines.append(f"G: {batting_season['games']} | AB: {batting_season['at_bats']}")
            lines.append(f"AVG: {batting_season['avg']:.3f} | OBP: {batting_season['obp']:.3f} | SLG: {batting_season['slg']:.3f}")
            lines.append(f"HR: {batting_season['home_runs']} | RBI: {batting_season['rbi']} | SB: {batting_season['stolen_bases']}")
            lines.append(f"BB: {batting_season['walks']} | K: {batting_season['strikeouts']}")
        
        # Pitching stats if available
        pitching_season = prospect.get('pitching', {}).get('season', {})
        if pitching_season and pitching_season.get('innings_pitched', 0) > 0:
            lines.append("**🥎 2025 Pitching Stats**")
            lines.append(f"G: {pitching_season['games']} | GS: {pitching_season['games_started']}")
            lines.append(f"W-L: {pitching_season['wins']}-{pitching_season['losses']} | SV: {pitching_season['saves']}")
            lines.append(f"IP: {pitching_season['innings_pitched']:.1f} | ERA: {pitching_season['era']:.2f} | WHIP: {pitching_season['whip']:.2f}")
            lines.append(f"K: {pitching_season['strikeouts']} | BB: {pitching_season['walks']}")
        
        # If no stats
        if not batting_season.get('at_bats') and not pitching_season.get('innings_pitched'):
            lines.append("*No 2025 MLB stats yet*")
        
        return "\n".join(lines)
    
    # ========== SAVE METHODS ==========
    
    def _save_database(self):
        """Save main database"""
        with open(self.db_file, 'w') as f:
            json.dump(self.database, f, indent=2)
    
    def _save_cache(self):
        """Save stats cache"""
        with open(self.cache_file, 'w') as f:
            json.dump(self.stats_cache, f, indent=2)
    
    def _save_metadata(self):
        """Save metadata"""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)
    
    # ========== EXPORT ==========
    
    def export_to_json(self, filename: str = None) -> str:
        """Export database to JSON file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"prospect_stats_export_{timestamp}.json"
        
        export_path = os.path.join(self.data_dir, filename)
        
        with open(export_path, 'w') as f:
            json.dump({
                'export_date': datetime.now().isoformat(),
                'total_prospects': len(self.database),
                'prospects': self.database,
                'metadata': self.metadata
            }, f, indent=2)
        
        return export_path


# ========== STANDALONE FUNCTIONS ==========

def initialize_repository():
    """Initialize a new stats repository"""
    repo = ProspectStatsRepository()
    print(f"✅ Repository initialized")
    print(f"📁 Data directory: {repo.data_dir}")
    print(f"📊 Current prospects in DB: {len(repo.database)}")
    return repo


def update_all_prospects():
    """Update all prospects - run this daily or weekly"""
    repo = ProspectStatsRepository()
    stats = repo.bulk_update_prospects()
    return repo, stats


if __name__ == "__main__":
    print("🎯 Prospect Stats Repository - Initialization")
    print("=" * 60)
    
    # Initialize
    repo = initialize_repository()
    
    # Check if we should do a full update
    if len(repo.database) == 0:
        print("\n📊 No data in database. Running full update...")
        print("⚠️ This will take a while (~5 minutes for 100+ prospects)")
        
        response = input("\nProceed with full update? (y/n): ")
        if response.lower() == 'y':
            update_all_prospects()
    else:
        print(f"\n✅ Database loaded with {len(repo.database)} prospects")
        print(f"📅 Last updated: {repo.metadata.get('last_full_update', 'Never')}")
