import json

import httpx

from bicameral.tools.base import ToolRegistry
from bicameral.tools.guard import PrivateContentGuard
from bicameral.tools.web_search import WebSearch

KEY = "tvly-secret-key-123"


def registry(handler, guard=None):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    return ToolRegistry([WebSearch(KEY, transport=httpx.MockTransport(wrapped))], guard=guard, secrets=[KEY]), seen


def ok(request):
    return httpx.Response(200, json={"results": [{"title": "T1", "url": "https://a.example/1", "content": "x " * 400}, {"title": "T2", "url": "https://b.example/2", "content": "short"}]})


async def test_results_are_titles_urls_and_truncated_snippets():
    reg, seen = registry(ok)
    result = await reg.run("web_search", {"query": "bicameral", "max_results": 2})
    assert result.ok and "1. T1\n   https://a.example/1" in result.content and "2. T2" in result.content
    assert max(len(line) for line in result.content.split("\n")) <= 320
    body = json.loads(seen[0].content)
    assert body["query"] == "bicameral" and body["max_results"] == 2 and body["include_answer"] is False
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


async def test_the_key_is_never_in_a_result_or_an_error():
    reg, _ = registry(lambda r: httpx.Response(401, text=f"bad key {KEY}"))
    result = await reg.run("web_search", {"query": "q"})
    assert not result.ok and KEY not in result.error and KEY not in result.content and "rejected" in result.error


async def test_quota_and_server_errors_are_readable():
    for status, word in [(429, "limit"), (432, "limit"), (500, "500")]:
        reg, _ = registry(lambda r, s=status: httpx.Response(s, text="x"))
        assert word in (await reg.run("web_search", {"query": "q"})).error


async def test_query_length_and_result_count_are_validated():
    reg, seen = registry(ok)
    assert not (await reg.run("web_search", {"query": "x" * 201})).ok
    assert not (await reg.run("web_search", {"query": "q", "max_results": 9})).ok
    assert not (await reg.run("web_search", {"query": ""})).ok
    assert seen == []


async def test_a_private_term_in_the_query_is_refused_case_insensitively_and_nothing_is_sent():
    reg, seen = registry(ok, guard=PrivateContentGuard(["Nightjar", "owner/secret-repo"]))
    for query in ["how does NIGHTJAR work", "owner/Secret-Repo issues", "the nightjar project"]:
        result = await reg.run("web_search", {"query": query})
        assert not result.ok and "private" in result.error
    assert seen == []
    assert (await reg.run("web_search", {"query": "bicameral legislature"})).ok
