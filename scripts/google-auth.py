#!/usr/bin/env python3
"""One-time sign-in for the read-only Gmail and Drive tools. Run it yourself, on your laptop, from the repository:

    .venv/bin/python scripts/google-auth.py

It asks for the OAuth client id and secret of a "Desktop app" client (see docs/tools.md for how to create one), opens Google's consent page in your browser, receives the answer on a
local address (127.0.0.1) and stores the three values (client id, client secret, refresh token) on the server in ~/.config/jaynes/env, readable only by its owner. Nothing is
printed or kept on this machine, and nothing goes through a command line. Only read-only scopes are requested. To revoke access: https://myaccount.google.com/permissions
"""
from __future__ import annotations

import asyncio
import getpass
import json
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bicameral.google_auth import SCOPES, GoogleAuthError, build_auth_url, exchange_code, parse_redirect, pkce_pair   # noqa: E402

HOST = "melville"
WAIT_SECONDS = 300


def receive_code(expected_state: str, auth_url_for):
    """Listen on a free local port for Google's redirect, point the browser at the consent page, and return the authorization code."""
    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                        # noqa: N802
            if self.path.startswith("/?") or self.path.startswith("/oauth"):
                try:
                    result["code"] = parse_redirect(self.path, expected_state)
                    page = "Signed in. You can close this tab and go back to the terminal."
                except GoogleAuthError as error:
                    result["error"] = str(error)
                    page = f"Sign-in failed: {error}"
                self.send_response(200)
                self.send_header("content-type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(page.encode())
                done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):                            # nothing about the request is printed: the address holds the code
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    url = auth_url_for(redirect_uri)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Opening your browser to sign in to Google. If it does not open, copy this address into it:\n\n" + url + "\n")
    webbrowser.open(url)
    if not done.wait(WAIT_SECONDS):
        server.shutdown()
        raise SystemExit("Timed out waiting for the sign-in.")
    server.shutdown()
    if "error" in result:
        raise SystemExit(result["error"])
    result["redirect_uri"] = redirect_uri
    return result["code"], redirect_uri


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    print("Sign-in for read-only Gmail and Drive access. Scopes requested:\n  " + "\n  ".join(SCOPES) + "\n")
    client_id = input("OAuth client id: ").strip()
    client_secret = getpass.getpass("OAuth client secret (not shown): ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Both are needed.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    code, redirect_uri = receive_code(state, lambda uri: build_auth_url(client_id, uri, state, challenge))
    try:
        tokens = asyncio.run(exchange_code(client_id, client_secret, code, redirect_uri, verifier))
    except GoogleAuthError as error:
        raise SystemExit(str(error))
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SystemExit("Google gave no refresh token. Remove the app at https://myaccount.google.com/permissions and run this again.")
    granted = set((tokens.get("scope") or "").split())
    if granted and not set(SCOPES) <= granted:
        raise SystemExit("Not every requested permission was granted: " + ", ".join(sorted(set(SCOPES) - granted)))
    payload = json.dumps({"GOOGLE_CLIENT_ID": client_id, "GOOGLE_CLIENT_SECRET": client_secret, "GOOGLE_REFRESH_TOKEN": refresh})
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "cd ~/jaynes/app && ../.venv/bin/python -m bicameral.envfile"], input=payload, text=True, capture_output=True)
    if done.returncode != 0:
        raise SystemExit("Could not store the values on the server (is the latest version deployed?): " + done.stderr.strip()[:200])
    print(done.stdout.strip() + f" on {host}.\n\nNext: set GMAIL_EXCLUDE_LABELS / GMAIL_EXCLUDE_SENDERS / DRIVE_EXCLUDE_TERMS if you want some mail or files hidden, set ALLOW_PRIVATE_EGRESS=1 once "
          "the Gemini key is on a paid tier, then run scripts/update-jaynes.sh --restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
