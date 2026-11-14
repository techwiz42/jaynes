import base64
import json

import httpx
import pytest

from bicameral.tools.audit import AuditLog
from bicameral.tools.base import ToolRegistry
from bicameral.tools.github import GitHubRead

TOKEN = "github_pat_FAKE_TOKEN_FOR_TESTS_123"
REPO = "acme/private-app"


def make(routes=None, **kwargs):
    """A registry with a GitHub tool whose transport answers from `routes` (a function of the request) and records every request."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return routes(request) if routes else httpx.Response(200, json=[])

    tool = GitHubRead(TOKEN, kwargs.pop("allowlist", [REPO, "acme/other"]), transport=httpx.MockTransport(handler), **kwargs)
    return ToolRegistry([tool], secrets=[TOKEN]), seen


def file_response(text: str, name="a.py", size=None):
    raw = text.encode()
    return httpx.Response(200, json={"type": "file", "name": name, "encoding": "base64", "content": base64.b64encode(raw).decode(), "size": size if size is not None else len(raw)})


# ---- the allowlist, before any request -------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("repo", ["someone/else", "acme/private-app-2", "acme", "../etc", "", "acme/private-app/extra", "https://github.com/acme/private-app"])
async def test_a_repository_not_on_the_allowlist_is_refused_without_any_request(repo):
    reg, seen = make()
    result = await reg.run("github_read", {"operation": "list_files", "repo": repo})
    assert not result.ok and seen == []
    assert "allowlist" in result.error or "invalid arguments" in result.error


async def test_the_allowlist_ignores_case_and_every_operation_is_checked():
    reg, seen = make()
    for op, extra in [("list_files", {}), ("read_file", {"path": "x"}), ("search_code", {"query": "q"}), ("list_commits", {}), ("read_issue", {"number": 1}), ("list_issues", {})]:
        denied = await reg.run("github_read", {"operation": op, "repo": "evil/repo", **extra})
        assert not denied.ok and "allowlist" in denied.error
    assert seen == []
    allowed = await reg.run("github_read", {"operation": "list_files", "repo": "ACME/Private-App"})
    assert allowed.ok and len(seen) == 1 and seen[0].url.path == f"/repos/{REPO}/contents"


# ---- reads only ---------------------------------------------------------------------------------------------------------------------------------------------------

async def test_only_get_requests_are_ever_sent_and_there_is_no_write_operation():
    reg, seen = make(lambda r: httpx.Response(200, json=[] if "contents" not in r.url.path else {"type": "dir"}))
    for op, extra in [("list_files", {}), ("list_commits", {}), ("list_issues", {}), ("search_code", {"query": "q"})]:
        await reg.run("github_read", {"operation": op, "repo": REPO, **extra})
    assert seen and {r.method for r in seen} == {"GET"}
    tool = GitHubRead(TOKEN, [REPO])
    operations = set(tool.schema()["properties"]["operation"]["enum"])
    assert operations == {"list_files", "read_file", "search_code", "list_commits", "read_issue", "list_issues"}
    assert not (await reg.run("github_read", {"operation": "create_issue", "repo": REPO})).ok


# ---- refs and paths ------------------------------------------------------------------------------------------------------------------------------------------------

async def test_only_the_default_branch_and_configured_branches_can_be_read():
    reg, seen = make(lambda r: file_response("x"), allowed_refs=["release"])
    assert (await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "a.py"})).ok
    assert dict(seen[0].url.params) == {}                                                       # no ref: the default branch
    assert (await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "a.py", "ref": "release"})).ok
    n = len(seen)
    for ref in ["feature/x", "0123456789abcdef0123456789abcdef01234567", "abc1234", "bad ref!"]:
        result = await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "a.py", "ref": ref})
        assert not result.ok and result.error.startswith("refused")
    assert len(seen) == n


async def test_any_ref_can_be_allowed_by_config():
    reg, seen = make(lambda r: file_response("x"), allow_any_ref=True)
    assert (await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "a.py", "ref": "0123456789abcdef0123456789abcdef01234567"})).ok


@pytest.mark.parametrize("path", ["../secrets", "a/../../b", "/etc/passwd", "a\\b"])
async def test_paths_that_climb_out_are_refused(path):
    reg, seen = make()
    result = await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": path})
    assert not result.ok and seen == []


# ---- the operations ---------------------------------------------------------------------------------------------------------------------------------------------

async def test_read_file_returns_text_with_a_line_range_and_truncation_marks():
    text = "\n".join(f"line {i}" for i in range(1, 101))
    reg, _ = make(lambda r: file_response(text))
    ranged = await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "a.py", "start_line": 10, "end_line": 12})
    assert ranged.ok and "lines 10-12 of 100" in ranged.content and "line 10\nline 11\nline 12" in ranged.content and "line 13" not in ranged.content
    big = "x" * 50000
    reg, _ = make(lambda r: file_response(big))
    cut = await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "a.py", "max_chars": 1000})
    assert cut.truncated and "[file truncated]" in cut.content and len(cut.content) < 1200


async def test_binary_and_oversized_files_and_wrong_kinds_are_refused():
    reg, _ = make(lambda r: file_response("PK\x00\x03binary"))
    assert "binary" in (await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "x.zip"})).error
    reg, _ = make(lambda r: file_response("x", size=5_000_000))
    assert "too large" in (await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "big"})).error
    reg, _ = make(lambda r: httpx.Response(200, json=[{"name": "a"}]))
    assert "directory" in (await reg.run("github_read", {"operation": "read_file", "repo": REPO, "path": "src"})).error
    reg, _ = make(lambda r: file_response("x"))
    assert "is a file" in (await reg.run("github_read", {"operation": "list_files", "repo": REPO, "path": "a.py"})).error


async def test_list_files_sorts_directories_first():
    reg, _ = make(lambda r: httpx.Response(200, json=[{"name": "b.py", "type": "file", "size": 5}, {"name": "src", "type": "dir"}]))
    result = await reg.run("github_read", {"operation": "list_files", "repo": REPO})
    assert result.content.split("\n") == ["dir  src/", "file b.py (5 bytes)"]


async def test_search_code_is_limited_to_the_repository_and_rejects_qualifiers():
    reg, seen = make(lambda r: httpx.Response(200, json={"items": [{"path": "src/a.py"}, {"path": "src/b.py"}]}))
    ok = await reg.run("github_read", {"operation": "search_code", "repo": REPO, "query": "def main"})
    assert ok.content == "src/a.py\nsrc/b.py" and seen[0].url.params["q"] == f"def main repo:{REPO}"
    n = len(seen)
    for query in ["x repo:other/secret", "org:acme token", "x path:secrets", "user:someone x"]:
        assert not (await reg.run("github_read", {"operation": "search_code", "repo": REPO, "query": query})).ok
    assert len(seen) == n


async def test_commits_and_issues():
    commits = [{"sha": "abcdef1234567", "commit": {"message": "Fix bug\n\nlong body", "author": {"name": "Ann", "date": "2026-11-01T10:00:00Z"}}}]
    reg, _ = make(lambda r: httpx.Response(200, json=commits))
    assert (await reg.run("github_read", {"operation": "list_commits", "repo": REPO})).content == "abcdef1 2026-11-01 Ann: Fix bug"
    issue = {"number": 7, "title": "Crash", "state": "open", "user": {"login": "ann"}, "labels": [{"name": "bug"}], "comments": 2, "body": "It crashes. " * 100}
    reg, seen = make(lambda r: httpx.Response(200, json=issue))
    result = await reg.run("github_read", {"operation": "read_issue", "repo": REPO, "number": 7, "max_chars": 500})
    assert "#7 Crash" in result.content and "labels: bug" in result.content and result.truncated and seen[0].url.path == f"/repos/{REPO}/issues/7"
    reg, _ = make(lambda r: httpx.Response(200, json=[{"number": 1, "state": "open", "title": "A", "labels": []}, {"number": 2, "state": "open", "title": "B", "labels": [], "pull_request": {"url": "https://api.github.com/x"}}]))
    assert (await reg.run("github_read", {"operation": "list_issues", "repo": REPO})).content == "#1 [open] A\n#2 [open] (PR) B"


async def test_missing_arguments_for_an_operation_are_reported():
    reg, seen = make()
    assert "path" in (await reg.run("github_read", {"operation": "read_file", "repo": REPO})).error
    assert "query" in (await reg.run("github_read", {"operation": "search_code", "repo": REPO})).error
    assert "number" in (await reg.run("github_read", {"operation": "read_issue", "repo": REPO})).error
    assert seen == []


@pytest.mark.parametrize("status,header,words", [(401, {}, "rejected the token"), (403, {"x-ratelimit-remaining": "0"}, "rate limit"), (403, {}, "does not have access"), (404, {}, "not found"), (500, {}, "500")])
async def test_github_errors_are_readable(status, header, words):
    reg, _ = make(lambda r: httpx.Response(status, headers=header, json={"message": f"echo {TOKEN}"}))
    result = await reg.run("github_read", {"operation": "list_files", "repo": REPO})
    assert not result.ok and words in result.error and TOKEN not in result.error


# ---- the token ------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_token_is_only_in_the_authorization_header_and_never_in_any_output(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"name": f"leak-{TOKEN}", "type": "file", "size": 1}])           # even a server that echoes the token cannot put it in a result

    tool = GitHubRead(TOKEN, [REPO], transport=httpx.MockTransport(handler))
    reg = ToolRegistry([tool], audit=audit, secrets=[TOKEN])
    result = await reg.run("github_read", {"operation": "list_files", "repo": REPO, "path": "src"})
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in result.content and TOKEN not in result.summary and TOKEN not in json.dumps(result.meta) and "[redacted]" in result.content
    assert TOKEN not in (tmp_path / "audit.jsonl").read_text()
    assert TOKEN not in repr(tool.__dict__.get("_allow"))
