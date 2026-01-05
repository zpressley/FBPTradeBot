#!/usr/bin/env python3
"""
Debug: Test token_manager import from data_pipeline folder
"""

import sys
import os

print("🔍 Debugging token_manager import")
print("=" * 60)

# Show current directory
print(f"Current directory: {os.getcwd()}")
print(f"Script location: {os.path.abspath(__file__)}")

# Calculate parent directory
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

print(f"\nScript dir: {script_dir}")
print(f"Parent dir: {parent_dir}")

# Check if token_manager.py exists in parent
token_manager_path = os.path.join(parent_dir, "token_manager.py")
print(f"\nLooking for: {token_manager_path}")
print(f"Exists: {os.path.exists(token_manager_path)}")

# Show current sys.path
print(f"\nCurrent sys.path:")
for i, path in enumerate(sys.path):
    print(f"  {i}. {path}")

# Try to add parent to path
print(f"\nAdding parent dir to sys.path...")
sys.path.insert(0, parent_dir)

print(f"Parent dir in sys.path: {parent_dir in sys.path}")

# Try import
print(f"\nAttempting import...")
try:
    from token_manager import get_access_token
    print("✅ SUCCESS! token_manager imported")
    
    # Test it
    token = get_access_token()
    if token:
        print(f"✅ Token retrieved: {token[:20]}...")
    else:
        print("⚠️ Token is None (might be expired)")
        
except ImportError as e:
    print(f"❌ FAILED: {e}")
    
    # Additional debugging
    print(f"\nChecking what's in parent directory:")
    parent_files = os.listdir(parent_dir)
    py_files = [f for f in parent_files if f.endswith('.py')]
    print(f"Python files in parent: {py_files}")
    
    if 'token_manager.py' in py_files:
        print("✅ token_manager.py EXISTS in parent dir!")
        print("❌ But Python can't import it - this is weird")
    else:
        print("❌ token_manager.py NOT FOUND in parent dir!")
        print(f"💡 Expected at: {token_manager_path}")
