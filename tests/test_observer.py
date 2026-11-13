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
