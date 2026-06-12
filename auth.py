"""
Authenticate with the Robinhood MCP trading server using OAuth2 + PKCE.
Saves the access token to .env automatically.

Usage:
    python auth.py
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTHORIZE_URL = "https://robinhood.com/oauth"
TOKEN_URL = "https://api.robinhood.com/oauth2/token/"
REGISTER_URL = "https://agent.robinhood.com/oauth/trading/register"
REDIRECT_URI = "http://localhost:9876/callback"
SCOPE = "internal"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _register_client() -> str:
    """Register as a public client and return the client_id."""
    payload = json.dumps({
        "redirect_uris": [REDIRECT_URI],
        "token_endpoint_auth_method": "none",
    }).encode()
    req = urllib.request.Request(
        REGISTER_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data["client_id"]


def _exchange_code(code: str, verifier: str, client_id: str) -> dict:
    """Exchange auth code for tokens."""
    params = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _save_token(token: str, refresh: str | None = None):
    """Write / update ROBINHOOD_API_TOKEN in .env."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    keys_to_set = {"ROBINHOOD_API_TOKEN": token}
    if refresh:
        keys_to_set["ROBINHOOD_REFRESH_TOKEN"] = refresh

    updated = {k: False for k in keys_to_set}
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in keys_to_set:
            new_lines.append(f"{key}={keys_to_set[key]}\n")
            updated[key] = True
        else:
            new_lines.append(line)

    for key, value in keys_to_set.items():
        if not updated[key]:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)


def authenticate():
    print("Registering OAuth client …")
    try:
        client_id = _register_client()
        print(f"  client_id: {client_id}")
    except Exception as exc:
        print(f"  Registration failed ({exc}). Trying with client_id=robinhood-mcp-public …")
        client_id = "robinhood-mcp-public"

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    auth_url = (
        f"{AUTHORIZE_URL}?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
    )

    received: dict = {}
    server_ready = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            received["code"] = params.get("code", [None])[0]
            received["state"] = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authentication successful! You can close this tab.</h2>")
            threading.Thread(target=self.server.shutdown).start()

    httpd = http.server.HTTPServer(("localhost", 9876), _Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()

    print(f"\nOpening Robinhood login in your browser …")
    print(f"\nIf the browser does not open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for Robinhood to redirect back …")
    thread.join()

    if not received.get("code"):
        print("ERROR: No authorization code received.")
        return

    if received.get("state") != state:
        print("ERROR: State mismatch — possible CSRF. Aborting.")
        return

    print("Exchanging code for token …")
    try:
        tokens = _exchange_code(received["code"], verifier, client_id)
    except Exception as exc:
        print(f"ERROR: Token exchange failed: {exc}")
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        print(f"ERROR: No access_token in response: {tokens}")
        return

    _save_token(access_token, refresh_token)
    print("\nAuthentication successful!")
    print(f"  Access token saved to .env  (expires in {tokens.get('expires_in', '?')}s)")
    if refresh_token:
        print("  Refresh token saved to .env")
    print("\nYou can now run:  python main.py")


if __name__ == "__main__":
    authenticate()
