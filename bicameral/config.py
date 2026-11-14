"""Settings, read from the environment. Keys are never hardcoded and never printed."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

DEFAULT_ACTOR_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OBSERVER_MODEL = "claude-haiku-5-5"


class ConfigError(Exception):
    """The configuration cannot be used. The message names variables, never values."""


def _list(raw: Optional[str]) -> Tuple[str, ...]:
    return tuple(item.strip() for item in (raw or "").split(",") if item.strip())


def _flag(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    anthropic_api_key: str
    actor_model: str = DEFAULT_ACTOR_MODEL
    observer_model: str = DEFAULT_OBSERVER_MODEL
    observer_timeout_seconds: float = 30.0
    # Tools. Each one is off unless what it needs is configured; none of these values is ever printed or logged.
    search_api_key: str = ""                        # SEARCH_API_KEY: web_search (Tavily)
    github_token: str = ""                          # GITHUB_TOKEN: fine-grained, read-only, only the allowlisted repositories
    github_repo_allowlist: Tuple[str, ...] = ()     # GITHUB_REPO_ALLOWLIST: owner/repo,owner/repo
    github_allowed_refs: Tuple[str, ...] = ()       # GITHUB_ALLOWED_REFS: branch names other than the default that may be read
    github_allow_any_ref: bool = False              # GITHUB_ALLOW_ANY_REF=1 also allows commit SHAs and any other ref
    docs_dir: str = ""                              # DOCS_DIR: Markdown and text files for retrieve_docs
    private_terms: Tuple[str, ...] = ()             # PRIVATE_TERMS: never sent to a search provider or in a fetched URL
    max_tool_calls_per_turn: int = 5                # MAX_TOOL_CALLS_PER_TURN
    allow_private_egress: bool = False              # ALLOW_PRIVATE_EGRESS=1: private repository and document content may go to the model providers (see README)
    audit_log: str = "audit.jsonl"                  # AUDIT_LOG: where every tool call is recorded (never the contents)
    google_client_id: str = ""                      # GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN: read-only Gmail and Drive (scripts/google-auth.py sets them up)
    google_client_secret: str = ""
    google_refresh_token: str = ""
    gmail_exclude_labels: Tuple[str, ...] = ()      # GMAIL_EXCLUDE_LABELS: label names whose messages are never shown
    gmail_exclude_senders: Tuple[str, ...] = ()     # GMAIL_EXCLUDE_SENDERS: text in a From address that hides the message
    drive_exclude_terms: Tuple[str, ...] = ()       # DRIVE_EXCLUDE_TERMS: text in a file name that hides the file

    def secrets(self) -> Tuple[str, ...]:
        return tuple(v for v in (self.gemini_api_key, self.anthropic_api_key, self.search_api_key, self.github_token, self.google_client_secret, self.google_refresh_token) if v)

    def __repr__(self) -> str:
        return (f"Settings(actor_model={self.actor_model!r}, observer_model={self.observer_model!r}, keys=<set>, "
                f"github_repos={len(self.github_repo_allowlist)}, docs_dir={bool(self.docs_dir)}, search={bool(self.search_api_key)})")

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
        raw_calls = (env.get("MAX_TOOL_CALLS_PER_TURN") or "").strip() or "5"
        if not raw_calls.isdigit() or not 0 <= int(raw_calls) <= 20:
            raise ConfigError("MAX_TOOL_CALLS_PER_TURN must be a whole number from 0 to 20")
        repos = _list(env.get("GITHUB_REPO_ALLOWLIST"))
        for repo in repos:
            if repo.count("/") != 1 or not all(repo.split("/")):
                raise ConfigError("GITHUB_REPO_ALLOWLIST entries must look like owner/repo")
        return cls(
            gemini_api_key=gemini,
            anthropic_api_key=anthropic,
            actor_model=(env.get("ACTOR_MODEL") or DEFAULT_ACTOR_MODEL).strip(),
            observer_model=(env.get("OBSERVER_MODEL") or DEFAULT_OBSERVER_MODEL).strip(),
            search_api_key=(env.get("SEARCH_API_KEY") or "").strip(),
            github_token=(env.get("GITHUB_TOKEN") or "").strip(),
            github_repo_allowlist=repos,
            github_allowed_refs=_list(env.get("GITHUB_ALLOWED_REFS")),
            github_allow_any_ref=_flag(env.get("GITHUB_ALLOW_ANY_REF")),
            docs_dir=(env.get("DOCS_DIR") or "").strip(),
            private_terms=_list(env.get("PRIVATE_TERMS")),
            max_tool_calls_per_turn=int(raw_calls),
            allow_private_egress=_flag(env.get("ALLOW_PRIVATE_EGRESS")),
            audit_log=(env.get("AUDIT_LOG") or "audit.jsonl").strip(),
            google_client_id=(env.get("GOOGLE_CLIENT_ID") or "").strip(),
            google_client_secret=(env.get("GOOGLE_CLIENT_SECRET") or "").strip(),
            google_refresh_token=(env.get("GOOGLE_REFRESH_TOKEN") or "").strip(),
            gmail_exclude_labels=_list(env.get("GMAIL_EXCLUDE_LABELS")),
            gmail_exclude_senders=_list(env.get("GMAIL_EXCLUDE_SENDERS")),
            drive_exclude_terms=_list(env.get("DRIVE_EXCLUDE_TERMS")),
        )
