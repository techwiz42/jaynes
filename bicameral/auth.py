"""Passwords, login tokens and the limits on guessing. Standard library only: scrypt for passwords, SHA-256 for stored tokens.

A password is never stored or logged; only a salted scrypt hash. A login token is random, shown to the browser once in an HttpOnly cookie, and stored only as its
SHA-256 hash, so a copy of the database cannot be used to log in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_MAXMEM = 128 * 1024 * 1024
MIN_PASSWORD = 10
MAX_PASSWORD = 200
LOGIN_LIFETIME_SECONDS = 30 * 24 * 3600
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class AuthError(Exception):
    """A request refused for a reason the user may be told."""


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 320 or not EMAIL_PATTERN.match(email):
        raise AuthError("enter a valid email address")
    return email


def check_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(f"the password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"the password must be at most {MAX_PASSWORD} characters")


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(_derive(password, salt)).decode()


def verify_password(password: str, stored: Optional[str]) -> bool:
    """True when the password matches. With no stored hash (an unknown email) a hash is still computed, so the time taken does not say whether the email exists."""
    try:
        scheme, salt_b64, digest_b64 = (stored or "").split("$")
        if scheme != "scrypt":
            raise ValueError
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        _derive(password[:MAX_PASSWORD], b"\0" * 16)
        return False
    return hmac.compare_digest(_derive(password[:MAX_PASSWORD], salt), expected)


def new_token() -> Tuple[str, str]:
    """(the token for the cookie, the hash to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Throttle:
    """At most `limit` events per `window` seconds per key, in memory. Used for failed logins (per email and per address) and for registrations (per address)."""

    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window_seconds, clock
        self._events: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> Deque[float]:
        events, now = self._events[key], self.clock()
        while events and now - events[0] > self.window:
            events.popleft()
        if not events:
            self._events.pop(key, None)
            return deque()
        return events

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.limit

    def add(self, key: str) -> None:
        self._prune(key)
        self._events[key].append(self.clock())

    def clear(self, key: str) -> None:
        self._events.pop(key, None)

    def retry_after(self, key: str) -> int:
        events = self._prune(key)
        return max(1, int(self.window - (self.clock() - events[0]))) if events else 0
