"""What one user, and everyone together, may spend. The service is open to the internet and every turn costs money at two model providers, so there are four ceilings, all set in
the environment (0 turns a ceiling off): messages per hour per user, estimated tokens per day per user and for everybody, and new accounts per day.

Tokens are estimated, not metered: the characters sent to and received from the models, divided by four, counted for the actor's drafts and for each observer review. The estimate
errs toward the generous side of the provider's bill only slightly; the ceilings are a brake, not an invoice. A day is a UTC day.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Limits:
    messages_per_hour: int = 30
    user_tokens_per_day: int = 300_000
    global_tokens_per_day: int = 3_000_000
    new_accounts_per_day: int = 50


def estimate_tokens(chars: int) -> int:
    return -(-max(0, chars) // CHARS_PER_TOKEN)


def utc_day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def seconds_until_utc_midnight(now: float | None = None) -> int:
    moment = time.time() if now is None else now
    return max(1, int(86400 - moment % 86400))
