"""What every tool shares: the result type, the registry that runs tools safely, and the per-turn context.

A tool is a class with a name, a description, a Pydantic model for its arguments (which gives both the JSON schema the model sees and the validation), and an async run().
The registry is the only way tools are run. It validates the arguments, refuses text the private-content guard blocks, enforces the timeout and the output size, scrubs
secrets, records an audit line, and never raises: every failure comes back as ToolResult(ok=False, error=...).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Type

from pydantic import BaseModel, ValidationError

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_OUTPUT_CHARS = 20000


@dataclass
class ToolResult:
    ok: bool
    content: str = ""                     # what the model is given (inside the untrusted wrapper)
    error: str = ""
    truncated: bool = False
    private: bool = False                 # set by the registry: a private tool returned content
    summary: str = ""                     # one short line for the page; never the contents
    meta: Dict[str, Any] = field(default_factory=dict)       # for the audit log: target (repo, url, path), counts. Never contents.

    @classmethod
    def failure(cls, error: str, **meta: Any) -> "ToolResult":
        return cls(ok=False, error=error, summary=error[:120], meta=dict(meta))


class Tool:
    name: str = ""
    description: str = ""
    args_model: Type[BaseModel]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    outgoing: bool = False                # True when part of the arguments leaves this machine for a third party (a query, a URL): the private-content guard checks it
    private: bool = False                 # True when results are the user's private content (mail, files, repositories): once one has been read, outgoing tools are refused

    def schema(self) -> Dict[str, Any]:
        return clean_schema(self.args_model.model_json_schema())

    def outgoing_text(self, args: BaseModel) -> str:
        """The text of the arguments that would leave this machine. Only used when `outgoing` is True."""
        return ""

    async def run(self, args: BaseModel) -> ToolResult:
        raise NotImplementedError


def clean_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Pydantic's JSON schema without the `title` noise, for the model."""
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(schema)


# ---- what the actor loop and the orchestrator exchange ---------------------------------------------------------------------------------------------------------

@dataclass
class ToolCallEvent:
    call_id: str
    tool: str
    args: Dict[str, Any]                  # secrets removed


@dataclass
class ToolResultEvent:
    call_id: str
    tool: str
    ok: bool
    summary: str
    truncated: bool
    private: bool = False                 # a private tool returned content: the conversation now holds private data


@dataclass
class TraceItem:
    """One tool call as the observer sees it: the arguments and the full result."""
    call_id: str
    tool: str
    args: Dict[str, Any]
    ok: bool
    output: str                           # the result text, or the error
    truncated: bool


@dataclass
class Taint:
    """Whether a conversation has read private content. Shared by every turn of a session, because what was read stays in the conversation (in the model's replies) and
    could be carried out by text in something read later. Once set, outgoing tools (web search and fetch) are refused until a new conversation starts."""
    tools: List[str] = field(default_factory=list)

    @property
    def tainted(self) -> bool:
        return bool(self.tools)

    def mark(self, tool: str) -> None:
        if tool not in self.tools:
            self.tools.append(tool)


@dataclass
class TurnContext:
    session_id: str
    turn_id: str
    max_calls: int = 5
    calls_used: int = 0
    trace: List[TraceItem] = field(default_factory=list)
    taint: Taint = field(default_factory=Taint)

    def next_call_id(self) -> str:
        self.calls_used += 1
        return f"c{self.calls_used}"


# ---- the registry -----------------------------------------------------------------------------------------------------------------------------------------------

def wrap_untrusted(tool: str, text: str) -> str:
    """A tool result as the model is shown it: marked as data, with any look-alike tag inside the text defanged so it cannot close the wrapper early."""
    safe = text.replace("<tool_result", "&lt;tool_result").replace("</tool_result", "&lt;/tool_result")
    return f'<tool_result name="{tool}" untrusted="true">\n{safe}\n</tool_result>'


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool] = (), guard=None, audit=None, secrets: Sequence[str] = ()) -> None:
        self._tools: Dict[str, Tool] = {t.name: t for t in tools}
        self._guard = guard
        self._audit = audit
        self._secrets = tuple(s for s in secrets if s)
        self._secret_sources: List[Any] = []

    @property
    def names(self) -> List[str]:
        return sorted(self._tools)

    def __bool__(self) -> bool:
        return bool(self._tools)

    def declarations(self) -> List[Dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "parameters": t.schema()} for t in self._tools.values()]

    def add_secret_source(self, source: Any) -> None:
        """Something with a secrets() method, for secrets that change while running (an access token that is renewed)."""
        self._secret_sources.append(source)

    def _all_secrets(self) -> List[str]:
        found = list(self._secrets)
        for source in self._secret_sources:
            found.extend(s for s in source.secrets() if s)
        return found

    def scrub(self, value: Any) -> Any:
        """Replace any known secret that appears in a string, a list or a dict."""
        if isinstance(value, str):
            for secret in self._all_secrets():
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(v) for v in value]
        return value

    async def run(self, name: str, raw_args: Any, ctx: Optional[TurnContext] = None, call_id: str = "") -> ToolResult:
        started = time.monotonic()
        tool = self._tools.get(name)
        args_for_log: Dict[str, Any] = self.scrub(raw_args) if isinstance(raw_args, dict) else {}
        result = await self._run(tool, name, raw_args, ctx)
        if tool is not None and tool.private and result.ok and ctx is not None:
            ctx.taint.mark(name)
            result.private = True
        result.content = self.scrub(result.content)
        result.error = self.scrub(result.error)
        result.summary = self.scrub(result.summary)
        if self._audit is not None:
            try:
                self._audit.record(
                    session_id=ctx.session_id if ctx else "", turn_id=ctx.turn_id if ctx else "", call_id=call_id, tool=name, args=args_for_log, ok=result.ok,
                    error=result.error, truncated=result.truncated, bytes_out=len(result.content.encode("utf-8")), seconds=time.monotonic() - started, meta=self.scrub(result.meta),
                )
            except Exception:                           # the audit log failing must not break a turn, but it must not be silent either
                import logging
                logging.getLogger(__name__).error("audit log write failed for tool %s", name)
        return result

    async def _run(self, tool: Optional[Tool], name: str, raw_args: Any, ctx: Optional[TurnContext] = None) -> ToolResult:
        if tool is None:
            return ToolResult.failure(f"unknown tool: {name[:60]}")
        if not isinstance(raw_args, dict):
            return ToolResult.failure("arguments must be an object")
        try:
            args = tool.args_model.model_validate(raw_args)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ())) or "arguments"
            return ToolResult.failure(f"invalid arguments: {where}: {first.get('msg', 'invalid')}"[:200])
        if tool.outgoing and ctx is not None and ctx.taint.tainted:
            return ToolResult.failure("refused: this conversation has read private data (" + ", ".join(ctx.taint.tools) + "), so outgoing requests are blocked for the rest of it; "
                                      "tell the user they can start a new conversation to use web search or fetch")
        if tool.outgoing and self._guard is not None:
            blocked = self._guard.check(tool.outgoing_text(args))
            if blocked:
                return ToolResult.failure("refused: this would send private content to a third party (it matches a configured private term); rephrase without it")
        try:
            result = await asyncio.wait_for(tool.run(args), timeout=tool.timeout_seconds)
        except asyncio.TimeoutError:
            return ToolResult.failure(f"timed out after {tool.timeout_seconds:g} seconds")
        except Exception as error:                      # a tool bug or an unexpected failure: the kind only, never a message that might hold a secret
            return ToolResult.failure(f"the tool failed: {type(error).__name__}")
        if not isinstance(result, ToolResult):
            return ToolResult.failure("the tool returned an unusable result")
        if len(result.content) > tool.max_output_chars:
            result.content = result.content[: tool.max_output_chars] + "\n[output truncated]"
            result.truncated = True
        return result


def describe_args(args: Dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, default=str)[:300]
