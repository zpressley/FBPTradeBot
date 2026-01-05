#!/usr/bin/env python3
"""
Test and refresh Yahoo token
"""

import sys
import os

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if __name__ == "__main__" else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from token_manager import get_access_token, refresh_access_token, get_stored_token
import time
import json

print("🔑 Yahoo Token Refresh Test")
print("=" * 60)

# Check current token
token_data = get_stored_token()

if token_data:
    print(f"📋 Current token info:")
    print(f"   Expires at: {token_data.get('expires_at')}")
    print(f"   Current time: {time.time()}")
    
    expired = time.time() > token_data.get('expires_at', 0)
    print(f"   Status: {'❌ EXPIRED' if expired else '✅ VALID'}")
    
    if expired:
        print(f"\n🔄 Token expired - attempting automatic refresh...")
        new_token = refresh_access_token()
        
        if new_token:
            print(f"✅ Token refreshed successfully!")
            print(f"   New token: {new_token[:30]}...")
            
            # Verify new expiry
            new_data = get_stored_token()
            new_expiry = new_data.get('expires_at')
            print(f"   New expires at: {new_expiry}")
            print(f"   Valid for: {(new_expiry - time.time()) / 3600:.1f} hours")
        else:
            print(f"❌ Token refresh failed!")
            print(f"💡 You need to re-authenticate:")
            print(f"   cd /Users/zpressley/fbp-trade-bot")
            print(f"   python3 get_token.py")
else:
    print("❌ No token found!")

# Test getting access token (should auto-refresh if needed)
print(f"\n🧪 Testing get_access_token() (should auto-refresh)...")
token = get_access_token()

if token:
    print(f"✅ Got valid token: {token[:30]}...")
else:
    print(f"❌ Could not get token")
    print(f"💡 Run: python3 get_token.py")
