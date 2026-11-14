"""One conversation: the turn lock, the streamed draft, the review, and at most one correction."""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from .actor import ACTOR_SYSTEM, Actor, ActorError, Message
from .events import EventLog
from .observer import Observer, ObserverError, Verdict
from .tools.base import ToolCallEvent, ToolResultEvent, TurnContext

logger = logging.getLogger(__name__)

INTERNAL_NOTE_LABEL = "[Internal note from the observer, not from the user]"


def internal_note(verdict: Verdict) -> str:
    issues = "\n".join(f"- {issue}" for issue in verdict.issues) or "- (none listed)"
    return (f"{INTERNAL_NOTE_LABEL}\nYour previous reply was retracted.\nIssues:\n{issues}\n"
            f"Guidance: {verdict.correction_guidance or '(none)'}\nWrite one corrected reply to the user's last message.")


class Session:
    def __init__(self, session_id: str, actor: Actor, observer: Observer, max_tool_calls: int = 5) -> None:
        self.id = session_id
        self.actor = actor
        self.observer = observer
        self.max_tool_calls = max_tool_calls
        self._turn_count = 0
        self._turn: Optional[TurnContext] = None
        self.log = EventLog()
        self.history: List[Message] = []
        self._lock = asyncio.Lock()
        self._message_count = 0

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def _new_message_id(self) -> str:
        self._message_count += 1
        return f"m{self._message_count}"

    async def _stream(self, kind: str, message_id: str, **extra: str) -> str:
        parts: List[str] = []
        turn = self._turn
        options = {"turn": turn} if (turn is not None and getattr(self.actor, "uses_tools", False)) else {}
        async for item in self.actor.stream(list(self.history), system=ACTOR_SYSTEM, **options):
            if isinstance(item, ToolCallEvent):
                self.log.append("tool_call", turn_id=turn.turn_id if turn else "", call_id=item.call_id, tool=item.tool, args=item.args)
            elif isinstance(item, ToolResultEvent):
                self.log.append("tool_result", turn_id=turn.turn_id if turn else "", call_id=item.call_id, tool=item.tool, ok=item.ok, summary=item.summary, truncated=item.truncated)
            elif item:
                parts.append(item)
                self.log.append("token", message_id=message_id, kind=kind, text=item, **extra)
        text = "".join(parts)
        if not text.strip():
            raise ActorError("the actor returned an empty reply")
        return text

    async def handle_user_message(self, text: str) -> None:
        """One turn. Turns are serialised: a second message waits for the first to finish."""
        async with self._lock:
            self._turn_count += 1
            self._turn = TurnContext(self.id, f"t{self._turn_count}", self.max_tool_calls)
            self.history.append({"role": "user", "content": text})
            self.log.append("user_message", text=text)

            draft_id = self._new_message_id()
            try:
                draft = await self._stream("draft", draft_id)
            except Exception as error:
                self.history.pop()                                   # the user turn went unanswered
                _log_failure("actor", error)
                self.log.append("error", where="actor", message=_describe(error))
                return
            self.log.append("draft_complete", message_id=draft_id, text=draft)
            self.history.append({"role": "assistant", "content": draft})

            try:
                trace = list(self._turn.trace) if self._turn else []
                verdict = await (self.observer.review(self.history[:-1], draft, trace=trace) if trace else self.observer.review(self.history[:-1], draft))
            except Exception as error:                               # never treat a failed review as ok
                _log_failure("observer", error)
                self.log.append("error", where="observer", message=_describe(error))
                return
            self.log.append("observer_verdict", message_id=draft_id, **verdict.model_dump())
            if verdict.verdict != "retract":
                return

            self.log.append("retraction", message_id=draft_id, issues=verdict.issues, text=draft)
            self.history.append({"role": "user", "content": internal_note(verdict)})
            correction_id = self._new_message_id()
            try:
                correction = await self._stream("correction", correction_id, corrects=draft_id)
            except Exception as error:
                self.history.pop()                                   # drop the note; the correction never happened
                _log_failure("actor_correction", error)
                self.log.append("error", where="actor_correction", message=_describe(error))
                return
            self.log.append("draft_complete", message_id=correction_id, text=correction)
            self.history.append({"role": "assistant", "content": correction})      # corrections are not reviewed


def _describe(error: Exception) -> str:
    """A message for the user: what kind of failure, with the provider's status code when it gave one, never a key or a request body."""
    if isinstance(error, (ActorError, ObserverError)):
        return str(error)
    code, status = getattr(error, "code", None), getattr(error, "status", None)
    detail = " ".join(str(part) for part in (code, status) if isinstance(part, (int, str)) and part != "")
    return f"{type(error).__name__} {detail}".strip()


def _log_failure(where: str, error: Exception) -> None:
    """For the server's log: the provider's own message, which says why (for example "high demand"). Never a key: the SDKs do not put one in an error."""
    logger.warning("%s failed: %s %s", where, type(error).__name__, str(getattr(error, "message", error))[:300])
