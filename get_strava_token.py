"""
One-time helper to obtain a valid Strava refresh token.

Run:  python get_strava_token.py
Then follow the prompts. Paste the resulting STRAVA_REFRESH_TOKEN into .env.
"""
import urllib.parse
import webbrowser
import requests
from dotenv import load_dotenv
import os

load_dotenv()

CLIENT_ID     = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env")

# Step 1 — build the authorization URL
params = urllib.parse.urlencode({
    "client_id":     CLIENT_ID,
    "redirect_uri":  "http://localhost",
    "response_type": "code",
    "approval_prompt": "force",
    "scope":         "activity:read_all",
})
auth_url = f"https://www.strava.com/oauth/authorize?{params}"

print("\n=== Strava Token Helper ===\n")
print("Opening your browser to authorize StravaXEng with your Strava account...")
print(f"\nIf the browser doesn't open, visit this URL manually:\n{auth_url}\n")
webbrowser.open(auth_url)

print("After you click 'Authorize', you'll be redirected to http://localhost/?code=XXXX&...")
print("The page will fail to load — that's fine. Copy the full URL from your browser's address bar.\n")

redirected_url = input("Paste the full redirect URL here: ").strip()

# Extract the code from the URL
parsed = urllib.parse.urlparse(redirected_url)
query  = urllib.parse.parse_qs(parsed.query)

if "code" not in query:
    raise SystemExit("Could not find 'code' in the URL. Make sure you copied the full redirect URL.")

code = query["code"][0]
print(f"\nAuthorization code: {code}")

# Step 2 — exchange the code for tokens
print("\nExchanging code for tokens...")
response = requests.post(
    "https://www.strava.com/oauth/token",
    data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    },
    timeout=10,
)

if not response.ok:
    raise SystemExit(f"Token exchange failed ({response.status_code}): {response.json()}")

data = response.json()

print("\n=== SUCCESS ===")
print(f"\nAccess token  (expires in ~6h): {data['access_token']}")
print(f"Refresh token (save this):      {data['refresh_token']}")
print(f"Athlete: {data['athlete']['firstname']} {data['athlete']['lastname']}")
print("\nUpdate your .env file with:")
print(f"  STRAVA_REFRESH_TOKEN={data['refresh_token']}")
