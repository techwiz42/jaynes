"""The actor: talks to the user. Gemini, streamed."""
from __future__ import annotations

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


class GeminiActor:
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai          # imported here so the tests need no network and no key
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def stream(self, messages: List[Message], system: Optional[str] = None) -> AsyncIterator[str]:
        from google.genai import types
        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part.from_text(text=m["content"])])
            for m in messages
        ]
        config = types.GenerateContentConfig(system_instruction=system) if system else None
        async for chunk in await self._client.aio.models.generate_content_stream(model=self._model, contents=contents, config=config):
            if chunk.text:
                yield chunk.text
