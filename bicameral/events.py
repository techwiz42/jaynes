"""The append-only event log of one session: replay from an id, and live fan-out to any number of listeners.

Event types (the frontend's discriminated union must match these; change both in the same commit). Every event has `id` (1, 2, 3, ...) and `type`:

  user_message      text
  token             message_id, kind ("draft" | "correction"), text, corrects (correction only: the retracted draft's message_id)
  draft_complete    message_id, text
  observer_verdict  message_id, verdict ("ok" | "retract"), severity, issues, correction_guidance
  retraction        message_id, issues, text (the retracted draft)
  tool_call         turn_id, call_id, tool, args (secrets removed)
  tool_result       turn_id, call_id, tool, ok, summary, truncated (the summary only: never the output, which can be private; the full output goes to the actor and the observer)
  error             where ("actor" | "observer" | "actor_correction"), message
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Set

from pydantic import BaseModel

EventType = Literal["user_message", "token", "draft_complete", "observer_verdict", "retraction", "tool_call", "tool_result", "error"]


class Event(BaseModel):
    id: int
    type: EventType
    data: Dict[str, Any] = {}

    def flat(self) -> Dict[str, Any]:
        """The wire form: one flat object, {"id", "type", ...data}."""
        return {"id": self.id, "type": self.type, **self.data}


class EventLog:
    def __init__(self) -> None:
        self._events: List[Event] = []
        self._listeners: Set[asyncio.Queue] = set()
        self._closed = False

    def append(self, type: EventType, **data: Any) -> Event:
        event = Event(id=len(self._events) + 1, type=type, data=data)
        self._events.append(event)
        for queue in self._listeners:
            queue.put_nowait(event)
        return event

    def replay(self, after: int = 0) -> List[Event]:
        """Every event with an id greater than `after`, in order."""
        return [e for e in self._events if e.id > after]

    def close(self) -> None:
        """No more events: every listener's stream ends."""
        self._closed = True
        for queue in self._listeners:
            queue.put_nowait(None)

    async def stream(self, after: int = 0, keepalive_seconds: Optional[float] = None) -> AsyncIterator[Optional[Event]]:
        """The events after `after`, then live ones, with nothing dropped or repeated between the two (the backlog is taken and the listener registered with no
        await in between). Yields None every `keepalive_seconds` of quiet, when given."""
        queue: asyncio.Queue = asyncio.Queue()
        backlog = self.replay(after)
        if self._closed:
            queue.put_nowait(None)
        self._listeners.add(queue)
        last = after
        try:
            for event in backlog:
                last = event.id
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if event is None:
                    return
                if event.id <= last:
                    continue
                last = event.id
                yield event
        finally:
            self._listeners.discard(queue)
