"""The actor's tools, and how they are chosen. build_registry() registers a tool only when everything it needs is configured and, for the ones that read private content,
when the operator has confirmed that content may go to the model providers."""
from __future__ import annotations

import logging
from typing import List, Tuple

from ..config import Settings
from .audit import AuditLog
from .base import ToolRegistry
from .guard import PrivateContentGuard

logger = logging.getLogger(__name__)


async def build_registry(settings: Settings) -> Tuple[ToolRegistry, List[str]]:
    """The registry, and a line for each tool left out and why (also logged, never containing a value)."""
    from .docs import DocIndex, RetrieveDocs
    from .github import GitHubRead
    from .sandbox import PythonSandbox, Sandbox, self_test
    from .web_fetch import WebFetch
    from .web_search import WebSearch

    tools, notes = [], []
    google = None
    tools.append(WebFetch())

    if settings.search_api_key:
        tools.append(WebSearch(settings.search_api_key))
    else:
        notes.append("web_search is off: SEARCH_API_KEY is not set")

    problem = await self_test(Sandbox())
    if problem is None:
        tools.append(PythonSandbox())
    else:
        notes.append(f"python_sandbox is off: {problem}")

    private_ok = settings.allow_private_egress
    if settings.docs_dir:
        if private_ok:
            index = DocIndex(settings.docs_dir)
            tools.append(RetrieveDocs(index))
            if not len(index):
                notes.append("retrieve_docs is on but DOCS_DIR has no Markdown or text files")
        else:
            notes.append("retrieve_docs is off: DOCS_DIR is set but ALLOW_PRIVATE_EGRESS is not 1 (see the README on sending private content to the model providers)")
    else:
        notes.append("retrieve_docs is off: DOCS_DIR is not set")

    if settings.github_token and settings.github_repo_allowlist:
        if private_ok:
            tools.append(GitHubRead(settings.github_token, settings.github_repo_allowlist, settings.github_allowed_refs, settings.github_allow_any_ref))
        else:
            notes.append("github_read is off: ALLOW_PRIVATE_EGRESS is not 1 (see the README on sending private content to the model providers)")
    else:
        notes.append("github_read is off: GITHUB_TOKEN or GITHUB_REPO_ALLOWLIST is not set")

    if settings.google_client_id and settings.google_client_secret and settings.google_refresh_token:
        if private_ok:
            from ..google_auth import GoogleAuth
            from .drive import DriveRead
            from .gmail import GmailRead
            google = GoogleAuth(settings.google_client_id, settings.google_client_secret, settings.google_refresh_token)
            tools.append(GmailRead(google, settings.gmail_exclude_labels, settings.gmail_exclude_senders))
            tools.append(DriveRead(google, settings.drive_exclude_terms))
        else:
            notes.append("gmail_read and drive_read are off: ALLOW_PRIVATE_EGRESS is not 1 (see the README on sending private content to the model providers)")
    else:
        notes.append("gmail_read and drive_read are off: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET or GOOGLE_REFRESH_TOKEN is not set (run scripts/google-auth.py)")

    # Names of private things, which must never go to a search provider or into a URL: what the operator listed, and the allowlisted repositories.
    terms = list(settings.private_terms) + list(settings.github_repo_allowlist)
    registry = ToolRegistry(tools, guard=PrivateContentGuard(terms), audit=AuditLog(settings.audit_log), secrets=settings.secrets())
    if google is not None:
        registry.add_secret_source(google)                  # the access token changes as it is renewed
    for note in notes:
        logger.warning(note)                # warning level: the server's logger shows nothing quieter, and a tool being off is something to see at start-up
    return registry, notes
