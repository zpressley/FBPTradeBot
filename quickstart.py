#!/usr/bin/env python3
"""
Quick Start - Test health.py locally before deploying

For local runs, only DISCORD_TOKEN is strictly required. GOOGLE_CREDS_JSON
and YAHOO_TOKEN_JSON are required for Render, but optional locally.
"""

import os
import sys
from dotenv import load_dotenv

# Load .env so local testing works without manual exports
load_dotenv()

def check_env():
    """Check if required and recommended environment variables are set."""
    required = {
        "DISCORD_TOKEN": "Discord Bot Token",
    }
    recommended = {
        "GOOGLE_CREDS_JSON": "Google Service Account JSON (required on Render)",
        "YAHOO_TOKEN_JSON": "Yahoo API Token JSON (required on Render)",
    }
    
    missing_required = []
    for var, desc in required.items():
        if not os.getenv(var):
            missing_required.append(f"  ❌ {var} ({desc})")
        else:
            print(f"  ✅ {var} is set")
    
    missing_recommended = []
    for var, desc in recommended.items():
        if not os.getenv(var):
            missing_recommended.append(f"  ⚠️ {var} ({desc})")
        else:
            print(f"  ✅ {var} is set")
    
    if missing_required:
        print("\n⚠️ Missing required environment variables:")
        print("\n".join(missing_required))
        print("\nSet them in .env file or export them:")
        print("export DISCORD_TOKEN='your_token_here'")
        return False
    
    if missing_recommended:
        print("\nℹ️ Recommended (for Render) but not required for local tests:")
        print("\n".join(missing_recommended))
    
    return True

def check_files():
    """Check if required files exist"""
    required_files = [
        "health.py",
        "requirements.txt",
        "commands/trade.py",
        "commands/roster.py",
        "commands/player.py",
        "commands/standings.py"
    ]
    
    missing = []
    for file in required_files:
        if not os.path.exists(file):
            missing.append(f"  ❌ {file}")
        else:
            print(f"  ✅ {file}")
    
    if missing:
        print("\n⚠️ Missing files:")
        print("\n".join(missing))
        return False
    
    return True

def main():
    print("=" * 60)
    print("🚀 FBP Trade Bot - Quick Start Check")
    print("=" * 60)
    
    print("\n📋 Checking Files:")
    files_ok = check_files()
    
    print("\n🔑 Checking Environment Variables:")
    env_ok = check_env()
    
    if not files_ok or not env_ok:
        print("\n❌ Pre-flight checks failed!")
        print("\n📖 See RENDER_DEPLOYMENT.md for setup instructions")
        sys.exit(1)
    
    print("\n✅ All checks passed!")
    print("\n🎯 Ready to deploy! Next steps:")
    print("  1. Push to GitHub: git push")
    print("  2. Deploy to Render (see RENDER_DEPLOYMENT.md)")
    print("\n💡 To test locally:")
    print("  python health.py")

if __name__ == "__main__":
    main()
