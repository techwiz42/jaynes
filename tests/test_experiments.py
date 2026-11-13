import pytest

from experiments.cases import CASES, Case
from experiments.compare import Run, render, run_case, summarize
from experiments.modes import ActorCall, Meter, NullObserver, ObserverCall, extract_final
from bicameral.orchestrator import Session
from dataclasses import asdict

from .fakes import OK, RETRACT, FakeActor, FakeObserver


def test_the_cases_are_well_formed():
    assert len({c.id for c in CASES}) == len(CASES)
    assert {c.category for c in CASES} == {"false_premise", "misconception", "reasoning", "control"}
    assert all(c.any_of for c in CASES)


def test_a_case_passes_on_any_pattern_regardless_of_case():
    case = Case("x", "control", "q", (r"\bAu\b", r"gold"))
    assert case.passes("The symbol is au.") and case.passes("It is GOLD")
    assert not case.passes("Silver") and not case.passes("Aurora")           # \b: a whole word


@pytest.mark.parametrize("text,answer,followed", [
    ("<workspace>draft\ncritique</workspace>\n<final>It is 408.</final>", "It is 408.", True),
    ("<workspace>a</workspace><final>one</final><final>two</final>", "two", True),
    ("<workspace>thinking</workspace>\nJust the answer.", "Just the answer.", False),
    ("No tags at all.", "No tags at all.", False),
])
def test_extract_final(text, answer, followed):
    assert extract_final(text) == (answer, followed)


class MeteredFake(FakeActor):
    """A fake actor that records into a meter as the real one does."""

    def __init__(self, meter, *replies):
        super().__init__(*replies)
        self.meter = meter

    async def stream(self, messages, system=None):
        async for chunk in super().stream(messages, system):
            yield chunk
        self.meter.actor_calls.append(ActorCall(0.1, 0.5, 100, 20))


CASE = Case("t", "reasoning", "What is 17 times 24?", (r"408",))


async def test_a_retracted_wrong_draft_that_the_correction_fixes():
    meter = Meter()
    session = Session("s", MeteredFake(meter, "It is 418.", "It is 408."), FakeObserver(RETRACT))
    meter.observer_calls.append(ObserverCall(0.4, 300, 40))
    run = await run_case(CASE, "two_agent", 0, session, meter)
    assert (run.draft, run.answer) == ("It is 418.", "It is 408.")
    assert (run.draft_passed, run.passed, run.retracted) == (False, True, True)
    assert run.draft_tokens == 120 and run.actor_tokens == 240 and run.observer_tokens == 340


async def test_a_correct_draft_that_is_not_retracted():
    meter = Meter()
    run = await run_case(CASE, "two_agent", 0, Session("s", MeteredFake(meter, "408"), FakeObserver(OK)), meter)
    assert (run.draft_passed, run.passed, run.retracted) == (True, True, False)


async def test_self_critique_keeps_only_the_final_answer():
    meter = Meter()
    reply = "<workspace>I think 418. Wait, 17*24=408.</workspace><final>408</final>"
    run = await run_case(CASE, "self_critique", 0, Session("s", MeteredFake(meter, reply), NullObserver()), meter)
    assert run.answer == "408" and run.passed and not run.format_miss and not run.retracted


async def test_a_workspace_that_mentions_the_answer_does_not_pass_a_wrong_final():
    meter = Meter()
    reply = "<workspace>maybe 408?</workspace><final>It is 418.</final>"
    run = await run_case(CASE, "self_critique", 0, Session("s", MeteredFake(meter, reply), NullObserver()), meter)
    assert not run.passed


async def test_an_actor_failure_is_recorded_not_scored():
    meter = Meter()
    run = await run_case(CASE, "two_agent", 0, Session("s", MeteredFake(meter, RuntimeError("down")), FakeObserver()), meter)
    assert run.actor_error == "RuntimeError" and not run.passed


def _row(**kw):
    return asdict(Run(**{"case_id": "x", "category": "reasoning", "mode": "two_agent", "rep": 0, **kw}))


def test_the_summary_separates_catching_mistakes_from_false_alarms():
    runs = [
        _row(draft_passed=False, passed=True, retracted=True, draft_seconds=1, seconds_total=4, draft_tokens=100, actor_tokens=200, observer_tokens=50),   # caught and fixed
        _row(draft_passed=False, passed=False, retracted=False),                                                                                                  # missed
        _row(draft_passed=True, passed=True, retracted=False),                                                                                                    # right, left alone
        _row(draft_passed=True, passed=False, retracted=True),                                                                                                    # false alarm
        _row(mode="self_critique", passed=True, seconds_total=3, actor_tokens=500),
        _row(mode="self_critique", passed=False, format_miss=True),
        _row(actor_error="ServerError 503 UNAVAILABLE"),
    ]
    s = summarize(runs)
    assert s["actor_alone"]["pass_rate"] == 0.5 and s["two_agent"]["pass_rate"] == 0.5 and s["self_critique"]["pass_rate"] == 0.5
    assert s["observer"]["catch_rate"] == 0.5 and s["observer"]["false_retraction_rate"] == 0.5 and s["observer"]["fixed_after_retraction"] == 1.0
    assert s["self_critique"]["format_misses"] == 1 and s["errors"] == {"actor_failures": 1, "runs": 7}
    assert "actor alone" in render(s) and "two-agent" in render(s)


def test_an_empty_summary_does_not_divide_by_zero():
    assert "actor alone" in render(summarize([]))
