import pytest

from bicameral.actor import GeminiActor, is_transient


class Provider(Exception):
    def __init__(self, code):
        super().__init__(f"provider {code}")
        self.code = code


class Chunk:
    def __init__(self, text):
        self.text = text


def actor_with(*attempts):
    """A GeminiActor whose API call is scripted: each attempt is a list of chunk texts, ending in an exception to raise."""
    actor = GeminiActor.__new__(GeminiActor)                    # no client, no network
    actor._retry_delays = (0.0, 0.0)
    actor._request = lambda messages, system=None: ([], None)
    script = list(attempts)

    async def chunks(contents, config):
        for item in script.pop(0):
            if isinstance(item, Exception):
                raise item
            yield Chunk(item)

    actor._chunks = chunks
    actor.attempts_left = script
    return actor


async def collect(actor):
    return "".join([text async for text in actor.stream([{"role": "user", "content": "hi"}])])


def test_which_failures_are_transient():
    assert all(is_transient(Provider(code)) for code in (429, 500, 503, 504))
    assert not any(is_transient(Provider(code)) for code in (400, 401, 403, 404))
    assert not is_transient(RuntimeError("no code"))


async def test_an_overloaded_first_attempt_is_retried():
    actor = actor_with([Provider(503)], ["Hel", "lo"])
    assert await collect(actor) == "Hello"


async def test_gives_up_after_the_last_retry():
    actor = actor_with([Provider(503)], [Provider(503)], [Provider(503)])
    with pytest.raises(Provider):
        await collect(actor)
    assert actor.attempts_left == []


async def test_a_rejected_request_is_not_retried():
    actor = actor_with([Provider(400)], ["never used"])
    with pytest.raises(Provider):
        await collect(actor)
    assert actor.attempts_left == [["never used"]]


async def test_a_failure_after_text_was_shown_is_not_retried():
    actor = actor_with(["par", Provider(503)], ["whole reply"])
    stream = actor.stream([])
    assert await stream.__anext__() == "par"
    with pytest.raises(Provider):
        await stream.__anext__()
    assert actor.attempts_left == [["whole reply"]]
