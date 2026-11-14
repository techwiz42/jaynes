"""The private-content guard: text that would leave this machine for a search provider or a web server is checked against a list of private terms first."""
from __future__ import annotations

from typing import Iterable, Optional


class PrivateContentGuard:
    def __init__(self, terms: Iterable[str]) -> None:
        self._terms = tuple(sorted({t.strip().lower() for t in terms if t and t.strip()}))

    def __bool__(self) -> bool:
        return bool(self._terms)

    def check(self, text: str) -> Optional[str]:
        """The first private term found in `text` (case-insensitive), or None. The caller refuses the call and must not repeat the term to the user's detriment."""
        lowered = (text or "").lower()
        for term in self._terms:
            if term in lowered:
                return term
        return None
