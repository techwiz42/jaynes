"""Google sign-in for the read-only Gmail and Drive tools.

A one-time consent (scripts/google-auth.py) gives a refresh token; this module turns it into short-lived access tokens as they are needed. Nothing here is ever printed, logged or
returned in a result: GoogleAuth.secrets() lists what the tool registry must scrub.

Scopes are read-only: gmail.readonly (cannot send, delete or change mail) and drive.readonly (cannot create, change or delete files).
"""
from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
import time
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/drive.readonly")


class GoogleAuthError(Exception):
    """Authorization failed. The message is safe to show: it never holds a token."""


def pkce_pair() -> Tuple[str, str]:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = base64.urlsafe_b64encode(_secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def parse_redirect(path: str, expected_state: str) -> str:
    """The authorization code from the address Google redirected the browser to (a request path with a query string). Refuses a wrong state, and an error from Google."""
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(path).query)
    if "error" in query:
        raise GoogleAuthError(f"Google reported: {query['error'][0][:80]}")
    if query.get("state", [""])[0] != expected_state:
        raise GoogleAuthError("the sign-in response did not match this request (wrong state)")
    code = query.get("code", [""])[0]
    if not code:
        raise GoogleAuthError("the sign-in response had no code")
    return code


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes: Sequence[str] = SCOPES) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",                 # offline + consent: Google returns a refresh token every time
    })


async def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, verifier: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> Dict[str, str]:
    async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
        response = await client.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri,
                                                      "grant_type": "authorization_code", "code_verifier": verifier})
    if response.status_code != 200:
        raise GoogleAuthError(f"Google did not accept the sign-in code ({response.status_code})")
    return response.json()


class GoogleAuth:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, transport: Optional[httpx.AsyncBaseTransport] = None, clock=time.monotonic) -> None:
        self._client_id, self._client_secret, self._refresh_token = client_id, client_secret, refresh_token
        self._transport = transport
        self._clock = clock
        self._access: str = ""
        self._expires_at = 0.0

    def secrets(self) -> List[str]:
        return [s for s in (self._client_secret, self._refresh_token, self._access) if s]

    async def access_token(self) -> str:
        if self._access and self._clock() < self._expires_at - 60:
            return self._access
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=15.0) as client:
                response = await client.post(TOKEN_URL, data={"client_id": self._client_id, "client_secret": self._client_secret, "refresh_token": self._refresh_token,
                                                              "grant_type": "refresh_token"})
        except httpx.HTTPError as error:
            raise GoogleAuthError(f"Google could not be reached to renew access ({type(error).__name__})") from None
        if response.status_code in (400, 401):
            raise GoogleAuthError("Google no longer accepts the saved authorization (it was revoked or has expired); it has to be set up again with scripts/google-auth.py")
        if response.status_code != 200:
            raise GoogleAuthError(f"Google answered {response.status_code} when renewing access")
        data = response.json()
        self._access = str(data.get("access_token", ""))
        self._expires_at = self._clock() + float(data.get("expires_in", 3600))
        if not self._access:
            raise GoogleAuthError("Google gave no access token")
        return self._access

    async def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {await self.access_token()}", "User-Agent": "jaynes/0.1"}
