import requests
import json
import webbrowser
import time
from requests.auth import HTTPBasicAuth
from token_manager import save_token

# Yahoo API Credentials
CLIENT_ID = "dj0yJmk9TU9NVmVQQWFiVDRmJmQ9WVdrOWMycHRhMUpTZVRjbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PTcy"
CLIENT_SECRET = "f12120bc33df79a0e8beab79059564b9f9efcd21"
REDIRECT_URI = "https://yahoo-oauth-hyodzutfi-zach-pressleys-projects.vercel.app/api/callback"

# Yahoo API Endpoints
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_FILE = "token.json"

# Step 1: Open Yahoo authorization URL
auth_url = f"{AUTH_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code"
print(f"🔗 Open this URL in your browser and log in: \n{auth_url}")
webbrowser.open(auth_url)  # Opens the URL automatically

# Step 2: Get Authorization Code from User
authorization_code = input("\nPaste the authorization code here: ")

# Step 3: Exchange Authorization Code for Access Token
response = requests.post(
    TOKEN_URL,
    auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
    data={
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": authorization_code
    }
)

# Step 4: Store Access & Refresh Tokens
if response.status_code == 200:
    token_data = response.json()
    token_data["expires_at"] = time.time() + token_data["expires_in"]
    save_token(token_data)
    print("\n✅ Access token saved successfully!")
else:
    print(f"❌ Error fetching access token: {response.text}")
