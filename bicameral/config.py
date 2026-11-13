"""Settings, read from the environment. Keys are never hardcoded and never printed."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_ACTOR_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OBSERVER_MODEL = "claude-haiku-5-5"


class ConfigError(Exception):
    """The configuration cannot be used. The message names variables, never values."""


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    anthropic_api_key: str
    actor_model: str = DEFAULT_ACTOR_MODEL
    observer_model: str = DEFAULT_OBSERVER_MODEL
    observer_timeout_seconds: float = 30.0

    def __repr__(self) -> str:
        return (f"Settings(actor_model={self.actor_model!r}, observer_model={self.observer_model!r}, "
                f"gemini_api_key=<set>, anthropic_api_key=<set>)")

    __str__ = __repr__

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if environ is None else environ
        gemini = (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip()
        anthropic = (env.get("ANTHROPIC_API_KEY") or "").strip()
        missing = []
        if not gemini:
            missing.append("GEMINI_API_KEY (or GOOGLE_API_KEY)")
        if not anthropic:
            missing.append("ANTHROPIC_API_KEY")
        if missing:
            raise ConfigError("missing required configuration: " + ", ".join(missing))
        return cls(
            gemini_api_key=gemini,
            anthropic_api_key=anthropic,
            actor_model=(env.get("ACTOR_MODEL") or DEFAULT_ACTOR_MODEL).strip(),
            observer_model=(env.get("OBSERVER_MODEL") or DEFAULT_OBSERVER_MODEL).strip(),
        )
