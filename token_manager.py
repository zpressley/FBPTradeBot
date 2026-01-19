#!/usr/bin/env python3
"""Yahoo OAuth token management for FBP trade bot.

This is the canonical token_manager module. It was originally implemented
under random/token_manager.py; that file is now effectively duplicated here
so imports like `from token_manager import get_access_token` work everywhere.
"""

import json
import time
import requests
from requests.auth import HTTPBasicAuth

# Yahoo API credentials and OAuth config
# (kept in sync with the original random/token_manager.py)
CLIENT_ID = "dj0yJmk9TU9NVmVQQWFiVDRmJmQ9WVdrOWMycHRhMUpTZVRjbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PTcy"
CLIENT_SECRET = "f12120bc33df79a0e8beab79059564b9f9efcd21"
REDIRECT_URI = "https://yahoo-oauth-hyodzutfi-zach-pressleys-projects.vercel.app/api/callback"

# Yahoo API endpoints
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_FILE = "token.json"


def get_stored_token():
    """Retrieve stored access token from token.json, or None if missing."""
    try:
        with open(TOKEN_FILE, "r") as file:
            token_data = json.load(file)
            return token_data
    except FileNotFoundError:
        return None


def save_token(token_data):
    """Save token data to token.json."""
    with open(TOKEN_FILE, "w") as file:
        json.dump(token_data, file, indent=4)


def is_token_expired(token_data):
    """Return True if the stored access token is past its expires_at timestamp."""
    return time.time() > token_data["expires_at"]


def refresh_access_token():
    """Refresh the access token using the stored refresh token.

    Returns the new access token string on success, or None on failure.
    """
    token_data = get_stored_token()
    if not token_data or "refresh_token" not in token_data:
        print("No refresh token available. Re-authentication required.")
        return None

    refresh_token = token_data["refresh_token"]

    response = requests.post(
        TOKEN_URL,
        auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )

    if response.status_code == 200:
        new_token_data = response.json()
        new_token_data["expires_at"] = time.time() + new_token_data["expires_in"]
        save_token(new_token_data)
        print("🔄 Access token refreshed successfully!")
        return new_token_data["access_token"]
    else:
        print(f"❌ Failed to refresh token: {response.text}")
        return None


def get_access_token():
    """Retrieve an access token, refreshing it if expired.

    Returns the access token string, or None if re-auth is required.
    """
    token_data = get_stored_token()

    if not token_data or "access_token" not in token_data:
        print("No access token found. Re-authentication required.")
        return None

    if is_token_expired(token_data):
        print("🔄 Access token expired. Refreshing...")
        return refresh_access_token()

    return token_data["access_token"]


__all__ = [
    "get_stored_token",
    "save_token",
    "is_token_expired",
    "refresh_access_token",
    "get_access_token",
]
