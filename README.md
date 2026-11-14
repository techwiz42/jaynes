# jaynes

A two-agent chat, named after Julian Jaynes' bicameral mind: one agent acts, the other observes and comments.

- **The actor** (Gemini) talks to the user and produces replies.
- **The observer** (Claude Haiku) reviews each finished reply and can **retract** it.

The design is **stream-and-retract**. The user sees the actor's reply as it streams, so a retraction is a correction after the fact, not a gate before delivery. When the observer retracts, the draft stays visible, marked as contested, and the actor follows it with a correction, and the observer reviews that too. This repeats until the observer approves; `MAX_REVISIONS` (default 5) stops an endless disagreement, and the turn then ends with an error rather than passing the reply off as approved.

## Status

Working. The backend and frontend are written and tested (no network needed), and the app runs on a local-network machine (melville) with the real models: the Gemini actor streams a reply and the Haiku observer reviews it. A retraction has been exercised with scripted fakes and in a real browser, but not yet with the live models. The design brief is [`docs/design.md`](docs/design.md) and the plan for running it is [`docs/run-plan.md`](docs/run-plan.md).

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

### Frontend

Node 20.19+ or 22.12+. The app is in `web/`.

```
cd web
npm ci
npm test                              # the event reducer and event parsing
npm run build                         # type-checks, then builds web/dist
npm run dev                           # dev server on :5173, proxying the API to :8000 (run the backend too)
```

When `web/dist` exists the backend serves it, so after `npm run build` the whole app is at `http://localhost:8000`. The page shows the actor's reply as it streams; a retracted draft stays visible, struck through and marked *contested*, with the correction below it; the observer's verdicts are in the side panel; errors appear inline. If the connection drops, the page reconnects and asks only for the events it missed.

Add `?after=<event id>` (or a `Last-Event-ID` header) to the events URL to resume after a dropped connection. `GET /healthz` reports the number of sessions and turns in progress.

## Running it on melville

The server runs as a `launchd` service on melville, bound to `127.0.0.1:8000`, so it is reachable only through an SSH forward:

```
ssh -L 8000:localhost:8000 melville        # then open http://localhost:8000
```

From a clone of this repository on the laptop:

```
scripts/set-jaynes-keys.sh                 # prompts for the two API keys (typed without echo) and stores them on melville, readable only by its owner
scripts/update-jaynes.sh                   # deploys origin/main: melville clones the commit, web/ is built here and uploaded, the service restarts
scripts/update-jaynes.sh <sha>             # deploy or roll back to a pushed commit
scripts/update-jaynes.sh --restart         # restart only
scripts/update-jaynes.sh --status          # what is deployed and whether it answers
```

Paste only the key value at the prompts, not a `NAME=value` line. A deploy refuses to restart while a reply is being written (add `--force` to override). A restart ends all conversations, since they live in memory. Logs are in `~/Library/Logs/jaynes.log` on melville.

## How a turn works

1. The user's message is added to the session. Turns are serialised by a per-session lock.
2. The actor's reply streams to the browser, token by token, as a *draft*.
3. When the draft is complete, the observer reads the transcript (without the draft) and the draft, and returns a verdict: `ok` or `retract`, with severity, issues and guidance for a correction.
4. On `retract`, the draft is marked contested, a note with the issues goes into the actor's history as an internal note (not from the user), and the actor streams a correction, which goes back to the observer. The loop ends on `ok`, on an error, or at `MAX_REVISIONS` corrections.
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
| `DATA_DIR` | Where the database (users, conversations, memory) lives; `deploy/run.sh` defaults it to `~/jaynes/data` |
| `COOKIE_SECURE`, `MAX_UPLOAD_BYTES`, `SHARED_PRIVATE_TOOLS` | See [`docs/users-and-memory.md`](docs/users-and-memory.md) |

## Tools

The actor can use read-only tools: `web_fetch`, `web_search` (Tavily), `python_sandbox`, `retrieve_docs` (your Markdown files), `github_read` (allowlisted private repositories), and `gmail_read` and `drive_read` (your Gmail and Google Drive). Each is off unless its configuration is set, and the ones that read private content also need `ALLOW_PRIVATE_EGRESS=1`, because their results go to Google and Anthropic. Once a conversation has read private data, web search and fetch are refused for the rest of it, so nothing can be sent out. Tool calls appear as small badges in the conversation, and the observer is shown the full tool trace so it can check the draft against what the tools returned. See [`docs/tools.md`](docs/tools.md) for the configuration, the data-egress decision, the defences (private-content guard, prompt-injection handling, SSRF protection, audit log) and the sandbox's limits.

## Accounts and memory

People register with an email and a password (registration is open) and log in; everything else needs the login. Each user's conversations are saved, listed in a sidebar and can be continued after a restart. Each user also has a **private memory**: their past conversations, documents they upload (PDF, text, Markdown, CSV, JSON) and notes (written in the page, or "remember that…" said to Jaynes). Two tools, `memory_search` and `memory_save`, work on the logged-in user's memory only: the user is taken from the login, never from anything the model says, and every query filters on it in the SQL. A user cannot see, search, continue or delete anyone else's conversations, documents or notes, and can delete their whole account with its data. Search is SQLite's full-text index with BM25 ranking, so nothing is sent to another service to index or search.

Searching memory returns the user's private content to the model providers, so the memory tools need `ALLOW_PRIVATE_EGRESS=1` and count as reading private data (web search and fetch are refused for the rest of that conversation). The Gmail, Drive, GitHub and local-docs tools use one set of credentials, so with accounts they are **off** unless `SHARED_PRIVATE_TOOLS=1`. See [`docs/users-and-memory.md`](docs/users-and-memory.md) for the security model, the limits and the deployment.

## Access and limits

The service is still meant for a private network: **it must not be reachable from the internet**. It runs on a machine on the local network and is used through an SSH port-forward (see the run plan). Accounts keep users apart, but registration is open, so anyone who can reach the page can make an account and spend your model quota; there is no spend ceiling in the app, so set budget alerts with the model providers.

## Out of scope for now

- Blocking a reply before delivery (hold-and-release).
- Email verification, password reset and two-factor login.
- A vector store or embeddings (memory and `retrieve_docs` are keyword indexes).

## Layout

```
bicameral/     backend package: config, events, actor, observer, orchestrator, server
web/           Vite + React + TypeScript app
tests/         pytest, no network (fake actor and observer)
docs/          design brief and run plan
scripts/       deploy script
```
