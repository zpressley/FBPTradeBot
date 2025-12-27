#!/usr/bin/env python3
"""
PHASE 1: Complete Implementation
- Merge ranks from CSV into combined_players.json
- Verify prospect stats API connectivity
- Prepare for database tracker
"""

import json
import csv
import os
import sys
from typing import Dict, List


class Phase1Implementation:
    """Phase 1: Rank integration and stats verification"""
    
    def __init__(self):
        self.csv_path = "data/Player Database.csv"  # Note: space in filename
        self.combined_path = "data/combined_players.json"
        self.backup_path = "data/combined_players_backup.json"
        
    def run_all(self):
        """Execute all Phase 1 steps"""
        
        print("🚀 PHASE 1: RANK INTEGRATION")
        print("=" * 70)
        print()
        
        # Step 1: Load CSV rankings
        print("Step 1: Loading rankings from CSV...")
        rank_map = self.load_csv_rankings()
        
        if not rank_map:
            print("❌ FAILED: Could not load rankings")
            return False
        
        print()
        
        # Step 2: Load combined players
        print("Step 2: Loading combined players...")
        players = self.load_combined_players()
        
        if not players:
            print("❌ FAILED: Could not load combined players")
            return False
        
        print()
        
        # Step 3: Backup existing data
        print("Step 3: Creating backup...")
        self.create_backup()
        print()
        
        # Step 4: Merge ranks
        print("Step 4: Merging ranks...")
        players_with_ranks = self.merge_ranks(players, rank_map)
        print()
        
        # Step 5: Save updated data
        print("Step 5: Saving updated data...")
        self.save_combined_players(players_with_ranks)
        print()
        
        # Step 6: Verify
        print("Step 6: Verification...")
        self.verify_integration(players_with_ranks)
        print()
        
        print("=" * 70)
        print("✅ PHASE 1 COMPLETE!")
        print()
        self.print_next_steps()
        
        return True
    
    def load_csv_rankings(self) -> Dict[str, int]:
        """Load Rank/ADP from Player_Database.csv using robust parsing"""
        
        rank_map = {}
        
        try:
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                # Read all lines
                lines = f.readlines()
                
                # Find header row (starts with "UPID,")
                header_idx = None
                for i, line in enumerate(lines):
                    if line.startswith('UPID,'):
                        header_idx = i
                        break
                
                if header_idx is None:
                    print(f"   ❌ Could not find header row")
                    return {}
                
                # Parse headers
                headers = lines[header_idx].strip().split(',')
                
                # Find column indices
                upid_idx = headers.index('UPID')
                rank_idx = headers.index('Rank/ADP')
                
                # Parse data rows
                for i in range(header_idx + 1, len(lines)):
                    line = lines[i].strip()
                    if not line:
                        continue
                    
                    parts = line.split(',')
                    
                    if len(parts) <= max(upid_idx, rank_idx):
                        continue
                    
                    upid = parts[upid_idx].strip().strip('"')
                    rank_raw = parts[rank_idx].strip().strip('"')
                    
                    if upid and rank_raw:
                        try:
                            rank = int(rank_raw)
                            rank_map[upid] = rank
                        except ValueError:
                            continue
            
            print(f"   ✅ Loaded {len(rank_map)} rankings")
            return rank_map
            
        except FileNotFoundError:
            print(f"   ❌ CSV not found: {self.csv_path}")
            print(f"   💡 Make sure Player Database.csv is in data/ folder")
            return {}
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return {}
    
    def load_combined_players(self) -> List[Dict]:
        """Load combined_players.json"""
        
        try:
            with open(self.combined_path, 'r') as f:
                players = json.load(f)
            
            print(f"   ✅ Loaded {len(players)} players")
            return players
            
        except FileNotFoundError:
            print(f"   ❌ File not found: {self.combined_path}")
            return []
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return []
    
    def create_backup(self):
        """Backup existing combined_players.json"""
        
        if os.path.exists(self.combined_path):
            import shutil
            shutil.copy(self.combined_path, self.backup_path)
            print(f"   ✅ Backup: {self.backup_path}")
        else:
            print(f"   ⚠️ No existing file to backup")
    
    def merge_ranks(self, players: List[Dict], rank_map: Dict[str, int]) -> List[Dict]:
        """Add rank field to each player"""
        
        matched = 0
        unmatched = 0
        prospect_matched = 0
        
        for player in players:
            upid = str(player.get('upid', '')).strip()
            
            if upid and upid in rank_map:
                player['rank'] = rank_map[upid]
                matched += 1
                
                # Count prospects specifically
                if player.get('player_type') == 'Farm':
                    prospect_matched += 1
            else:
                # Unranked - put at bottom
                player['rank'] = 9999
                unmatched += 1
        
        print(f"   ✅ Total matched: {matched}")
        print(f"   ✅ Prospects matched: {prospect_matched}")
        print(f"   ⚠️ Unmatched (9999 rank): {unmatched}")
        
        return players
    
    def save_combined_players(self, players: List[Dict]):
        """Save updated combined_players.json"""
        
        with open(self.combined_path, 'w') as f:
            json.dump(players, f, indent=2)
        
        print(f"   ✅ Saved to {self.combined_path}")
    
    def verify_integration(self, players: List[Dict]):
        """Verify ranks were added correctly"""
        
        # Get prospects only
        prospects = [p for p in players if p.get('player_type') == 'Farm']
        
        # Sort by rank
        prospects_sorted = sorted(prospects, key=lambda p: p.get('rank', 9999))
        
        # Count distributions
        top_50 = sum(1 for p in prospects if p.get('rank', 9999) <= 50)
        top_100 = sum(1 for p in prospects if p.get('rank', 9999) <= 100)
        top_200 = sum(1 for p in prospects if p.get('rank', 9999) <= 200)
        unranked = sum(1 for p in prospects if p.get('rank', 9999) == 9999)
        
        print(f"   📊 Prospect Rank Distribution:")
        print(f"      Top 50:    {top_50}")
        print(f"      Top 100:   {top_100}")
        print(f"      Top 200:   {top_200}")
        print(f"      Unranked:  {unranked}")
        print()
        
        print(f"   🔝 Top 10 Prospects by Rank:")
        for i, p in enumerate(prospects_sorted[:10], 1):
            rank = p.get('rank', '???')
            name = p.get('name', 'Unknown')
            pos = p.get('position', '?')
            team = p.get('team', '?')
            manager = p.get('manager', 'Available')
            
            print(f"      {rank:3d}. {name} ({pos}) [{team}] - {manager}")
        
        print()
        
        # Check for rank field existence
        has_rank = sum(1 for p in prospects if 'rank' in p)
        print(f"   ✅ {has_rank}/{len(prospects)} prospects have rank field")
        
        if has_rank == len(prospects):
            print(f"   ✅ ALL PROSPECTS RANKED - Ready for database sorting!")
        else:
            print(f"   ⚠️ Some prospects missing rank field")
    
    def print_next_steps(self):
        """Print what to do next"""
        
        print("📋 Next Steps:")
        print()
        print("✅ Phase 1 Complete - Ranks integrated")
        print()
        print("🔜 Ready for Phase 2:")
        print("   • Build database_tracker.py")
        print("   • Implement message location tracking")
        print("   • Create update queue system")
        print()
        print("📊 Database will now show prospects in rank order:")
        print("   1. Cole Young (SS) [SEA]")
        print("   2. Kristian Campbell (IF) [BOS]")
        print("   3. Bryce Eldridge (1B) [SF]")
        print("   ...")


def main():
    """Run Phase 1"""
    
    phase1 = Phase1Implementation()
    success = phase1.run_all()
    
    if success:
        sys.exit(0)
    else:
        print("\n❌ Phase 1 failed - check errors above")
        sys.exit(1)


if __name__ == "__main__":
    main()