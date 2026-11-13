"""The observer: reads each finished reply and may retract it. Claude Haiku. It never fails open: an unusable answer is an ObserverError, not an "ok"."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Dict, List, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

Message = Dict[str, str]


class ObserverError(Exception):
    """The review could not be done or its answer could not be understood."""


class Verdict(BaseModel):
    verdict: Literal["ok", "retract"]
    severity: Literal["none", "low", "medium", "high"] = "none"
    issues: List[str] = Field(default_factory=list)
    correction_guidance: str = ""


class Observer(Protocol):
    async def review(self, transcript: List[Message], draft: str) -> Verdict: ...


OBSERVER_SYSTEM = (
    "You are the observer in a two-agent chat. You see the conversation so far and the actor's newest reply (the draft), which the user is already reading. "
    "Decide whether the draft should be retracted. Retract only for a real problem: a factual error, advice that could cause harm, ignoring or misreading what the user asked, "
    "contradicting something said earlier, or invented specifics presented as fact. Do not retract for style, length or tone. "
    "Turns marked '[Internal note ...]' are from an earlier review, not from the user.\n\n"
    "Answer with one JSON object and nothing else:\n"
    '{"verdict": "ok" or "retract", "severity": "none" | "low" | "medium" | "high", "issues": ["short, specific problems"], '
    '"correction_guidance": "what the corrected reply must do; empty when ok"}'
)


def render_transcript(transcript: List[Message], draft: str) -> str:
    lines = [f"{'User' if m['role'] == 'user' else 'Actor'}: {m['content']}" for m in transcript]
    return "CONVERSATION SO FAR:\n" + ("\n\n".join(lines) or "(nothing yet)") + "\n\nDRAFT UNDER REVIEW:\n" + draft


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_verdict(text: str) -> Verdict:
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ObserverError("the observer's answer had no JSON object")
    try:
        return Verdict.model_validate(json.loads(body[start:end + 1]))
    except (json.JSONDecodeError, ValidationError) as error:
        raise ObserverError(f"the observer's answer could not be understood: {type(error).__name__}") from None


class HaikuObserver:
    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0) -> None:
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._timeout = timeout_seconds

    async def review(self, transcript: List[Message], draft: str) -> Verdict:
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model, max_tokens=600, system=OBSERVER_SYSTEM,
                    messages=[{"role": "user", "content": render_transcript(transcript, draft)}],
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise ObserverError("the observer did not answer in time") from None
        except Exception as error:                      # network, auth, rate limit: the review did not happen
            raise ObserverError(f"the observer could not be reached: {type(error).__name__}") from None
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        return parse_verdict(text)
