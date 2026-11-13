"""Compare ways of checking an answer, on questions with a known right answer.

  python -m experiments.compare [--reps 3] [--concurrency 4] [--limit N] [--seed 1]

Each case is run in two modes, and the results are read three ways:

  actor alone       the actor's first draft in the two-agent run (so it is the same sample, not a separate one)
  self-critique     one Gemini call told to draft, critique and revise privately; only the <final> answer is kept
  two-agent         the actor's draft, the observer's review, and at most one correction (the final answer is the correction when the draft was retracted)

Needs GEMINI_API_KEY and ANTHROPIC_API_KEY. Results (every raw answer too) are written to experiments/results/, which git ignores.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from bicameral.orchestrator import Session

from .cases import CASES, Case
from .modes import SELF_CRITIQUE_SYSTEM, Meter, NullObserver, extract_final

RESULTS_DIR = Path(__file__).resolve().parent / "results"
MODES = ("two_agent", "self_critique")


@dataclass
class Run:
    case_id: str
    category: str
    mode: str
    rep: int
    answer: str = ""                 # the final answer for this mode
    draft: str = ""                  # two-agent: the actor's first draft (the "actor alone" answer)
    passed: bool = False
    draft_passed: bool = False
    retracted: bool = False
    observer_error: bool = False
    actor_error: Optional[str] = None
    format_miss: bool = False        # self-critique: the reply had no <final> block
    seconds_total: float = 0.0
    seconds_first_token: float = 0.0
    draft_seconds: float = 0.0       # time for the actor's first call alone
    draft_tokens: int = 0            # the actor's first call (in + out)
    actor_tokens: int = 0            # all actor calls
    observer_tokens: int = 0


def _events(session: Session, type: str) -> List[Any]:
    return [e for e in session.log.replay() if e.type == type]


async def run_case(case: Case, mode: str, rep: int, session: Session, meter: Meter) -> Run:
    """One question through one mode, on a fresh session. `meter` is the one the session's actor and observer record into."""
    run = Run(case.id, case.category, mode, rep)
    started = time.monotonic()
    await session.handle_user_message(case.question)
    run.seconds_total = time.monotonic() - started

    errors = _events(session, "error")
    actor_errors = [e for e in errors if e.data["where"] in ("actor", "actor_correction")]
    run.observer_error = any(e.data["where"] == "observer" for e in errors)
    if actor_errors:
        run.actor_error = actor_errors[0].data["message"]
    done = _events(session, "draft_complete")
    run.draft = done[0].data["text"] if done else ""
    run.retracted = bool(_events(session, "retraction"))

    if mode == "self_critique":
        run.answer, followed = extract_final(run.draft)
        run.format_miss = not followed
        run.draft = run.answer
    else:
        run.answer = done[1].data["text"] if run.retracted and len(done) > 1 else run.draft
    run.draft_passed = bool(run.draft) and case.passes(extract_final(run.draft)[0] if mode == "self_critique" else run.draft)
    run.passed = bool(run.answer) and case.passes(run.answer)

    calls = meter.actor_calls
    if calls:
        run.seconds_first_token = calls[0].seconds_to_first
        run.draft_seconds = calls[0].seconds
        run.draft_tokens = calls[0].tokens_in + calls[0].tokens_out
    run.actor_tokens = sum(c.tokens_in + c.tokens_out for c in calls)
    run.observer_tokens = sum(c.tokens_in + c.tokens_out for c in meter.observer_calls)
    return run


# ---- reading the results ------------------------------------------------------------------------------------------------------------------------------------

def _rate(hits: int, total: int) -> Optional[float]:
    return hits / total if total else None


def _mean(values: List[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def summarize(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pass rates, time and token use for the three readings, and how well the observer's retractions line up with real mistakes. Runs where the actor failed are left out of
    the rates and counted."""
    ok = [r for r in runs if not r["actor_error"]]
    two = [r for r in ok if r["mode"] == "two_agent"]
    selfc = [r for r in ok if r["mode"] == "self_critique"]
    categories = sorted({r["category"] for r in ok})

    def block(rows: List[Dict[str, Any]], passed_key: str, seconds_key: str, tokens) -> Dict[str, Any]:
        return {
            "n": len(rows),
            "pass_rate": _rate(sum(r[passed_key] for r in rows), len(rows)),
            "by_category": {c: _rate(sum(r[passed_key] for r in rows if r["category"] == c), sum(1 for r in rows if r["category"] == c)) for c in categories},
            "mean_seconds": _mean([r[seconds_key] for r in rows]),
            "mean_tokens": _mean([tokens(r) for r in rows]),
        }

    reviewed = [r for r in two if not r["observer_error"]]
    wrong = [r for r in reviewed if not r["draft_passed"]]
    right = [r for r in reviewed if r["draft_passed"]]
    retracted_wrong = [r for r in wrong if r["retracted"]]
    return {
        "actor_alone": block(two, "draft_passed", "draft_seconds", lambda r: r["draft_tokens"]),
        "self_critique": {**block(selfc, "passed", "seconds_total", lambda r: r["actor_tokens"]), "format_misses": sum(r["format_miss"] for r in selfc)},
        "two_agent": {**block(two, "passed", "seconds_total", lambda r: r["actor_tokens"] + r["observer_tokens"]),
                      "mean_seconds_to_first_token": _mean([r["seconds_first_token"] for r in two])},
        "observer": {
            "drafts_reviewed": len(reviewed),
            "wrong_drafts": len(wrong),
            "caught": len(retracted_wrong),
            "catch_rate": _rate(len(retracted_wrong), len(wrong)),
            "false_retraction_rate": _rate(sum(r["retracted"] for r in right), len(right)),
            "fixed_after_retraction": _rate(sum(r["passed"] for r in retracted_wrong), len(retracted_wrong)),
            "observer_errors": sum(r["observer_error"] for r in two),
        },
        "errors": {"actor_failures": len(runs) - len(ok), "runs": len(runs)},
    }


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def _num(value: Optional[float], digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render(summary: Dict[str, Any]) -> str:
    rows = [("actor alone", summary["actor_alone"]), ("self-critique", summary["self_critique"]), ("two-agent", summary["two_agent"])]
    categories = list(summary["actor_alone"]["by_category"])
    lines = ["| mode | runs | pass | " + " | ".join(categories) + " | seconds | tokens |", "| --- | ---: | ---: | " + " | ".join("---:" for _ in categories) + " | ---: | ---: |"]
    for name, b in rows:
        lines.append(f"| {name} | {b['n']} | {_pct(b['pass_rate'])} | " + " | ".join(_pct(b["by_category"].get(c)) for c in categories) + f" | {_num(b['mean_seconds'])} | {_num(b['mean_tokens'], 0)} |")
    o, t = summary["observer"], summary["two_agent"]
    lines += [
        "",
        f"Two-agent: the first words appear after {_num(t['mean_seconds_to_first_token'])} s on average; the final answer after {_num(t['mean_seconds'])} s.",
        f"Observer: {o['drafts_reviewed']} drafts reviewed, {o['wrong_drafts']} wrong; it retracted {o['caught']} of them (catch rate {_pct(o['catch_rate'])}); "
        f"it retracted {_pct(o['false_retraction_rate'])} of the correct drafts; {_pct(o['fixed_after_retraction'])} of the retracted wrong drafts were right after the correction; "
        f"{o['observer_errors']} reviews failed.",
        f"Self-critique: {summary['self_critique']['format_misses']} replies had no <final> block.",
        f"Actor failures (left out of the rates): {summary['errors']['actor_failures']} of {summary['errors']['runs']} runs.",
    ]
    return "\n".join(lines)


# ---- running it ---------------------------------------------------------------------------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> None:
    from anthropic import AsyncAnthropic

    from bicameral.config import Settings
    from bicameral.observer import HaikuObserver

    from .modes import MeteredActor, MeteredClient

    settings = Settings.from_env()
    anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)
    cases = list(CASES)
    if args.limit:
        cases = random.Random(args.seed).sample(cases, min(args.limit, len(cases)))
    jobs = [(case, mode, rep) for case in cases for mode in MODES for rep in range(args.reps)]
    gate = asyncio.Semaphore(args.concurrency)
    runs: List[Run] = []

    async def one(case: Case, mode: str, rep: int) -> None:
        async with gate:
            for attempt in range(2):
                meter = Meter()
                if mode == "two_agent":
                    actor = MeteredActor(settings.gemini_api_key, settings.actor_model, meter)
                    observer = HaikuObserver(settings.anthropic_api_key, settings.observer_model, settings.observer_timeout_seconds, client=MeteredClient(anthropic, meter))
                else:
                    actor = MeteredActor(settings.gemini_api_key, settings.actor_model, meter, system_override=SELF_CRITIQUE_SYSTEM)
                    observer = NullObserver()
                run = await run_case(case, mode, rep, Session(f"{case.id}-{mode}-{rep}", actor, observer), meter)
                if not run.actor_error or attempt == 1:
                    break
                await asyncio.sleep(10)                      # one more try after a pause, so a busy moment does not cost a data point
            runs.append(run)
            print(f"\r{len(runs)}/{len(jobs)} runs", end="", flush=True)

    started = time.monotonic()
    await asyncio.gather(*(one(*job) for job in jobs))
    print(f"\ndone in {time.monotonic() - started:.0f} s")

    rows = [asdict(r) for r in sorted(runs, key=lambda r: (r.case_id, r.mode, r.rep))]
    summary = summarize(rows)
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps({"summary": summary, "runs": rows}, indent=2))
    report = render(summary)
    (RESULTS_DIR / f"{stamp}.md").write_text(report + "\n")
    print(report)
    print(f"\nraw answers: experiments/results/{stamp}.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3, help="runs per case and mode (default 3)")
    parser.add_argument("--concurrency", type=int, default=4, help="runs at once (default 4)")
    parser.add_argument("--limit", type=int, default=0, help="only this many randomly chosen cases (a smoke test)")
    parser.add_argument("--seed", type=int, default=1, help="which cases --limit picks")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
