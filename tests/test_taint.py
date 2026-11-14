from pydantic import BaseModel

from bicameral.orchestrator import Session
from bicameral.tools.base import Taint, Tool, ToolCallEvent, ToolRegistry, ToolResult, ToolResultEvent, TurnContext

from .fakes import OK, FakeObserver


class Args(BaseModel):
    text: str = "x"


class Private(Tool):
    name, description, args_model, private = "mail", "reads private mail", Args, True

    async def run(self, args):
        return ToolResult(ok=True, content="private text", summary="read mail")


class PrivateFails(Tool):
    name, description, args_model, private = "mailbroken", "fails", Args, True

    async def run(self, args):
        return ToolResult.failure("nothing read")


class Outgoing(Tool):
    name, description, args_model, outgoing = "web", "goes out", Args, True

    async def run(self, args):
        return ToolResult(ok=True, content="fetched")


def registry():
    return ToolRegistry([Private(), PrivateFails(), Outgoing()])


async def test_outgoing_tools_work_until_private_content_has_been_read_and_are_refused_after():
    reg, ctx = registry(), TurnContext("s", "t1")
    assert (await reg.run("web", {}, ctx, "c1")).ok
    read = await reg.run("mail", {}, ctx, "c2")
    assert read.ok and read.private and ctx.taint.tainted and ctx.taint.tools == ["mail"]
    refused = await reg.run("web", {}, ctx, "c3")
    assert not refused.ok and "private data (mail)" in refused.error and "new conversation" in refused.error


async def test_a_failed_private_call_read_nothing_so_it_taints_nothing():
    reg, ctx = registry(), TurnContext("s", "t1")
    assert not (await reg.run("mailbroken", {}, ctx, "c1")).ok
    assert not ctx.taint.tainted and (await reg.run("web", {}, ctx, "c2")).ok


async def test_the_taint_is_shared_by_every_turn_of_a_conversation_but_not_between_conversations():
    reg, taint = registry(), Taint()
    first = TurnContext("s", "t1", taint=taint)
    await reg.run("mail", {}, first, "c1")
    second = TurnContext("s", "t2", taint=taint)                    # a later turn of the same conversation
    assert not (await reg.run("web", {}, second, "c1")).ok
    other = TurnContext("other", "t1", taint=Taint())               # a new conversation
    assert (await reg.run("web", {}, other, "c1")).ok


async def test_a_call_without_a_turn_context_is_not_blocked_and_marks_nothing():
    assert (await registry().run("web", {})).ok


async def test_the_refusal_is_audited_and_does_not_reach_the_tool(tmp_path):
    from bicameral.tools.audit import AuditLog
    import json
    reg = ToolRegistry([Private(), Outgoing()], audit=AuditLog(str(tmp_path / "a.jsonl")))
    ctx = TurnContext("s", "t1")
    await reg.run("mail", {}, ctx, "c1")
    await reg.run("web", {}, ctx, "c2")
    lines = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert [(l["tool"], l["ok"]) for l in lines] == [("mail", True), ("web", False)] and "private data" in lines[1]["error"]


class MailActor:
    uses_tools = True

    def __init__(self, *scripts):
        self.scripts = list(scripts)

    async def stream(self, messages, system=None, turn=None):
        for item in self.scripts.pop(0):
            yield item


async def test_the_session_keeps_one_taint_for_all_its_turns_and_reports_private_results():
    session = Session("s", MailActor([ToolCallEvent("c1", "mail", {}), ToolResultEvent("c1", "mail", True, "read mail", False, True), "Summary."], ["Next."]), FakeObserver(OK, OK))
    await session.handle_user_message("read my mail")
    await session.handle_user_message("and now?")
    result = [e for e in session.log.replay() if e.type == "tool_result"][0]
    assert result.data["private"] is True
    assert session._turn.taint is session.taint
