# jaynes

A two-agent chat, named after Julian Jaynes' bicameral mind: one agent acts, the other observes and comments.

- **The actor** (Gemini) talks to the user and produces replies.
- **The observer** (Claude Haiku) reviews each finished reply and can **retract** it.

The design is **stream-and-retract**. The user sees the actor's reply as it streams, so a retraction is a correction after the fact, not a gate before delivery. When the observer retracts, the draft stays visible, marked as contested, and the actor follows it with exactly one correction. Corrections are not reviewed, so there is no review loop.

## Status

The backend is written and tested (no network needed). The frontend (`web/`) and the deployment to melville are not built yet. The design brief is [`docs/design.md`](docs/design.md) and the plan for running it is [`docs/run-plan.md`](docs/run-plan.md).

## Setup and run

Python 3.11 or newer.

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                      # no network, no keys

export GEMINI_API_KEY=...             # GOOGLE_API_KEY also works
export ANTHROPIC_API_KEY=...
.venv/bin/uvicorn bicameral.server:app --reload
```

The server refuses to start if a key is missing, and says which. It listens on `127.0.0.1:8000`. Try it by hand:

```
curl -s -X POST localhost:8000/sessions                                   # {"session_id": "..."}
curl -N localhost:8000/sessions/<id>/events                               # leave this open in one terminal
curl -s -X POST localhost:8000/sessions/<id>/messages \
     -H 'content-type: application/json' -d '{"text": "Hello"}'          # 202; the events stream in the other terminal
```

Add `?after=<event id>` (or a `Last-Event-ID` header) to the events URL to resume after a dropped connection. `GET /healthz` reports the number of sessions and turns in progress.

## How a turn works

1. The user's message is added to the session. Turns are serialised by a per-session lock.
2. The actor's reply streams to the browser, token by token, as a *draft*.
3. When the draft is complete, the observer reads the transcript (without the draft) and the draft, and returns a verdict: `ok` or `retract`, with severity, issues and guidance for a correction.
4. On `retract`, the draft is marked contested, a note with the issues goes into the actor's history as an internal note (not from the user), and the actor streams one correction.
5. If the actor or the observer fails, the user sees an `error` event saying which one. A failed review is never treated as `ok`.

## Planned stack

| Part | Choice |
| --- | --- |
| Backend | Python 3.11+, FastAPI, Pydantic v2, asyncio |
| Frontend | React + TypeScript (Vite), in `web/` |
| Actor | `gemini-3.1-flash-lite` through the `google-genai` SDK (`ACTOR_MODEL` overrides) |
| Observer | `claude-haiku-5-5` through the `anthropic` SDK (`OBSERVER_MODEL` overrides) |
| Transport | Server-sent events from `GET /sessions/{id}/events`, resumable with `?after=<id>` |
| Storage | None. Sessions live in process memory and are lost on restart. |

Model IDs and SDK call signatures are to be checked against the providers' current documentation when the code is written, not taken from the brief.

## Configuration

Keys come from the environment and never go in this repository. A `.env.example` with placeholder values will list them.

| Variable | Meaning |
| --- | --- |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Actor |
| `ANTHROPIC_API_KEY` | Observer |
| `ACTOR_MODEL`, `OBSERVER_MODEL` | Optional model overrides |

## Access and limits

There is no authentication, and none is planned for the first version, so **the service must not be reachable from the internet**. It runs on a machine on the local network and is used through an SSH port-forward (see the run plan). Because it is private, there are no rate limits or spend ceiling in the app; set budget alerts with the model providers instead.

## Out of scope for now

- Actor tools and function calling (the observer sees text only).
- Blocking a reply before delivery (hold-and-release).
- Authentication, persistence beyond process memory, multi-user support.
- Retrieval or a vector store.

## Layout

```
bicameral/     backend package: config, events, actor, observer, orchestrator, server
web/           Vite + React + TypeScript app (not built yet)
tests/         pytest, no network (fake actor and observer)
docs/          design brief and run plan
scripts/       deploy script
```
