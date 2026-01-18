#!/usr/bin/env python3
"""
Quick Start: Fetch and Analyze 2026 Yahoo Data
Run this single script to get everything
"""

import subprocess
import sys
import os

def run_command(cmd, description):
    """Run a command and report status"""
    print(f"\n{'='*60}")
    print(f"🚀 {description}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {e}")
        print(f"Output: {e.stdout}")
        print(f"Error: {e.stderr}")
        return False

def main():
    print("🎯 FBP 2026 Yahoo Data - Quick Start")
    print("="*60)
    
    # Check prerequisites
    print("\n📋 Checking prerequisites...")
    
    if not os.path.exists("token.json"):
        print("❌ token.json not found")
        print("💡 Run: python3 get_token.py")
        return
    
    if not os.path.exists("google_creds.json"):
        print("⚠️ google_creds.json not found (needed for full analysis)")
    
    print("✅ Prerequisites OK")
    
    # Step 1: Fetch 2026 data
    success = run_command(
        "python3 fetch_2026_yahoo_data.py",
        "Step 1: Fetching 2026 Yahoo Data"
    )
    
    if not success:
        print("\n❌ Data fetch failed. Check your Yahoo token.")
        print("💡 Try running: python3 get_token.py")
        return
    
    # Step 2: Analyze data
    if os.path.exists("data/yahoo_2026_complete.json"):
        run_command(
            "python3 analyze_2026_data.py",
            "Step 2: Analyzing 2026 Data"
        )
    else:
        print("\n⚠️ Skipping analysis - no data file found")
    
    # Summary
    print("\n" + "="*60)
    print("✅ Quick Start Complete!")
    print("="*60)
    
    print("\n📁 Files Created:")
    if os.path.exists("data/yahoo_2026_complete.json"):
        print("  ✅ data/yahoo_2026_complete.json")
    if os.path.exists("data/yahoo_players.json"):
        print("  ✅ data/yahoo_players.json")
    if os.path.exists("data/2026_yahoo_positions.csv"):
        print("  ✅ data/2026_yahoo_positions.csv")
    
    print("\n🔍 What to Check:")
    print("  1. Review data/2026_yahoo_positions.csv for position changes")
    print("  2. Check if players have league-specific rankings")
    print("  3. Compare positions to your Google Sheet")
    
    print("\n📖 For detailed docs: see YAHOO_2026_GUIDE.md")

if __name__ == "__main__":
    main()
