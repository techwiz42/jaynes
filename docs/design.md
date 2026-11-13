# Design brief: bicameral chat (stream-and-retract)

Source: the project brief, kept here as written scope. Where code and this brief differ, decide deliberately and update both.

## Goal

A chat application where the **actor** (Gemini) talks to the user and the **observer** (Claude Haiku) reviews each finished reply and can **retract** it. The user sees the reply as it streams, so retraction is a correction after the fact.

## Stack

- Backend: Python 3.11+, FastAPI, Pydantic v2, asyncio, type hints throughout.
- Frontend: React with TypeScript (Vite), no JavaScript-only files.
- Actor `gemini-3.1-flash-lite` (`google-genai`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`); observer `claude-haiku-5-5` (`anthropic`, `ANTHROPIC_API_KEY`). Overridable with `ACTOR_MODEL` and `OBSERVER_MODEL`. No hardcoded keys.

## Turn behaviour (must hold)

1. `handle_user_message` takes a per-session lock; turns are serialised.
2. Emit `user_message`, then stream the actor's reply as `token` events with `kind="draft"`.
3. Emit `draft_complete`; append the assistant message to history.
4. Send the transcript (excluding the draft under review) plus the draft to the observer.
5. Emit `observer_verdict`.
6. On `retract`, emit `retraction` (issues and the retracted text), append an internal note (labelled internal, not from the user) with the issues and guidance, and stream a correction with `kind="correction"`.
7. Exactly one correction per turn. Corrections are not reviewed.
8. On failure emit `error` with `where` = `actor`, `observer` or `actor_correction`. A failed review is never `ok`.

## Backend work

- The brief mentions an existing scaffold; none exists. Write it: `config.py`, `events.py` (append-only log with replay and live fan-out), `actor.py`, `observer.py` (returns a verdict, never fails open), `orchestrator.py`, `server.py`.
- pytest with no network, using fake actor and observer objects (`stream(messages, system=...)`, `review(transcript, draft)`): clean turn, retracted turn with exactly one correction, correction not reviewed, observer parse failure emits `error` and does not retract, actor failure pops the unanswered user turn, event log replays from `after`.
- Verify SDK usage against current docs (`client.aio.models.generate_content_stream`, async `messages.create`).
- README with setup and run (`uvicorn bicameral.server:app --reload`); `.env.example`.

## Frontend work

Vite + React + TypeScript in `web/`.

- `POST /sessions` on load; `POST /sessions/{id}/messages` with `{ "text": string }` (202); `EventSource` on `GET /sessions/{id}/events`, tracking the last event id and reconnecting with `?after=<id>`.
- Typed discriminated union over `user_message | token | draft_complete | observer_verdict | retraction | error`, in its own module.
- Rendering: tokens append to the message, draft and correction streams kept separate; a retracted draft stays visible, struck through or dimmed, with a "contested" badge; the correction follows as its own message labelled "Correction".
- Observer side panel: each verdict next to the message it reviewed, with severity and issues; `ok` verdicts shown quietly.
- Errors inline with `where` and `message`. Plain styling. Vitest test for the event reducer.

## Acceptance criteria

- `pytest` passes with no network; `npm test` passes in `web/`.
- With real keys, a reply streams immediately; a retraction marks the reply contested and one correction appears.
- Reconnecting the event stream mid-turn does not duplicate or drop events.
- No API key in the repository.

## Out of scope for now

Actor tools; hold-and-release; authentication, persistence beyond process memory, multi-user.

## Working notes

Small, reviewable commits. Check official docs when a model ID or SDK signature is uncertain. If the event schema changes, update the backend `events.py` types and the frontend union in the same commit.
