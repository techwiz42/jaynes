import pytest

from bicameral.actor import ActorError
from bicameral.orchestrator import INTERNAL_NOTE_LABEL, Session

from .fakes import BROKEN, OK, RETRACT, FakeActor, FakeObserver


def types(session):
    return [e.type for e in session.log.replay()]


async def test_a_clean_turn_has_no_retraction():
    session = Session("s", FakeActor(["Hel", "lo"]), FakeObserver(OK))
    await session.handle_user_message("hi")
    assert types(session) == ["user_message", "token", "token", "draft_complete", "observer_verdict"]
    done = session.log.replay()[3]
    assert done.data["text"] == "Hello"
    assert [m["role"] for m in session.history] == ["user", "assistant"]


async def test_tokens_are_drafts_with_one_message_id():
    session = Session("s", FakeActor(["a", "b"]), FakeObserver(OK))
    await session.handle_user_message("hi")
    tokens = [e for e in session.log.replay() if e.type == "token"]
    assert {t.data["kind"] for t in tokens} == {"draft"}
    assert len({t.data["message_id"] for t in tokens}) == 1


async def test_a_retracted_turn_gets_exactly_one_correction():
    actor = FakeActor("wrong", "right")
    session = Session("s", actor, FakeObserver(RETRACT))
    await session.handle_user_message("when?")
    assert types(session) == ["user_message", "token", "draft_complete", "observer_verdict", "retraction", "token", "draft_complete"]
    retraction = session.log.replay()[4]
    assert retraction.data["text"] == "wrong" and retraction.data["issues"] == ["states a wrong date"]
    correction = session.log.replay()[5]
    assert correction.data["kind"] == "correction" and correction.data["corrects"] == retraction.data["message_id"]
    assert len(actor.calls) == 2


async def test_the_internal_note_is_labelled_and_reaches_the_actor():
    actor = FakeActor("wrong", "right")
    session = Session("s", actor, FakeObserver(RETRACT))
    await session.handle_user_message("when?")
    last_call = actor.calls[1]
    assert last_call[-1]["role"] == "user" and last_call[-1]["content"].startswith(INTERNAL_NOTE_LABEL)
    assert "states a wrong date" in last_call[-1]["content"] and "give the right date" in last_call[-1]["content"]
    assert [m["role"] for m in session.history] == ["user", "assistant", "user", "assistant"]


async def test_a_correction_is_not_itself_reviewed():
    observer = FakeObserver(RETRACT)                      # a second verdict is not available: a second review would fail the test
    session = Session("s", FakeActor("wrong", "right"), observer)
    await session.handle_user_message("when?")
    assert len(observer.reviews) == 1
    assert "observer_verdict" not in types(session)[types(session).index("draft_complete", 3):]


async def test_the_observer_sees_the_transcript_without_the_draft():
    observer = FakeObserver(OK, OK)
    session = Session("s", FakeActor("one", "two"), observer)
    await session.handle_user_message("first")
    await session.handle_user_message("second")
    transcript, draft = observer.reviews[1]
    assert draft == "two"
    assert [m["content"] for m in transcript] == ["first", "one", "second"]


async def test_an_observer_failure_emits_an_error_and_does_not_retract():
    session = Session("s", FakeActor("fine"), FakeObserver(BROKEN))
    await session.handle_user_message("hi")
    assert types(session)[-1] == "error" and "retraction" not in types(session)
    error = session.log.replay()[-1]
    assert error.data["where"] == "observer"
    assert [m["role"] for m in session.history] == ["user", "assistant"]       # the draft stands


async def test_an_actor_failure_pops_the_unanswered_user_turn():
    session = Session("s", FakeActor(ActorError("boom")), FakeObserver())
    await session.handle_user_message("hi")
    assert types(session) == ["user_message", "error"]
    assert session.log.replay()[-1].data["where"] == "actor"
    assert session.history == []


async def test_an_actor_failure_part_way_through_a_reply_also_pops_the_turn():
    session = Session("s", FakeActor((["par"], RuntimeError("cut"))), FakeObserver())
    await session.handle_user_message("hi")
    assert session.log.replay()[-1].data["where"] == "actor" and session.history == []


async def test_an_empty_reply_is_an_actor_error():
    session = Session("s", FakeActor([""]), FakeObserver())
    await session.handle_user_message("hi")
    assert session.log.replay()[-1].data["where"] == "actor"


async def test_a_correction_failure_emits_actor_correction_and_drops_the_note():
    session = Session("s", FakeActor("wrong", RuntimeError("down")), FakeObserver(RETRACT))
    await session.handle_user_message("when?")
    assert session.log.replay()[-1].data["where"] == "actor_correction"
    assert [m["role"] for m in session.history] == ["user", "assistant"]


async def test_errors_never_carry_exception_details():
    session = Session("s", FakeActor(RuntimeError("secret-key-123")), FakeObserver())
    await session.handle_user_message("hi")
    assert "secret-key-123" not in session.log.replay()[-1].data["message"]


async def test_turns_are_serialised():
    import asyncio
    session = Session("s", FakeActor("a", "b"), FakeObserver(OK, OK))
    await asyncio.gather(session.handle_user_message("one"), session.handle_user_message("two"))
    order = [e.type for e in session.log.replay() if e.type in ("user_message", "observer_verdict")]
    assert order == ["user_message", "observer_verdict", "user_message", "observer_verdict"]
