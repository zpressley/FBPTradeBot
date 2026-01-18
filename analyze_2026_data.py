#!/usr/bin/env python3
"""
2026 Yahoo Data Structure Guide and Analysis Tools
This shows what data you'll get and how to use it
"""

import json
import os
import shutil

# Expected data structure from fetch_2026_yahoo_data.py
EXAMPLE_OUTPUT = {
    "league_info": {
        "league_id": "15505",
        "league_key": "mlb.l.15505",
        "season": "2026",
        "name": "Fantasy Baseball Pantheon",
        "current_week": "1",
        "start_week": "1", 
        "end_week": "26",
        "fetched_at": "2026-01-18T12:00:00"
    },
    "stat_categories": {
        "batting": [
            {"stat_id": "7", "name": "Runs", "abbreviation": "R"},
            {"stat_id": "12", "name": "Home Runs", "abbreviation": "HR"},
            {"stat_id": "13", "name": "RBI", "abbreviation": "RBI"},
            {"stat_id": "16", "name": "Stolen Bases", "abbreviation": "SB"},
            {"stat_id": "3", "name": "Batting Average", "abbreviation": "AVG"},
            {"stat_id": "60", "name": "On Base Percentage", "abbreviation": "OBP"},
        ],
        "pitching": [
            {"stat_id": "50", "name": "Innings Pitched", "abbreviation": "IP"},
            {"stat_id": "28", "name": "Wins", "abbreviation": "W"},
            {"stat_id": "32", "name": "Saves", "abbreviation": "SV"},
            {"stat_id": "42", "name": "Strikeouts", "abbreviation": "K"},
            {"stat_id": "26", "name": "ERA", "abbreviation": "ERA"},
            {"stat_id": "27", "name": "WHIP", "abbreviation": "WHIP"},
        ]
    },
    "teams": {
        "WIZ": {
            "team_id": "1",
            "team_name": "WIZ",
            "player_count": 26,
            "players": [
                {
                    "yahoo_id": "12345",
                    "name": "Juan Soto",
                    "first_name": "Juan",
                    "last_name": "Soto",
                    "primary_position": "OF",
                    "display_position": "OF",
                    "eligible_positions": ["OF", "Util"],
                    "mlb_team": "NYM",
                    "mlb_team_full": "New York Mets",
                    "status": "Healthy",
                    "rankings": {
                        "average_draft_position": "5",
                        "preseason_rank": "3",
                        "current_rank": "8"
                    },
                    "stats": {
                        "coverage_type": "season",
                        "stats": [
                            {"stat_id": "7", "value": "0"},  # Runs
                            {"stat_id": "12", "value": "0"}, # HR
                            {"stat_id": "13", "value": "0"}, # RBI
                        ]
                    }
                }
            ]
        }
    }
}

def analyze_position_changes():
    """Analyze position eligibility changes for 2026"""
    print("📊 Position Analysis for 2026 Season")
    print("=" * 60)
    
    try:
        # Load the actual data
        with open("data/yahoo_2026_complete.json", "r") as f:
            data = json.load(f)
        
        print("✅ Data loaded successfully\n")
        
        # Track position changes
        position_summary = {}
        multi_position_players = []
        
        for team_abbr, team_data in data["teams"].items():
            for player in team_data["players"]:
                name = player["name"]
                positions = player.get("eligible_positions", [])
                
                # Track multi-position eligibility
                if len(positions) > 1:
                    multi_position_players.append({
                        "name": name,
                        "team": team_abbr,
                        "positions": positions
                    })
                
                # Count by primary position
                primary = player.get("primary_position", "Unknown")
                if primary not in position_summary:
                    position_summary[primary] = []
                position_summary[primary].append(name)
        
        # Print position summary
        print("📍 Position Distribution:")
        for pos, players in sorted(position_summary.items()):
            print(f"  {pos}: {len(players)} players")
        
        print(f"\n🔀 Multi-Position Eligible Players: {len(multi_position_players)}")
        if multi_position_players:
            print("\nTop 10 Multi-Position Players:")
            for i, player in enumerate(multi_position_players[:10], 1):
                pos_str = ", ".join(player["positions"])
                print(f"  {i}. {player['name']} ({player['team']}): {pos_str}")
        
    except FileNotFoundError:
        print("⚠️ Data not found. Run fetch_2026_yahoo_data.py first")
        print("\nExpected position data structure:")
        print(json.dumps(EXAMPLE_OUTPUT["teams"]["WIZ"]["players"][0], indent=2))

def analyze_rankings():
    """Analyze league-specific rankings"""
    print("\n\n📈 League Rankings Analysis")
    print("=" * 60)
    
    try:
        with open("data/yahoo_2026_complete.json", "r") as f:
            data = json.load(f)
        
        # Collect players with rankings
        ranked_players = []
        
        for team_data in data["teams"].values():
            for player in team_data["players"]:
                rankings = player.get("rankings", {})
                if rankings:
                    ranked_players.append({
                        "name": player["name"],
                        "team": player.get("mlb_team", "FA"),
                        "adp": rankings.get("average_draft_position"),
                        "preseason_rank": rankings.get("preseason_rank"),
                        "current_rank": rankings.get("current_rank")
                    })
        
        # Sort by current rank (if available)
        ranked_players.sort(key=lambda x: int(x.get("current_rank", 999)) if x.get("current_rank") else 999)
        
        print(f"\nPlayers with Rankings: {len(ranked_players)}")
        
        if ranked_players:
            print("\nTop 10 Ranked Players:")
            print(f"{'Rank':<6} {'Player':<25} {'Team':<5} {'ADP':<6} {'Preseason'}")
            print("-" * 60)
            
            for player in ranked_players[:10]:
                rank = player.get("current_rank", "-")
                name = player["name"][:24]
                team = player["team"]
                adp = player.get("adp", "-")
                preseason = player.get("preseason_rank", "-")
                
                print(f"{rank:<6} {name:<25} {team:<5} {adp:<6} {preseason}")
        
    except FileNotFoundError:
        print("⚠️ Data not found. Run fetch_2026_yahoo_data.py first")
        print("\nExpected ranking data structure:")
        print(json.dumps(EXAMPLE_OUTPUT["teams"]["WIZ"]["players"][0]["rankings"], indent=2))

def compare_positions_to_sheet():
    """Compare Yahoo positions to your Google Sheet data"""
    print("\n\n🔄 Position Comparison: Yahoo vs Google Sheets")
    print("=" * 60)
    
    try:
        # Load Yahoo data
        with open("data/yahoo_2026_complete.json", "r") as f:
            yahoo_data = json.load(f)
        
        # Load combined players (includes Google Sheet data)
        with open("data/combined_players.json", "r") as f:
            sheet_data = json.load(f)
        
        # Create lookup by name
        yahoo_players = {}
        for team_data in yahoo_data["teams"].values():
            for player in team_data["players"]:
                yahoo_players[player["name"].lower()] = player
        
        # Find position differences
        position_changes = []
        
        for sheet_player in sheet_data:
            name = sheet_player.get("name", "").lower()
            
            if name in yahoo_players:
                yahoo_player = yahoo_players[name]
                yahoo_pos = yahoo_player.get("eligible_positions", [])
                sheet_pos = sheet_player.get("position", "")
                
                # Check for differences
                if sheet_pos not in yahoo_pos:
                    position_changes.append({
                        "name": sheet_player["name"],
                        "sheet_pos": sheet_pos,
                        "yahoo_pos": yahoo_pos,
                        "team": sheet_player.get("team", "")
                    })
        
        print(f"\nPosition Discrepancies Found: {len(position_changes)}")
        
        if position_changes:
            print("\nPlayers with Different Positions:")
            print(f"{'Player':<25} {'Sheet':<8} {'Yahoo':<30}")
            print("-" * 65)
            
            for change in position_changes[:20]:
                name = change["name"][:24]
                sheet_pos = change["sheet_pos"]
                yahoo_pos_str = ", ".join(change["yahoo_pos"])[:29]
                
                print(f"{name:<25} {sheet_pos:<8} {yahoo_pos_str:<30}")
            
            if len(position_changes) > 20:
                print(f"\n... and {len(position_changes) - 20} more")
        
        else:
            print("✅ All positions match!")
        
    except FileNotFoundError as e:
        print(f"⚠️ Missing data file: {e}")
        print("Run fetch_2026_yahoo_data.py and ensure combined_players.json exists")

def export_positions_csv():
    """Export position data to CSV for easy review"""
    print("\n\n📤 Exporting Position Data to CSV")
    print("=" * 60)
    
    try:
        import csv
        
        with open("data/yahoo_2026_complete.json", "r") as f:
            data = json.load(f)
        
        # Prepare CSV data
        csv_data = []
        
        for team_abbr, team_data in data["teams"].items():
            for player in team_data["players"]:
                csv_data.append({
                    "Player Name": player["name"],
                    "FBP Team": team_abbr,
                    "MLB Team": player.get("mlb_team", ""),
                    "Primary Position": player.get("primary_position", ""),
                    "Display Position": player.get("display_position", ""),
                    "All Eligible Positions": ", ".join(player.get("eligible_positions", [])),
                    "Yahoo ID": player["yahoo_id"],
                    "Current Rank": player.get("rankings", {}).get("current_rank", ""),
                    "ADP": player.get("rankings", {}).get("average_draft_position", ""),
                })
        
        # Write to CSV
        output_file = "data/2026_yahoo_positions.csv"
        with open(output_file, "w", newline="") as f:
            if csv_data:
                writer = csv.DictWriter(f, fieldnames=csv_data[0].keys())
                writer.writeheader()
                writer.writerows(csv_data)

        # Also archive a copy under data/historical/2026
        hist_dir = os.path.join("data", "historical", "2026")
        os.makedirs(hist_dir, exist_ok=True)
        hist_file = os.path.join(hist_dir, "2026_yahoo_positions.csv")
        shutil.copyfile(output_file, hist_file)

        print(f"✅ Position data exported to: {output_file}")
        print(f"✅ Historical position data exported to: {hist_file}")
        print(f"   Total players: {len(csv_data)}")
        
    except Exception as e:
        print(f"❌ Export failed: {e}")

def main():
    """Run all analysis functions"""
    print("🎯 2026 Yahoo Data Analysis Tool")
    print("=" * 60)
    print()
    
    # Check if data exists
    if not os.path.exists("data/yahoo_2026_complete.json"):
        print("⚠️ No 2026 data found!")
        print()
        print("📝 Next Steps:")
        print("   1. Run: python3 fetch_2026_yahoo_data.py")
        print("   2. Verify your Yahoo token is valid")
        print("   3. Ensure 2026 season has started")
        print()
        print("Expected data structure:")
        print(json.dumps(EXAMPLE_OUTPUT, indent=2)[:500] + "...")
        return
    
    # Run analyses
    analyze_position_changes()
    analyze_rankings()
    compare_positions_to_sheet()
    export_positions_csv()
    
    print("\n\n✅ Analysis Complete!")
    print("\n📊 Generated Files:")
    print("   • data/2026_yahoo_positions.csv")

if __name__ == "__main__":
    main()
