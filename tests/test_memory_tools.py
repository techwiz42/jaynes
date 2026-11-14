import os
import tempfile

import pytest

from bicameral.store import Store
from bicameral.tools.base import TurnContext, ToolRegistry
from bicameral.tools.memory import MemorySave, MemorySearch


@pytest.fixture
def store():
    return Store(os.path.join(tempfile.mkdtemp(), "jaynes.db"))


def registry(store):
    return ToolRegistry([MemorySearch(store), MemorySave(store)])


def ctx(user_id, conversation_id=""):
    return TurnContext("s", "t1", user_id=user_id, conversation_id=conversation_id)


async def test_search_returns_only_the_logged_in_users_memory(store):
    a, b = store.create_user("a@example.com", "h"), store.create_user("b@example.com", "h")
    store.add_memory(a, "note", "Vault", "The vault code is 4417.")
    store.add_memory(b, "note", "Cat", "My cat is called Pixel.")
    tools = registry(store)
    mine = await tools.run("memory_search", {"query": "vault code"}, ctx(a))
    assert mine.ok and "4417" in mine.content and "[note · Vault" in mine.content
    theirs = await tools.run("memory_search", {"query": "vault code"}, ctx(b))
    assert theirs.ok and "4417" not in theirs.content and "Nothing" in theirs.content


async def test_the_model_cannot_name_a_user_the_tool_has_no_such_argument(store):
    a, b = store.create_user("a@example.com", "h"), store.create_user("b@example.com", "h")
    store.add_memory(a, "note", "Vault", "The vault code is 4417.")
    schema = MemorySearch(store).schema()
    assert set(schema["properties"]) == {"query", "kind", "limit"}
    sneaky = await registry(store).run("memory_search", {"query": "vault", "user_id": a}, ctx(b))          # an extra argument is ignored; b is still b
    assert "4417" not in sneaky.content


async def test_no_login_no_memory(store):
    tools = registry(store)
    assert not (await tools.run("memory_search", {"query": "x"}, ctx(""))).ok
    assert not (await tools.run("memory_search", {"query": "x"}, None)).ok
    assert not (await tools.run("memory_save", {"text": "x"}, ctx(""))).ok


async def test_reading_memory_marks_the_conversation_private_but_a_miss_still_does_too(store):
    a = store.create_user("a@example.com", "h")
    store.add_memory(a, "note", "n", "something to find")
    turn = ctx(a)
    result = await registry(store).run("memory_search", {"query": "something"}, turn)
    assert result.private and turn.taint.tainted and turn.taint.tools == ["memory_search"]


async def test_save_stores_a_note_that_search_then_finds(store):
    a = store.create_user("a@example.com", "h")
    tools = registry(store)
    saved = await tools.run("memory_save", {"text": "The garage code is 9081", "title": "Garage"}, ctx(a))
    assert saved.ok and not saved.private
    found = await tools.run("memory_search", {"query": "garage code", "kind": "note"}, ctx(a))
    assert "9081" in found.content and "Garage" in found.content
    assert store.list_memory(a, "note")[0]["title"] == "Garage"


async def test_the_current_conversation_is_left_out_of_search(store):
    a = store.create_user("a@example.com", "h")
    here, there = store.create_conversation(a), store.create_conversation(a)
    store.add_message(a, here, "user", "the zebra project deadline is Friday")
    store.add_message(a, there, "user", "the zebra project kickoff was Monday")
    result = await registry(store).run("memory_search", {"query": "zebra project"}, ctx(a, here))
    assert "kickoff" in result.content and "deadline" not in result.content


async def test_a_kind_filter_limits_where_it_looks(store):
    a = store.create_user("a@example.com", "h")
    store.add_memory(a, "note", "n", "orchid care notes")
    store.add_memory(a, "document", "d.txt", "orchid catalogue")
    only_docs = await registry(store).run("memory_search", {"query": "orchid", "kind": "document"}, ctx(a))
    assert "catalogue" in only_docs.content and "care notes" not in only_docs.content


async def test_the_registry_builder_offers_memory_only_with_the_egress_flag_and_keeps_shared_tools_off(store):
    from bicameral.config import Settings
    from bicameral.tools import build_registry

    base = {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a", "AUDIT_LOG": "/tmp/jaynes-test-audit3.jsonl", "GITHUB_TOKEN": "t", "GITHUB_REPO_ALLOWLIST": "o/r", "DOCS_DIR": "/tmp",
            "GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s", "GOOGLE_REFRESH_TOKEN": "r"}
    registry_off, notes = await build_registry(Settings.from_env(base), store)
    assert "memory_search" not in registry_off.names and any("memory_search" in n and "ALLOW_PRIVATE_EGRESS" in n for n in notes)
    registry_on, notes = await build_registry(Settings.from_env({**base, "ALLOW_PRIVATE_EGRESS": "1"}), store)
    assert {"memory_search", "memory_save"} <= set(registry_on.names)
    for shared in ("github_read", "gmail_read", "drive_read", "retrieve_docs"):
        assert shared not in registry_on.names                                    # one set of credentials for every user: off
    assert any("SHARED_PRIVATE_TOOLS" in n for n in notes)
    registry_shared, _ = await build_registry(Settings.from_env({**base, "ALLOW_PRIVATE_EGRESS": "1", "SHARED_PRIVATE_TOOLS": "1"}), store)
    assert {"github_read", "gmail_read", "drive_read"} <= set(registry_shared.names)


class SearchingActor:
    """An actor that, like the real one, runs a tool through the registry with the turn it was given, then answers with what came back."""
    uses_tools = True

    def __init__(self, registry, query):
        self.registry, self.query, self.seen = registry, query, []

    async def stream(self, messages, system=None, turn=None):
        result = await self.registry.run("memory_search", {"query": self.query}, turn, "c1")
        self.seen.append(result.content)
        yield "found: " + result.content


async def test_a_session_searches_only_its_own_users_memory_and_becomes_private(store):
    from bicameral.orchestrator import Session
    from .fakes import OK, FakeObserver

    a, b = store.create_user("a@example.com", "h"), store.create_user("b@example.com", "h")
    store.add_memory(a, "note", "Vault", "The vault code is 4417.")
    store.add_memory(b, "note", "Cat", "My cat is called Pixel and the vault code is unknown to me.")
    tools = registry(store)

    actor_a = SearchingActor(tools, "vault code")
    session_a = Session("sa", actor_a, FakeObserver(OK), user_id=a, conversation_id="ca", store=store)
    await session_a.handle_user_message("what is the vault code?")
    actor_b = SearchingActor(tools, "vault code")
    session_b = Session("sb", actor_b, FakeObserver(OK), user_id=b, conversation_id="cb", store=store)
    await session_b.handle_user_message("what is the vault code?")

    assert "4417" in actor_a.seen[0] and "Pixel" not in actor_a.seen[0]
    assert "Pixel" in actor_b.seen[0] and "4417" not in actor_b.seen[0]
    assert session_a.taint.tainted and session_b.taint.tainted                      # reading memory is reading private data in each
    assert "4417" not in str([m["text"] for m in store.conversation_messages(b, "cb") or []])
