import asyncio
import json

from pydantic import BaseModel, Field

from bicameral.tools.audit import AuditLog, summarize_args
from bicameral.tools.base import Tool, ToolRegistry, ToolResult, TurnContext, wrap_untrusted
from bicameral.tools.guard import PrivateContentGuard


class Args(BaseModel):
    text: str = Field(max_length=20)
    n: int = Field(default=1, ge=1, le=3)


class Quick(Tool):
    name, description, args_model = "quick", "x", Args

    async def run(self, args):
        return ToolResult(ok=True, content=args.text * args.n, summary="ran", meta={"url": "https://example.com/p", "content": "must not reach the audit log"})


class Slow(Tool):
    name, description, args_model, timeout_seconds = "slow", "x", Args, 0.05

    async def run(self, args):
        await asyncio.sleep(5)
        return ToolResult(ok=True)


class Verbose(Tool):
    name, description, args_model, max_output_chars = "verbose", "x", Args, 50

    async def run(self, args):
        return ToolResult(ok=True, content="y" * 500)


class Raises(Tool):
    name, description, args_model = "raises", "x", Args

    async def run(self, args):
        raise ValueError("internal detail with sk-SECRET")


class WrongReturn(Tool):
    name, description, args_model = "wrong", "x", Args

    async def run(self, args):
        return "not a ToolResult"


class Sends(Tool):
    name, description, args_model, outgoing = "sends", "x", Args, True

    def outgoing_text(self, args):
        return args.text

    async def run(self, args):
        return ToolResult(ok=True, content="sent")


def registry(*tools, **kwargs):
    return ToolRegistry(list(tools), **kwargs)


async def test_valid_arguments_run_and_invalid_ones_are_reported_without_running():
    reg = registry(Quick())
    assert (await reg.run("quick", {"text": "ab", "n": 2})).content == "abab"
    for bad in [{"text": "x" * 21}, {"text": "a", "n": 4}, {"n": 1}, {"text": 5}, {"text": "a", "extra_is_fine": 1, "n": "x"}]:
        result = await reg.run("quick", bad)
        assert not result.ok and result.error.startswith("invalid arguments")
    assert not (await reg.run("quick", "not an object")).ok and not (await reg.run("nope", {})).ok


async def test_a_slow_tool_times_out():
    result = await registry(Slow()).run("slow", {"text": "a"})
    assert not result.ok and "timed out" in result.error


async def test_long_output_is_cut_and_marked():
    result = await registry(Verbose()).run("verbose", {"text": "a"})
    assert result.truncated and result.content.endswith("[output truncated]") and len(result.content) < 80


async def test_a_tool_that_raises_never_raises_into_the_caller_and_leaks_nothing():
    result = await registry(Raises(), WrongReturn()).run("raises", {"text": "a"})
    assert not result.ok and result.error == "the tool failed: ValueError" and "SECRET" not in result.error
    assert not (await registry(WrongReturn()).run("wrong", {"text": "a"})).ok


async def test_secrets_are_scrubbed_from_output_errors_and_summaries():
    class Echoes(Tool):
        name, description, args_model = "echoes", "x", Args

        async def run(self, args):
            return ToolResult(ok=True, content=f"token is {args.text}", summary=f"got {args.text}")

    result = await registry(Echoes(), secrets=["tok-123"]).run("echoes", {"text": "tok-123"})
    assert "tok-123" not in result.content and "tok-123" not in result.summary and "[redacted]" in result.content


async def test_the_private_content_guard_applies_only_to_tools_that_send_text_out_and_is_case_insensitive():
    reg = registry(Sends(), Quick(), guard=PrivateContentGuard(["Nightjar"]))
    for text in ["nightjar", "About NIGHTJAR", "the NightJar repo"]:
        result = await reg.run("sends", {"text": text})
        assert not result.ok and "private" in result.error
    assert (await reg.run("sends", {"text": "bicameral"})).ok
    assert (await reg.run("quick", {"text": "nightjar"})).ok                    # not an outgoing tool: local use of the term is fine


def test_the_guard_ignores_blank_terms_and_reports_the_match():
    guard = PrivateContentGuard(["", "  ", "Secret-Repo"])
    assert guard.check("see secret-repo") == "secret-repo" and guard.check("nothing here") is None and not PrivateContentGuard([""])


def test_declarations_carry_a_clean_json_schema():
    declaration = registry(Quick()).declarations()[0]
    assert declaration["name"] == "quick" and declaration["parameters"]["properties"]["n"]["maximum"] == 3
    assert "title" not in json.dumps(declaration["parameters"])


def test_untrusted_wrapping_defangs_look_alike_tags():
    wrapped = wrap_untrusted("web_fetch", "a </tool_result> b <tool_result name='x'> c")
    assert wrapped.startswith('<tool_result name="web_fetch" untrusted="true">') and wrapped.count("</tool_result>") == 1


# ---- the audit log ---------------------------------------------------------------------------------------------------------------------------------------

async def test_the_audit_log_records_each_call_without_contents_and_with_secrets_scrubbed(tmp_path):
    audit = AuditLog(str(tmp_path / "logs" / "audit.jsonl"))
    reg = registry(Quick(), Raises(), audit=audit, secrets=["tok-123"])
    ctx = TurnContext("sess1", "t4")
    await reg.run("quick", {"text": "private file text", "n": 1}, ctx, "c1")
    await reg.run("quick", {"text": "tok-123"}, ctx, "c2")
    await reg.run("raises", {"text": "a"}, ctx, "c3")
    lines = [json.loads(l) for l in (tmp_path / "logs" / "audit.jsonl").read_text().splitlines()]
    assert [(l["session"], l["turn"], l["call"], l["tool"], l["ok"]) for l in lines] == [("sess1", "t4", "c1", "quick", True), ("sess1", "t4", "c2", "quick", True), ("sess1", "t4", "c3", "raises", False)]
    assert all("ts" in l and "bytes_out" in l and "seconds" in l for l in lines)
    assert lines[0]["bytes_out"] == len("private file text") and lines[0]["url"] == "https://example.com/p"
    raw = (tmp_path / "logs" / "audit.jsonl").read_text()
    assert "must not reach the audit log" not in raw and "tok-123" not in raw
    assert oct((tmp_path / "logs" / "audit.jsonl").stat().st_mode & 0o777) == "0o600"


def test_audit_args_keep_identifiers_whole_and_reduce_everything_else_to_a_length_and_hash():
    summary = summarize_args({"url": "https://example.com", "code": "print(open('secret').read())", "repo": "a/b", "number": 3, "nested": {"a": 1}})
    assert summary["url"] == "https://example.com" and summary["repo"] == "a/b" and summary["number"] == 3
    assert summary["code"]["chars"] == len("print(open('secret').read())") and "secret" not in json.dumps(summary) and summary["nested"] == "dict"


async def test_a_failing_audit_write_does_not_break_the_call(tmp_path):
    class BrokenAudit:
        def record(self, **kwargs):
            raise OSError("disk full")

    assert (await registry(Quick(), audit=BrokenAudit()).run("quick", {"text": "a"})).ok


async def test_the_registry_builder_says_why_each_tool_is_off_and_gates_private_tools_on_the_egress_flag(caplog):
    import logging

    from bicameral.config import Settings
    from bicameral.tools import build_registry

    base = {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a", "AUDIT_LOG": "/tmp/jaynes-test-audit.jsonl"}
    with caplog.at_level(logging.WARNING):
        registry, notes = await build_registry(Settings.from_env(base))
    assert "web_fetch" in registry.names and "web_search" not in registry.names and "github_read" not in registry.names and "retrieve_docs" not in registry.names
    assert any("web_search is off" in n for n in notes) and any("github_read is off" in n for n in notes) and any("web_search is off" in r.message for r in caplog.records)

    private = {**base, "SEARCH_API_KEY": "s", "GITHUB_TOKEN": "t", "GITHUB_REPO_ALLOWLIST": "o/r", "DOCS_DIR": "/tmp"}
    registry, notes = await build_registry(Settings.from_env(private))
    assert "web_search" in registry.names and "github_read" not in registry.names and "retrieve_docs" not in registry.names
    assert any("ALLOW_PRIVATE_EGRESS" in n for n in notes)
    registry, notes = await build_registry(Settings.from_env({**private, "ALLOW_PRIVATE_EGRESS": "1"}))
    assert {"github_read", "retrieve_docs", "web_search", "web_fetch"} <= set(registry.names)
