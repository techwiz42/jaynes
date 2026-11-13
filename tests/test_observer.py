import pytest

from bicameral.observer import ObserverError, parse_verdict, render_transcript


def test_plain_json():
    v = parse_verdict('{"verdict": "retract", "severity": "high", "issues": ["x"], "correction_guidance": "fix"}')
    assert v.verdict == "retract" and v.issues == ["x"]


def test_json_in_a_code_fence_or_with_chatter():
    assert parse_verdict('```json\n{"verdict": "ok"}\n```').verdict == "ok"
    assert parse_verdict('Here you go: {"verdict": "ok", "severity": "none"} thanks').verdict == "ok"


@pytest.mark.parametrize("text", ["", "no json here", "{broken", '{"verdict": "maybe"}', '{"severity": "low"}', '["ok"]'])
def test_unusable_answers_raise_and_never_become_ok(text):
    with pytest.raises(ObserverError):
        parse_verdict(text)


def test_the_transcript_marks_who_spoke():
    text = render_transcript([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "draft")
    assert "User: hi" in text and "Actor: hello" in text and text.endswith("DRAFT UNDER REVIEW:\ndraft")


class _Block:
    def __init__(self, type, text=""):
        self.type, self.text = type, text


class _Response:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content, self.stop_reason = blocks, stop_reason


class _Client:
    def __init__(self, response):
        self.calls = []
        self.messages = self
        self._response = response

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


async def test_a_thinking_block_before_the_answer_is_ignored():
    from bicameral.observer import HaikuObserver
    client = _Client(_Response([_Block("thinking"), _Block("text", '{"verdict": "ok"}')]))
    verdict = await HaikuObserver("k", "m", client=client).review([], "draft")
    assert verdict.verdict == "ok"


async def test_running_out_of_tokens_while_thinking_is_a_clear_error_not_ok():
    from bicameral.observer import HaikuObserver
    client = _Client(_Response([_Block("thinking")], stop_reason="max_tokens"))
    with pytest.raises(ObserverError, match="ran out of room"):
        await HaikuObserver("k", "m", client=client).review([], "draft")


async def test_the_request_leaves_room_for_thinking():
    from bicameral.observer import MAX_OUTPUT_TOKENS, HaikuObserver
    client = _Client(_Response([_Block("text", '{"verdict": "ok"}')]))
    await HaikuObserver("k", "m", client=client).review([], "draft")
    assert client.calls[0]["max_tokens"] == MAX_OUTPUT_TOKENS >= 4000
