#!/usr/bin/env python3
"""
Quick test to verify Yahoo API access for 2026 season
"""

import requests
import os
import sys

# Ensure token_manager from random/ is importable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, "random"))

from token_manager import get_access_token

LEAGUE_ID = "15505"
GAME_KEY = "mlb"

def test_yahoo_connection():
    """Test basic Yahoo API connectivity"""
    print("🔍 Testing Yahoo API Connection for 2026")
    print("=" * 60)
    
    try:
        # Get token
        print("\n1️⃣ Getting access token...")
        token = get_access_token()
        
        if not token:
            print("❌ Failed to get access token")
            print("💡 Run: python3 get_token.py")
            return False
        
        print("✅ Access token retrieved")
        
        # Test API call
        print("\n2️⃣ Testing API endpoint...")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{GAME_KEY}.l.{LEAGUE_ID}"
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ API connection successful!")
            
            # Parse response
            from xml.etree import ElementTree as ET
            root = ET.fromstring(response.text)
            
            # Extract key info
            season = root.find(".//season")
            name = root.find(".//name")
            current_week = root.find(".//current_week")
            
            print("\n📊 League Information:")
            print(f"   League: {name.text if name is not None else 'Unknown'}")
            print(f"   Season: {season.text if season is not None else 'Unknown'}")
            print(f"   Current Week: {current_week.text if current_week is not None else 'N/A'}")
            
            # Check if 2026 season
            if season is not None and season.text == "2026":
                print("\n🎉 2026 season confirmed!")
                print("✅ Ready to fetch full dataset")
                return True
            else:
                print(f"\n⚠️ Season is {season.text if season else 'Unknown'}, not 2026")
                print("💡 Yahoo may still be on 2025 season")
                return False
        
        elif response.status_code == 401:
            print("❌ Authorization failed (401)")
            print("💡 Token may be expired. Run: python3 get_token.py")
            return False
        
        elif response.status_code == 404:
            print("❌ League not found (404)")
            print("💡 Check league ID: https://baseball.fantasysports.yahoo.com/b1/8560")
            return False
        
        else:
            print(f"❌ Unexpected status: {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error: {e}")
        print("💡 Check internet connection")
        return False
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_roster_access():
    """Test if we can access roster data"""
    print("\n\n3️⃣ Testing roster data access...")
    print("=" * 60)
    
    try:
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        # Try to get one team's roster
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{GAME_KEY}.l.{LEAGUE_ID}/teams;team_keys={GAME_KEY}.l.{LEAGUE_ID}.t.1/roster"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(response.text)
            
            # Count players
            players = root.findall(".//player")
            print(f"✅ Roster access successful!")
            print(f"   Sample team has {len(players)} players")
            
            # Check for position data
            if players:
                sample_player = players[0]
                name = sample_player.find(".//name/full")
                pos = sample_player.find(".//display_position")
                
                if name is not None and pos is not None:
                    print(f"   Sample player: {name.text} ({pos.text})")
                    print("✅ Position data available")
            
            return True
        else:
            print(f"❌ Failed to access roster: {response.status_code}")
            return False
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    """Run all tests"""
    print("🧪 Yahoo API Connection Test")
    print("League: Fantasy Baseball Pantheon (15505)")
    print("Link: https://baseball.fantasysports.yahoo.com/b1/8560")
    print("=" * 60)
    
    # Test 1: Basic connection
    conn_success = test_yahoo_connection()
    
    # Test 2: Roster access
    roster_success = test_roster_access() if conn_success else False
    
    # Summary
    print("\n\n📊 Test Summary")
    print("=" * 60)
    print(f"{'Test':<30} {'Status':<10}")
    print("-" * 60)
    print(f"{'Yahoo API Connection':<30} {'✅ PASS' if conn_success else '❌ FAIL':<10}")
    print(f"{'Roster Data Access':<30} {'✅ PASS' if roster_success else '❌ FAIL':<10}")
    
    if conn_success and roster_success:
        print("\n🎉 All tests passed!")
        print("\n✅ Ready to run: python3 quickstart_2026.py")
    else:
        print("\n⚠️ Some tests failed")
        print("\n💡 Troubleshooting:")
        print("   1. Run: python3 get_token.py (if token expired)")
        print("   2. Check league URL: https://baseball.fantasysports.yahoo.com/b1/8560")
        print("   3. Verify 2026 season has started in Yahoo")
        print("   4. Check internet connection")

if __name__ == "__main__":
    main()
