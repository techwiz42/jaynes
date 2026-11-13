"""Fake actor and observer: no network, no keys."""
from __future__ import annotations

from typing import AsyncIterator, Dict, List, Optional

from bicameral.observer import ObserverError, Verdict


class FakeActor:
    """Replies from a script, one entry per call; an entry that is an Exception is raised (after `fail_after` chunks, when it is a (chunks, exception) pair)."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: List[List[Dict[str, str]]] = []
        self.systems: List[Optional[str]] = []

    async def stream(self, messages, system=None) -> AsyncIterator[str]:
        self.calls.append([dict(m) for m in messages])
        self.systems.append(system)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        chunks, error = reply if isinstance(reply, tuple) else (reply, None)
        for chunk in ([chunks] if isinstance(chunks, str) else chunks):
            yield chunk
        if error:
            raise error


class FakeObserver:
    """Verdicts from a script; an entry that is an Exception is raised."""

    def __init__(self, *verdicts) -> None:
        self.verdicts = list(verdicts)
        self.reviews: List[tuple] = []

    async def review(self, transcript, draft) -> Verdict:
        self.reviews.append(([dict(m) for m in transcript], draft))
        verdict = self.verdicts.pop(0)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


OK = Verdict(verdict="ok")
RETRACT = Verdict(verdict="retract", severity="high", issues=["states a wrong date"], correction_guidance="give the right date")
BROKEN = ObserverError("the observer's answer had no JSON object")
