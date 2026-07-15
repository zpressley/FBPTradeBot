#!/usr/bin/env python3
"""
Enhanced MLB Stats CSV Collector for Prospect Analysis
Now includes comprehensive position data for both batters and pitchers
"""

import pandas as pd
import requests
import json
import time
from datetime import datetime, timedelta
import os

class EnhancedMLBStatsCollector:
    def __init__(self):
        self.base_url = "https://statsapi.mlb.com/api/v1"
        self.current_season = 2025  # Changed to 2024 since 2025 season hasn't started
        self.output_dir = "prospect_analysis_data"
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Load Yahoo ownership data if available
        self.yahoo_ownership = self.load_yahoo_ownership()
        
        # Team mapping using official MLB abbreviations from your grid
        self.team_mapping = {
            108: 'LAA',  # Los Angeles Angels
            109: 'ARI',  # Arizona Diamondbacks
            110: 'BAL',  # Baltimore Orioles
            111: 'BOS',  # Boston Red Sox
            112: 'CHC',  # Chicago Cubs
            113: 'CIN',  # Cincinnati Reds
            114: 'CLE',  # Cleveland Guardians
            115: 'COL',  # Colorado Rockies
            116: 'DET',  # Detroit Tigers
            117: 'HOU',  # Houston Astros
            118: 'KC',   # Kansas City Royals
            119: 'LAD',  # Los Angeles Dodgers
            120: 'WSH',  # Washington Nationals
            121: 'NYY',  # New York Yankees
            133: 'OAK',  # Oakland Athletics
            134: 'PIT',  # Pittsburgh Pirates
            135: 'SD',   # San Diego Padres
            136: 'SEA',  # Seattle Mariners
            137: 'SF',   # San Francisco Giants
            138: 'STL',  # St. Louis Cardinals
            139: 'TB',   # Tampa Bay Rays
            140: 'TEX',  # Texas Rangers
            141: 'TOR',  # Toronto Blue Jays
            142: 'MIN',  # Minnesota Twins
            143: 'PHI',  # Philadelphia Phillies
            144: 'ATL',  # Atlanta Braves
            145: 'CWS',  # Chicago White Sox
            146: 'MIA',  # Miami Marlins
            158: 'MIL'   # Milwaukee Brewers
        }
    
    def load_yahoo_ownership(self):
        """Load Yahoo ownership data from your existing files"""
        try:
            # Try to load from your existing Yahoo data
            with open("data/yahoo_players.json", 'r') as f:
                yahoo_data = json.load(f)
            
            # Create a lookup dictionary by player name
            ownership_lookup = {}
            for manager, players in yahoo_data.items():
                for player in players:
                    player_name = player.get('name', '').strip()
                    if player_name:
                        ownership_lookup[player_name.lower()] = {
                            'manager': manager,
                            'yahoo_team': manager,
                            'yahoo_id': player.get('yahoo_id', ''),
                            'position': player.get('position', '')
                        }
            
            print(f"📊 Loaded Yahoo ownership for {len(ownership_lookup)} players")
            return ownership_lookup
            
        except FileNotFoundError:
            print("⚠️ No Yahoo ownership data found (data/yahoo_players.json)")
            print("💡 Run your Yahoo data updater first for ownership info")
            return {}
        except Exception as e:
            print(f"⚠️ Error loading Yahoo data: {e}")
            return {}
        
    def get_all_players_with_stats(self, min_games=5):
        """Get all players with minimum games played from all MLB teams"""
        print("🔍 Fetching all MLB players with stats...")
        
        # First, get all MLB teams
        teams_url = f"{self.base_url}/teams"
        params = {'sportId': '1', 'season': self.current_season}
        
        try:
            teams_response = requests.get(teams_url, params=params, timeout=30)
            teams_response.raise_for_status()
            teams_data = teams_response.json()
            
            all_players = []
            teams = teams_data.get('teams', [])
            
            print(f"📊 Checking {len(teams)} MLB teams for players...")
            
            for team in teams:
                team_id = team.get('id')
                team_name = team.get('abbreviation', 'UNK')
                team_abbrev = team_name.lower()
                
                # Get roster for each team with enhanced hydration including positions
                roster_url = f"{self.base_url}/teams/{team_id}/roster"
                roster_params = {
                    'season': self.current_season,
                    'hydrate': 'person(stats(group=[hitting,pitching,fielding],type=[season]),positions,primaryPosition)'
                }
                
                try:
                    roster_response = requests.get(roster_url, params=roster_params, timeout=15)
                    if roster_response.status_code != 200:
                        print(f"⚠️ Could not get roster for {team_name}")
                        continue
                        
                    roster_data = roster_response.json()
                    roster = roster_data.get('roster', [])
                    
                    team_players_added = 0
                    for roster_entry in roster:
                        player = roster_entry.get('person', {})
                        
                        # Inject team info directly into player data
                        player['_team_id'] = team_id
                        player['_team_abbrev'] = team_abbrev
                        player['_team_name'] = team_name
                        
                        # Check if player has meaningful stats
                        stats = player.get('stats', [])
                        has_meaningful_stats = False
                        
                        for stat_group in stats:
                            splits = stat_group.get('splits', [])
                            for split in splits:
                                stat = split.get('stat', {})
                                games = stat.get('gamesPlayed', 0) or stat.get('games', 0)
                                if games >= min_games:
                                    has_meaningful_stats = True
                                    break
                            if has_meaningful_stats:
                                break
                        
                        if has_meaningful_stats:
                            all_players.append(player)
                            team_players_added += 1
                    
                    if team_players_added > 0:
                        print(f"  ✅ {team_name}: {team_players_added} players")
                    
                except Exception as e:
                    print(f"⚠️ Error fetching {team_name} roster: {e}")
                    continue
            
            print(f"✅ Found {len(all_players)} total players with {min_games}+ games")
            return all_players
            
        except Exception as e:
            print(f"❌ Error fetching teams/players: {e}")
            return []
    
    def get_detailed_player_stats(self, player_id):
        """Get comprehensive stats for a specific player with team and position info"""
        url = f"{self.base_url}/people/{player_id}"
        params = {
            'hydrate': 'stats(group=[hitting,pitching,fielding],type=[season,career,saberMetrics]),currentTeam,positions,primaryPosition',
            'season': self.current_season
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"⚠️ Error fetching detailed stats for player {player_id}: {e}")
            return None
    
    def get_player_position_details(self, player_data):
        """Extract simplified position information from player data"""
        player = player_data.get('people', [{}])[0]
        
        position_details = {
            'primary_position': '',
            'all_positions': '',
            'is_pitcher': False,
            'is_two_way': False,
            'pitcher_type': ''  # SP or RP
        }
        
        # Get primary position and convert to standard abbreviation
        primary_pos = player.get('primaryPosition', {})
        if primary_pos:
            pos_code = primary_pos.get('code', '')
            position_details['primary_position'] = self.convert_position_code_to_abbrev(pos_code)
            position_details['is_pitcher'] = primary_pos.get('type') == 'Pitcher'
        
        # Get all positions and convert to abbreviated list
        positions = player.get('positions', [])
        pos_abbreviations = []
        
        has_pitcher = False
        has_position = False
        
        for pos in positions:
            pos_code = pos.get('code', '')
            pos_abbrev = self.convert_position_code_to_abbrev(pos_code)
            if pos_abbrev and pos_abbrev not in pos_abbreviations:
                pos_abbreviations.append(pos_abbrev)
            
            # Check for two-way eligibility
            if pos.get('type') == 'Pitcher':
                has_pitcher = True
            elif pos.get('type') in ['Infielder', 'Outfielder', 'Catcher']:
                has_position = True
        
        position_details['all_positions'] = ', '.join(pos_abbreviations)
        position_details['is_two_way'] = has_pitcher and has_position
        
        # Determine pitcher type (SP or RP) from stats
        if position_details['is_pitcher']:
            position_details['pitcher_type'] = self.determine_pitcher_type(player_data)
        
        return position_details
    
    def convert_position_code_to_abbrev(self, pos_code):
        """Convert MLB position code to standard abbreviation"""
        position_map = {
            '1': 'P',      # Pitcher
            '2': 'C',      # Catcher
            '3': '1B',     # First Base
            '4': '2B',     # Second Base
            '5': '3B',     # Third Base
            '6': 'SS',     # Shortstop
            '7': 'LF',     # Left Field
            '8': 'CF',     # Center Field
            '9': 'RF',     # Right Field
            '10': 'DH',    # Designated Hitter
            '11': 'OF',    # General Outfield
            '12': 'IF'     # General Infield
        }
        return position_map.get(str(pos_code), '')
    
    def determine_pitcher_type(self, player_data):
        """Determine if pitcher is SP (Starter) or RP (Reliever)"""
        player = player_data.get('people', [{}])[0]
        
        # Look for pitching stats to determine role
        for stat_group in player.get('stats', []):
            group = stat_group.get('group', {}).get('displayName', '')
            if group == 'pitching':
                for split in stat_group.get('splits', []):
                    stat = split.get('stat', {})
                    
                    starts = stat.get('gamesStarted', 0)
                    games = stat.get('gamesPlayed', 0)
                    
                    if games > 0:
                        # If 50% or more of games are starts, consider them a starter
                        if starts >= (games * 0.5):
                            return 'SP'
                        else:
                            return 'RP'
        
        # Default to RP if no stats available
        return 'RP'
    

    
    def extract_batter_data(self, player_data):
        """Extract batter statistics with comprehensive position data"""
        player = player_data.get('people', [{}])[0]
        
        # Get team info - use injected team data first, then fallback to API data
        team_abbrev = player.get('_team_abbrev', 'FA')
        team_id = player.get('_team_id', '')
        
        # If no injected team data, try API sources
        if team_abbrev == 'FA':
            current_team = player.get('currentTeam')
            if current_team:
                team_abbrev = current_team.get('abbreviation', 'FA')
                team_id = current_team.get('id', '')
            
            # Try team mapping if we have a team ID
            if team_id and team_id in self.team_mapping:
                team_abbrev = self.team_mapping[team_id]
        
        # Get Yahoo ownership info
        player_name = player.get('fullName', '')
        yahoo_info = self.yahoo_ownership.get(player_name.lower(), {})
        
        # Get comprehensive position data
        position_details = self.get_player_position_details(player_data)
        
        # Basic info - matching Raw Bat column structure
        basic_info = {
            'rank': '',  # A: rank (will be filled later)
            'team': team_abbrev,  # B: team
            'age': player.get('currentAge', ''),  # C: age
            'teamId': team_id,  # D: teamId
            'full_name': player_name,  # E: full_name
            'playerId': player.get('id'),  # F: playerId
        }
        
        # Initialize stats - matching Raw Bat columns exactly
        current_stats = {
            'atBats': 0,           # G: atBats
            'runs': 0,             # H: runs
            'hits': 0,             # I: hits
            'doubles': 0,          # J: doubles
            'triples': 0,          # K: triples
            'homeRuns': 0,         # L: homeRuns
            'obp': 0.0,            # M: obp
            'ops': 0.0,            # N: ops
            'slg': 0.0,            # O: slg
            'rbi': 0,              # P: rbi
            'baseOnBalls': 0,      # Q: baseOnBalls
            'strikeOuts': 0,       # R: strikeOuts
            'stolenBases': 0,      # S: stolenBases
            'caughtStealing': 0,   # T: caughtStealing
            'leftOnBase': 0,       # U: leftOnBase
            # Additional stats
            'avg': 0.0,            # V: batting average
            'plateAppearances': 0, # W: plate appearances
            'totalBases': 0,       # X: total bases
            'games': 0,            # Y: games played
            'position': position_details['primary_position'],  # Z: position
            'sportAbbrev': 'MLB',  # AA: sport abbreviation
        }
        
        # Yahoo ownership columns
        yahoo_columns = {
            'yahoo_owner': yahoo_info.get('manager', ''),           # AB: Yahoo owner
            'yahoo_team': yahoo_info.get('yahoo_team', ''),         # AC: Yahoo team
            'yahoo_id': yahoo_info.get('yahoo_id', ''),             # AD: Yahoo player ID
            'yahoo_position': yahoo_info.get('position', '')        # AE: Yahoo position
        }
        
        # Simplified position columns (new additions)
        position_columns = {
            'primary_position': position_details['primary_position'],                    # AF: Primary Position (1B, 2B, SS, etc.)
            'all_positions': position_details['all_positions'],                          # AG: All Positions (comma-separated)
            'is_two_way': position_details['is_two_way'],                               # AH: Two-Way Player
        }
        
        # Process stats from API
        for stat_group in player.get('stats', []):
            stat_type = stat_group.get('type', {}).get('displayName', '')
            group = stat_group.get('group', {}).get('displayName', '')
            
            if group == 'hitting' and stat_type == 'season':
                for split in stat_group.get('splits', []):
                    stat = split.get('stat', {})
                    
                    current_stats.update({
                        'atBats': stat.get('atBats', 0),
                        'runs': stat.get('runs', 0),
                        'hits': stat.get('hits', 0),
                        'doubles': stat.get('doubles', 0),
                        'triples': stat.get('triples', 0),
                        'homeRuns': stat.get('homeRuns', 0),
                        'obp': float(stat.get('obp', 0.0)),
                        'ops': float(stat.get('ops', 0.0)),
                        'slg': float(stat.get('slg', 0.0)),
                        'rbi': stat.get('rbi', 0),
                        'baseOnBalls': stat.get('baseOnBalls', 0),
                        'strikeOuts': stat.get('strikeOuts', 0),
                        'stolenBases': stat.get('stolenBases', 0),
                        'caughtStealing': stat.get('caughtStealing', 0),
                        'leftOnBase': stat.get('leftOnBase', 0),
                        'avg': float(stat.get('avg', 0.0)),
                        'plateAppearances': stat.get('plateAppearances', 0),
                        'totalBases': stat.get('totalBases', 0),
                        'games': stat.get('gamesPlayed', 0)
                    })
                    break
        
        # Combine all data
        return {**basic_info, **current_stats, **yahoo_columns, **position_columns}
    
    def extract_pitcher_data(self, player_data):
        """Extract pitcher statistics with comprehensive position data"""
        player = player_data.get('people', [{}])[0]
        
        # Get team info - use injected team data first, then fallback to API data
        team_abbrev = player.get('_team_abbrev', 'FA')
        team_id = player.get('_team_id', '')
        
        # If no injected team data, try API sources
        if team_abbrev == 'FA':
            current_team = player.get('currentTeam')
            if current_team:
                team_abbrev = current_team.get('abbreviation', 'FA')
                team_id = current_team.get('id', '')
            
            # Try team mapping if we have a team ID
            if team_id and team_id in self.team_mapping:
                team_abbrev = self.team_mapping[team_id]
        
        # Get Yahoo ownership info
        player_name = player.get('fullName', '')
        yahoo_info = self.yahoo_ownership.get(player_name.lower(), {})
        
        # Get comprehensive position data
        position_details = self.get_player_position_details(player_data)
        
        # Basic info - matching Raw Pitch column structure
        basic_info = {
            'rank': '',  # A: rank (will be filled later)
            'team': team_abbrev,  # B: team
            'age': player.get('currentAge', ''),  # C: age
            'teamId': team_id,  # D: teamId
            'playerId': player.get('id'),  # E: playerId
            'full_name': player_name,  # F: full_name
        }
        
        # Initialize stats - matching Raw Pitch columns exactly
        current_stats = {
            'gamesPitched': 0,          # G: gamesPitched
            'whip': 0.0,                # H: whip
            'inningsPitched': 0.0,      # I: inningsPitched
            'hits': 0,                  # J: hits
            'runs': 0,                  # K: runs
            'earnedRuns': 0,            # L: earnedRuns
            'baseOnBalls': 0,           # M: baseOnBalls
            'strikeOuts': 0,            # N: strikeOuts
            'homeRuns': 0,              # O: homeRuns
            'outs': 0,                  # P: outs
            'battersFaced': 0,          # Q: battersFaced
            'pitchesThrown': 0,         # R: pitchesThrown
            'era': 0.0,                 # S: era
            'saves': 0,                 # T: saves
            'holds': 0,                 # U: holds
            # Additional pitching stats
            'blownSaves': 0,            # V: blownSaves
            'wins': 0,                  # W: wins
            'losses': 0,                # X: losses
            'gamesStarted': 0,          # Y: gamesStarted
            'completeGames': 0,         # Z: completeGames
            'shutouts': 0,              # AA: shutouts
        }
        
        # Yahoo ownership columns
        yahoo_columns = {
            'yahoo_owner': yahoo_info.get('manager', ''),           # AB: Yahoo owner
            'yahoo_team': yahoo_info.get('yahoo_team', ''),         # AC: Yahoo team
            'yahoo_id': yahoo_info.get('yahoo_id', ''),             # AD: Yahoo player ID
            'yahoo_position': yahoo_info.get('position', '')        # AE: Yahoo position
        }
        
        # Simplified position columns (new additions)
        position_columns = {
            'primary_position': position_details['primary_position'],                    # AF: Primary Position (1B, 2B, SS, etc.)
            'all_positions': position_details['all_positions'],                          # AG: All Positions (comma-separated)
            'is_two_way': position_details['is_two_way'],                               # AH: Two-Way Player
            'pitcher_type': position_details['pitcher_type'],                           # AI: SP or RP
        }
        
        # Process stats from API
        for stat_group in player.get('stats', []):
            stat_type = stat_group.get('type', {}).get('displayName', '')
            group = stat_group.get('group', {}).get('displayName', '')
            
            if group == 'pitching' and stat_type == 'season':
                for split in stat_group.get('splits', []):
                    stat = split.get('stat', {})
                    
                    current_stats.update({
                        'gamesPitched': stat.get('gamesPlayed', 0),
                        'whip': float(stat.get('whip', 0.0)),
                        'inningsPitched': float(stat.get('inningsPitched', '0.0')),
                        'hits': stat.get('hits', 0),
                        'runs': stat.get('runs', 0),
                        'earnedRuns': stat.get('earnedRuns', 0),
                        'baseOnBalls': stat.get('baseOnBalls', 0),
                        'strikeOuts': stat.get('strikeOuts', 0),
                        'homeRuns': stat.get('homeRuns', 0),
                        'outs': stat.get('outs', 0),
                        'battersFaced': stat.get('battersFaced', 0),
                        'pitchesThrown': stat.get('numberOfPitches', 0),
                        'era': float(stat.get('era', 0.0)),
                        'saves': stat.get('saves', 0),
                        'holds': stat.get('holds', 0),
                        'blownSaves': stat.get('blownSaves', 0),
                        'wins': stat.get('wins', 0),
                        'losses': stat.get('losses', 0),
                        'gamesStarted': stat.get('gamesStarted', 0),
                        'completeGames': stat.get('completeGames', 0),
                        'shutouts': stat.get('shutouts', 0)
                    })
                    break
        
        # Update pitcher type after stats are processed
        position_columns['pitcher_type'] = self.determine_pitcher_type(player_data)
        
        # Combine all data
        return {**basic_info, **current_stats, **yahoo_columns, **position_columns}
    

    
    def collect_all_data(self):
        """Main function to collect all player data with enhanced position information"""
        print("🚀 Starting Enhanced MLB Stats Collection with Position Data")
        print("=" * 70)
        
        # Get all players with stats
        all_players = self.get_all_players_with_stats(min_games=5)
        
        if not all_players:
            print("❌ No players found. This might be due to:")
            print("   • Off-season period with no current stats")
            print("   • API endpoint changes")
            print("   • Network connectivity issues")
            print(f"   • Current season set to {self.current_season}")
            print("\n💡 Try adjusting the season or minimum games threshold")
            return [], []
        
        batters_data = []
        pitchers_data = []
        
        print(f"\n📊 Processing {len(all_players)} players with position data...")
        
        for i, player in enumerate(all_players, 1):
            player_id = player['id']
            name = player.get('fullName', 'Unknown')
            position = player.get('primaryPosition', {}).get('abbreviation', '')
            
            if i % 25 == 0:
                print(f"  📈 Processed {i}/{len(all_players)} players...")
            
            # Get detailed stats with position data
            detailed_data = self.get_detailed_player_stats(player_id)
            if not detailed_data:
                continue
            
            # Determine if player is primarily a batter or pitcher
            is_pitcher = position in ['P', 'SP', 'RP', 'CP']
            
            # Check stats to confirm classification and get meaningful data
            has_pitching_stats = False
            has_hitting_stats = False
            pitching_games = 0
            hitting_games = 0
            
            for stat_group in detailed_data.get('people', [{}])[0].get('stats', []):
                group = stat_group.get('group', {}).get('displayName', '')
                if group == 'pitching':
                    for split in stat_group.get('splits', []):
                        games = split.get('stat', {}).get('gamesPlayed', 0)
                        if games > 0:
                            has_pitching_stats = True
                            pitching_games = games
                elif group == 'hitting':
                    for split in stat_group.get('splits', []):
                        games = split.get('stat', {}).get('gamesPlayed', 0)
                        if games > 0:
                            has_hitting_stats = True
                            hitting_games = games
            
            # Extract appropriate data based on primary role and meaningful stats
            if has_pitching_stats and (is_pitcher or pitching_games >= hitting_games):
                pitcher_data = self.extract_pitcher_data(detailed_data)
                if pitcher_data.get('gamesPitched', 0) > 0:
                    pitchers_data.append(pitcher_data)
            
            # For position players OR pitchers who also hit significantly
            if has_hitting_stats and (not is_pitcher or hitting_games > pitching_games):
                batter_data = self.extract_batter_data(detailed_data)
                if batter_data.get('atBats', 0) > 0:
                    batters_data.append(batter_data)
            
            # Rate limiting - be respectful to MLB API
            if i % 50 == 0:
                time.sleep(1)
            else:
                time.sleep(0.1)
        
        # Create DataFrames and save to CSV
        self.save_data_to_csv(batters_data, pitchers_data)
        
        print(f"\n✅ Enhanced data collection complete!")
        print(f"⚾ Batters: {len(batters_data)} players (with position data)")
        print(f"🥎 Pitchers: {len(pitchers_data)} players (with position data)")
        
        return batters_data, pitchers_data
    
    def save_data_to_csv(self, batters_data, pitchers_data):
        """Save collected data to CSV files with comprehensive position data"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        
        if batters_data:
            # Add ranking based on OPS
            batters_sorted = sorted(batters_data, key=lambda x: x.get('ops', 0), reverse=True)
            for i, batter in enumerate(batters_sorted, 1):
                batter['rank'] = i
            
            # Define simplified column order with position data
            batter_columns = [
                'rank', 'team', 'age', 'teamId', 'full_name', 'playerId',
                'atBats', 'runs', 'hits', 'doubles', 'triples', 'homeRuns',
                'obp', 'ops', 'slg', 'rbi', 'baseOnBalls', 'strikeOuts',
                'stolenBases', 'caughtStealing', 'leftOnBase', 'avg',
                'plateAppearances', 'totalBases', 'games', 'position', 'sportAbbrev',
                'yahoo_owner', 'yahoo_team', 'yahoo_id', 'yahoo_position',
                # Simplified position columns
                'primary_position', 'all_positions', 'is_two_way'
            ]
            
            batters_df = pd.DataFrame(batters_sorted)
            batters_df = batters_df.reindex(columns=batter_columns)
            
            batters_file = f"{self.output_dir}/enhanced_raw_bat_{timestamp}.csv"
            batters_df.to_csv(batters_file, index=False)
            print(f"💾 Enhanced Raw Bat data (with positions) saved to: {batters_file}")
        
        if pitchers_data:
            # Add ranking based on ERA (ascending - lower is better)
            pitchers_sorted = sorted(pitchers_data, key=lambda x: x.get('era', 99.99))
            for i, pitcher in enumerate(pitchers_sorted, 1):
                pitcher['rank'] = i
            
            # Define simplified column order with position data
            pitcher_columns = [
                'rank', 'team', 'age', 'teamId', 'playerId', 'full_name',
                'gamesPitched', 'whip', 'inningsPitched', 'hits', 'runs', 'earnedRuns',
                'baseOnBalls', 'strikeOuts', 'homeRuns', 'outs', 'battersFaced',
                'pitchesThrown', 'era', 'saves', 'holds', 'blownSaves', 'wins', 'losses',
                'gamesStarted', 'completeGames', 'shutouts',
                'yahoo_owner', 'yahoo_team', 'yahoo_id', 'yahoo_position',
                # Simplified position columns
                'primary_position', 'all_positions', 'is_two_way', 'pitcher_type'
            ]
            
            pitchers_df = pd.DataFrame(pitchers_sorted)
            pitchers_df = pitchers_df.reindex(columns=pitcher_columns)
            
            pitchers_file = f"{self.output_dir}/enhanced_raw_pitch_{timestamp}.csv"
            pitchers_df.to_csv(pitchers_file, index=False)
            print(f"💾 Enhanced Raw Pitch data (with positions) saved to: {pitchers_file}")
        
        print(f"\n📊 Simplified CSV files with clean position data ready!")
        print(f"🔗 Position columns now include:")
        print(f"   • Primary position (1B, 2B, SS, C, LF, CF, RF, DH, P)")
        print(f"   • All positions (comma-separated list)")
        print(f"   • Two-way player flag (True/False)")
        print(f"   • Pitcher type (SP or RP for pitchers)")

def main():
    """Main execution function"""
    collector = EnhancedMLBStatsCollector()
    
    print("⚾ Enhanced MLB Stats Collector with Position Data")
    print("=" * 60)
    print("Collecting comprehensive MLB stats AND position eligibility")
    print("for analysis similar to your Prospect Analyzer spreadsheet.\n")
    
    # Check if we're in off-season and adjust
    print(f"🗓️ Collecting data for {collector.current_season} season...")
    print("💡 If no players found, try adjusting season in the code\n")
    
    # Collect all data with enhanced position information
    batters, pitchers = collector.collect_all_data()
    
    if batters or pitchers:
        print(f"\n🎉 Enhanced Collection Complete!")
        print(f"📁 Files saved in: {collector.output_dir}/")
        print(f"🔍 Import the enhanced CSV files into your analysis spreadsheet")
        print(f"📊 Position data now included for fantasy eligibility analysis")
    else:
        print(f"\n⚠️ No data collected. Try adjusting:")
        print(f"   • Season year (currently {collector.current_season})")
        print(f"   • Minimum games threshold (currently 5)")
        print(f"   • Check if MLB season is active")

if __name__ == "__main__":
    main()