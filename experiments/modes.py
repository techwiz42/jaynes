"""The self-critique mode, and the instruments that measure time and tokens in the real actor and observer."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional

from bicameral.actor import GeminiActor, Message
from bicameral.observer import Verdict

# The directive being tested, adapted so one model call can carry it: the private workspace is written inside <workspace> tags and removed before the answer is used.
SELF_CRITIQUE_SYSTEM = """Before giving your final output, use a three-stage 'Bicameral Reasoning' process. Hold your intermediate thoughts in a private workspace; the user never sees it.

1. The Generator (the Actor): draft an initial response to the user's prompt.
2. The Evaluator (the Observer): critique the draft as an independent, highly analytical auditor. Identify logical gaps, biases, factual errors, or surface-level mimicry. Ask yourself: does this response hold an internal consistency, or is it merely predicting the next likely word?
3. The Synthesizer (the Conscious Integration): revise the draft based on your own critique, merging the original intent with the critical feedback.

Write the workspace inside <workspace>...</workspace> tags. Then write only the final answer for the user inside <final>...</final> tags."""

_FINAL = re.compile(r"<final>(.*?)</final>", re.IGNORECASE | re.DOTALL)
_AFTER_WORKSPACE = re.compile(r"</workspace>(.*)$", re.IGNORECASE | re.DOTALL)


def extract_final(text: str) -> tuple[str, bool]:
    """The answer for the user, and whether the reply followed the format (a <final> block). Falls back to the text after the workspace, then to everything."""
    found = _FINAL.findall(text)
    if found:
        return found[-1].strip(), True
    tail = _AFTER_WORKSPACE.search(text)
    if tail and tail.group(1).strip():
        return re.sub(r"</?final>", "", tail.group(1), flags=re.IGNORECASE).strip(), False
    return text.strip(), False


@dataclass
class ActorCall:
    seconds_to_first: float
    seconds: float
    tokens_in: int
    tokens_out: int


@dataclass
class ObserverCall:
    seconds: float
    tokens_in: int
    tokens_out: int


@dataclass
class Meter:
    actor_calls: List[ActorCall] = field(default_factory=list)
    observer_calls: List[ObserverCall] = field(default_factory=list)


class MeteredActor(GeminiActor):
    """The real Gemini actor (with its retries), recording each successful call's time to first text, total time, and token use."""

    def __init__(self, api_key: str, model: str, meter: Meter, system_override: Optional[str] = None, **kwargs) -> None:
        super().__init__(api_key, model, **kwargs)
        self.meter = meter
        self._system_override = system_override

    def _request(self, messages: List[Message], system: Optional[str] = None):
        return super()._request(messages, self._system_override or system)

    async def _chunks(self, contents, config) -> AsyncIterator:
        started = time.monotonic()
        first: Optional[float] = None
        last = None
        async for chunk in super()._chunks(contents, config):
            last = chunk
            if first is None and chunk.text:
                first = time.monotonic() - started
            yield chunk
        usage = getattr(last, "usage_metadata", None)
        tokens_in = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        tokens_out = ((getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)) if usage else 0
        self.meter.actor_calls.append(ActorCall(first if first is not None else 0.0, time.monotonic() - started, tokens_in, tokens_out))


class MeteredClient:
    """Wraps an Anthropic client so each messages.create call is timed and its token use recorded."""

    def __init__(self, client, meter: Meter) -> None:
        self._client, self._meter = client, meter
        self.messages = self

    async def create(self, **kwargs):
        started = time.monotonic()
        response = await self._client.messages.create(**kwargs)
        usage = getattr(response, "usage", None)
        self._meter.observer_calls.append(ObserverCall(time.monotonic() - started, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0))
        return response


class NullObserver:
    """Never objects, and costs nothing: for runs where nothing reviews the answer."""

    async def review(self, transcript, draft) -> Verdict:
        return Verdict(verdict="ok")
