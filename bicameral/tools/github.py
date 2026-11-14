"""github_read: read-only access to the repositories on an allowlist, with a fine-grained token.

  - A repository not on the allowlist is refused before any request is made.
  - Only GET requests are ever sent, and there is no operation that writes. (Give the token read-only permissions on those repositories anyway: that is the real limit.)
  - Only the default branch, and the branches named in GITHUB_ALLOWED_REFS, can be read; a commit SHA or any other ref needs GITHUB_ALLOW_ANY_REF=1.
  - The token is only ever in the Authorization header of the request: never in a result, an error, an event or the audit log.
  - Everything returned is capped, and marked when cut.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, Literal, Optional, Sequence, Tuple
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult

API = "https://api.github.com"
MAX_FILE_BYTES = 1_000_000
MAX_LISTING = 200
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_QUALIFIER = re.compile(r"(?<![\w-])(repo|org|user|owner|in|path|filename|extension|language|size|fork|is):", re.IGNORECASE)


class GitHubArgs(BaseModel):
    operation: Literal["list_files", "read_file", "search_code", "list_commits", "read_issue", "list_issues"] = Field(
        description="list_files: a directory listing. read_file: a file's text. search_code: find code. list_commits: recent commits. read_issue: one issue. list_issues: issues.")
    repo: str = Field(max_length=140, description="The repository as owner/repo. Only allowlisted repositories can be read.")
    path: str = Field(default="", max_length=300, description="A file or directory path inside the repository (list_files, read_file, list_commits).")
    ref: str = Field(default="", max_length=100, description="A branch name. Leave empty for the default branch.")
    query: str = Field(default="", max_length=200, description="What to search for (search_code).")
    number: int = Field(default=0, ge=0, le=10_000_000, description="The issue number (read_issue).")
    state: Literal["open", "closed", "all"] = Field(default="open", description="Which issues to list (list_issues).")
    max_results: int = Field(default=10, ge=1, le=30, description="The most items to return (search_code, list_commits, list_issues).")
    max_chars: int = Field(default=8000, ge=500, le=20000, description="The most characters of text to return (read_file, read_issue).")
    start_line: int = Field(default=0, ge=0, description="First line to return, counting from 1 (read_file). 0 means from the start.")
    end_line: int = Field(default=0, ge=0, description="Last line to return (read_file). 0 means to the end.")


class GitHubRead(Tool):
    name = "github_read"
    description = ("Read from the user's private GitHub repositories (read-only): list a directory, read a file, search code, list commits, read or list issues. "
                   "Only repositories on the configured allowlist work. When you rely on a file, cite its repository and path.")
    args_model = GitHubArgs
    timeout_seconds = 20.0
    max_output_chars = 20000

    def __init__(self, token: str, allowlist: Sequence[str], allowed_refs: Sequence[str] = (), allow_any_ref: bool = False,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._token = token
        self._allow = {r.strip().lower(): r.strip() for r in allowlist}
        self._refs = tuple(allowed_refs)
        self._any_ref = allow_any_ref
        self._transport = transport

    @property
    def allowlist(self) -> Tuple[str, ...]:
        return tuple(self._allow.values())

    # ---- checks made before any request ----------------------------------------------------------------------------------------------------------------

    def _repo(self, repo: str) -> Optional[str]:
        return self._allow.get(repo.strip().lower())

    def _check_ref(self, ref: str) -> Optional[str]:
        if not ref:
            return None
        if not _REF.match(ref):
            return "that ref is not valid"
        if self._any_ref or ref in self._refs:
            return None
        if _SHA.match(ref):
            return "reading a commit SHA is not allowed; use a branch name that is on the allowlist, or the default branch"
        return "that branch is not on the allowlist; only the default branch and the configured branches can be read"

    @staticmethod
    def _check_path(path: str) -> Optional[str]:
        if path and (path.startswith("/") or ".." in path.split("/") or "\\" in path or "\x00" in path):
            return "that path is not valid"
        return None

    # ---- requests ---------------------------------------------------------------------------------------------------------------------------------------

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None, accept: str = "application/vnd.github+json"):
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "jaynes/0.1", "Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient(transport=self._transport, base_url=API, timeout=15.0) as client:
            return await client.get(path, params=params, headers=headers)

    @staticmethod
    def _problem(response: httpx.Response) -> str:
        status = response.status_code
        if status == 401:
            return "GitHub rejected the token"
        if status == 403:
            return "GitHub's rate limit was reached; try again later" if response.headers.get("x-ratelimit-remaining") == "0" else "the token does not have access to that"
        if status == 404:
            return "not found (or the token has no access to it)"
        if status == 422:
            return "GitHub could not run that query"
        return f"GitHub answered {status}"

    # ---- the operations ---------------------------------------------------------------------------------------------------------------------------------

    async def run(self, args: GitHubArgs) -> ToolResult:
        repo = self._repo(args.repo)
        if repo is None:                                  # before anything is sent
            return ToolResult.failure("refused: that repository is not on the allowlist", repo=args.repo[:140])
        for problem in (self._check_ref(args.ref), self._check_path(args.path)):
            if problem:
                return ToolResult.failure(f"refused: {problem}", repo=repo)
        meta: Dict[str, Any] = {"repo": repo, "operation": args.operation}
        if args.path:
            meta["path"] = args.path
        if args.ref:
            meta["ref"] = args.ref
        try:
            handler = getattr(self, f"_op_{args.operation}")
            result: ToolResult = await handler(repo, args)
        except httpx.TimeoutException:
            return ToolResult.failure("GitHub did not answer in time", **meta)
        except httpx.HTTPError as error:
            return ToolResult.failure(f"GitHub could not be reached ({type(error).__name__})", **meta)
        result.meta = {**meta, **result.meta}
        return result

    def _contents_path(self, repo: str, path: str) -> str:
        return f"/repos/{repo}/contents/{quote(path.strip('/'), safe='/')}".rstrip("/")

    async def _op_list_files(self, repo: str, args: GitHubArgs) -> ToolResult:
        response = await self._get(self._contents_path(repo, args.path), {"ref": args.ref} if args.ref else None)
        if response.status_code != 200:
            return ToolResult.failure(self._problem(response))
        data = response.json()
        if isinstance(data, dict):
            return ToolResult.failure("that path is a file; use read_file")
        entries = sorted(data, key=lambda e: (e.get("type") != "dir", e.get("name", "")))
        lines = [f"{'dir ' if e.get('type') == 'dir' else 'file'} {e.get('name', '')}{'/' if e.get('type') == 'dir' else ''}" + (f" ({e['size']} bytes)" if e.get("type") == "file" else "")
                 for e in entries[:MAX_LISTING]]
        cut = len(entries) > MAX_LISTING
        return ToolResult(ok=True, content="\n".join(lines) + ("\n[listing truncated]" if cut else ""), truncated=cut, summary=f"{len(entries)} entries in {repo}/{args.path or ''}".rstrip("/"),
                          meta={"entries": len(entries)})

    async def _op_read_file(self, repo: str, args: GitHubArgs) -> ToolResult:
        if not args.path:
            return ToolResult.failure("read_file needs a path")
        response = await self._get(self._contents_path(repo, args.path), {"ref": args.ref} if args.ref else None)
        if response.status_code != 200:
            return ToolResult.failure(self._problem(response))
        data = response.json()
        if isinstance(data, list):
            return ToolResult.failure("that path is a directory; use list_files")
        if data.get("type") != "file":
            return ToolResult.failure(f"that is a {data.get('type', 'non-file')}, not a file")
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str) or int(data.get("size", 0)) > MAX_FILE_BYTES:
            return ToolResult.failure("that file is too large to read through this tool")
        raw = base64.b64decode(data["content"])
        if b"\x00" in raw[:8000]:
            return ToolResult.failure("that file is binary")
        lines = raw.decode("utf-8", errors="replace").split("\n")
        total = len(lines)
        first, last = max(args.start_line, 1), (args.end_line or total)
        selected = lines[first - 1:last] if (args.start_line or args.end_line) else lines
        text = "\n".join(selected)
        label = f"lines {first}-{min(last, total)} of {total}" if (args.start_line or args.end_line) else f"{total} lines"
        cut = len(text) > args.max_chars
        return ToolResult(ok=True, content=f"[{repo}/{args.path} — {label}]\n" + text[: args.max_chars] + ("\n[file truncated]" if cut else ""), truncated=cut,
                          summary=f"read {args.path} ({label})", meta={"chars": min(len(text), args.max_chars), "lines": total})

    async def _op_search_code(self, repo: str, args: GitHubArgs) -> ToolResult:
        if not args.query.strip():
            return ToolResult.failure("search_code needs a query")
        if _QUALIFIER.search(args.query):
            return ToolResult.failure("refused: search qualifiers such as repo: or path: are not allowed; the search is already limited to this repository")
        response = await self._get("/search/code", {"q": f"{args.query} repo:{repo}", "per_page": args.max_results})
        if response.status_code != 200:
            return ToolResult.failure(self._problem(response))
        items = response.json().get("items", [])[: args.max_results]
        lines = [f"{item.get('path', '')}" for item in items]
        return ToolResult(ok=True, content="\n".join(lines) or "(no matches; code search only covers the default branch)", summary=f"{len(lines)} files match",
                          meta={"matches": len(lines)})

    async def _op_list_commits(self, repo: str, args: GitHubArgs) -> ToolResult:
        params: Dict[str, Any] = {"per_page": args.max_results}
        if args.path:
            params["path"] = args.path
        if args.ref:
            params["sha"] = args.ref
        response = await self._get(f"/repos/{repo}/commits", params)
        if response.status_code != 200:
            return ToolResult.failure(self._problem(response))
        lines = []
        for item in response.json()[: args.max_results]:
            commit = item.get("commit", {})
            message = (commit.get("message") or "").split("\n")[0][:120]
            lines.append(f"{str(item.get('sha', ''))[:7]} {str(commit.get('author', {}).get('date', ''))[:10]} {commit.get('author', {}).get('name', '')}: {message}")
        return ToolResult(ok=True, content="\n".join(lines) or "(no commits)", summary=f"{len(lines)} commits", meta={"commits": len(lines)})

    async def _op_read_issue(self, repo: str, args: GitHubArgs) -> ToolResult:
        if not args.number:
            return ToolResult.failure("read_issue needs an issue number")
        response = await self._get(f"/repos/{repo}/issues/{args.number}")
        if response.status_code != 200:
            return ToolResult.failure(self._problem(response))
        issue = response.json()
        body = issue.get("body") or ""
        cut = len(body) > args.max_chars
        labels = ", ".join(label.get("name", "") for label in issue.get("labels", []))
        head = (f"#{issue.get('number')} {issue.get('title', '')}\nstate: {issue.get('state')}{' (pull request)' if issue.get('pull_request') else ''}"
                f"\nauthor: {(issue.get('user') or {}).get('login', '')}\nlabels: {labels or '-'}\ncomments: {issue.get('comments', 0)}\n\n")
        return ToolResult(ok=True, content=head + body[: args.max_chars] + ("\n[body truncated]" if cut else ""), truncated=cut, summary=f"issue #{args.number}: {str(issue.get('title', ''))[:60]}",
                          meta={"number": args.number, "chars": min(len(body), args.max_chars)})

    async def _op_list_issues(self, repo: str, args: GitHubArgs) -> ToolResult:
        response = await self._get(f"/repos/{repo}/issues", {"state": args.state, "per_page": args.max_results})
        if response.status_code != 200:
            return ToolResult.failure(self._problem(response))
        lines = []
        for issue in response.json()[: args.max_results]:
            labels = ", ".join(label.get("name", "") for label in issue.get("labels", []))
            lines.append(f"#{issue.get('number')} [{issue.get('state')}]{' (PR)' if issue.get('pull_request') else ''} {str(issue.get('title', ''))[:120]}" + (f" ({labels})" if labels else ""))
        return ToolResult(ok=True, content="\n".join(lines) or "(no issues)", summary=f"{len(lines)} {args.state} issues", meta={"issues": len(lines)})
