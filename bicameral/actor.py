"""The actor: talks to the user. Gemini, streamed."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, List, Optional, Protocol

Message = Dict[str, str]          # {"role": "user" | "assistant", "content": str}


class ActorError(Exception):
    """The actor could not produce a reply."""


class Actor(Protocol):
    def stream(self, messages: List[Message], system: Optional[str] = None) -> AsyncIterator[str]: ...


ACTOR_SYSTEM = (
    "You are the actor in a two-agent chat. You talk with the user; a second agent, the observer, reads each reply you finish and may retract it. "
    "Write clear, honest, helpful replies in plain prose. Some turns in the conversation begin with "
    "'[Internal note from the observer, not from the user]'. Those are not the user speaking: they say your previous reply was retracted and why. "
    "When you see one, write a single corrected reply to the user's last real message, fixing the issues. Do not address the observer or quote the note; "
    "if the correction changes what you told the user, say so plainly."
)


TRANSIENT_CODES = {429, 500, 503, 504}
RETRY_DELAYS = (1.0, 3.0)          # seconds to wait before the second and third attempts


def is_transient(error: Exception) -> bool:
    """A failure that is likely to pass on its own: the provider is overloaded or rate-limiting, not rejecting the request."""
    return getattr(error, "code", None) in TRANSIENT_CODES


class GeminiActor:
    def __init__(self, api_key: str, model: str, retry_delays: tuple = RETRY_DELAYS) -> None:
        from google import genai          # imported here so the tests need no network and no key
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._retry_delays = retry_delays

    def _request(self, messages: List[Message], system: Optional[str] = None):
        from google.genai import types
        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part.from_text(text=m["content"])])
            for m in messages
        ]
        config = types.GenerateContentConfig(system_instruction=system) if system else None
        return contents, config

    async def _chunks(self, contents, config) -> AsyncIterator:
        """One attempt at the streaming call, yielding the raw chunks."""
        async for chunk in await self._client.aio.models.generate_content_stream(model=self._model, contents=contents, config=config):
            yield chunk

    async def stream(self, messages: List[Message], system: Optional[str] = None) -> AsyncIterator[str]:
        """The reply as it is written. A transient failure before any text has arrived is retried after a short wait; once text has been shown it cannot be taken back,
        so a failure after that is raised."""
        contents, config = self._request(messages, system)
        for attempt in range(len(self._retry_delays) + 1):
            started = False
            try:
                async for chunk in self._chunks(contents, config):
                    if chunk.text:
                        started = True
                        yield chunk.text
                return
            except Exception as error:
                if started or attempt >= len(self._retry_delays) or not is_transient(error):
                    raise
                await asyncio.sleep(self._retry_delays[attempt])
