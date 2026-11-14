"""HTTP routes. No authentication: this service is for a private network and must be reached only through a tunnel to localhost.

  POST /sessions                       -> {"session_id": ...}
  POST /sessions/{id}/messages         {"text": ...} -> 202; the turn runs in the background
  GET  /sessions/{id}/events           server-sent events; ?after=<id> (or Last-Event-ID) resumes after that event
  GET  /healthz                        {"ok": true, "sessions": n, "turns_in_progress": n, "tools": [...], "tools_off": [why each other tool is off]}
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict, Optional, Set

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .actor import Actor
from .config import Settings
from .events import Event
from .observer import Observer
from .orchestrator import Session

KEEPALIVE_SECONDS = 15.0
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class WebFiles(StaticFiles):
    """The built frontend, with caching that makes a deploy show up on a plain reload: the page is re-checked every time, and the hashed files under /assets (whose names change
    whenever their content does) are kept for a year."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return response


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def sse_frame(event: Event) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.flat())}\n\n"


def create_app(actor: Optional[Actor] = None, observer: Optional[Observer] = None) -> FastAPI:
    """Without an actor and observer given, the real ones are built from the environment when the server starts (and it refuses to start without its keys)."""
    sessions: Dict[str, Session] = {}
    tasks: Set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.actor is None or app.state.observer is None:
            from .actor import GeminiActor
            from .observer import HaikuObserver
            from .tools import build_registry
            settings = Settings.from_env()
            registry, notes = await build_registry(settings)
            app.state.tool_notes = notes
            app.state.max_tool_calls = settings.max_tool_calls_per_turn
            app.state.tools = registry.names
            app.state.actor = app.state.actor or GeminiActor(settings.gemini_api_key, settings.actor_model, registry=registry)
            app.state.observer = app.state.observer or HaikuObserver(settings.anthropic_api_key, settings.observer_model, settings.observer_timeout_seconds)
        yield
        for session in sessions.values():
            session.log.close()

    app = FastAPI(title="jaynes", lifespan=lifespan)
    app.state.actor = actor
    app.state.observer = observer
    app.state.tools = []
    app.state.tool_notes = []
    app.state.max_tool_calls = 5
    app.state.sessions = sessions

    def find(session_id: str) -> Session:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such session")
        return session

    @app.post("/sessions")
    async def new_session() -> Dict[str, str]:
        session_id = uuid.uuid4().hex
        sessions[session_id] = Session(session_id, app.state.actor, app.state.observer, app.state.max_tool_calls)
        return {"session_id": session_id}

    @app.post("/sessions/{session_id}/messages", status_code=202)
    async def send(session_id: str, message: MessageIn) -> Dict[str, bool]:
        session = find(session_id)
        task = asyncio.get_running_loop().create_task(session.handle_user_message(message.text))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"accepted": True}

    @app.get("/sessions/{session_id}/events")
    async def events(session_id: str, after: Optional[int] = Query(default=None, ge=0), last_event_id: Optional[str] = Header(default=None)) -> StreamingResponse:
        session = find(session_id)
        resume = after if after is not None else (int(last_event_id) if (last_event_id or "").isdigit() else 0)

        async def frames() -> AsyncIterator[str]:
            async for event in session.log.stream(resume, keepalive_seconds=KEEPALIVE_SECONDS):
                yield ": keepalive\n\n" if event is None else sse_frame(event)

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/healthz")
    async def healthz() -> Dict[str, object]:
        return {"ok": True, "sessions": len(sessions), "turns_in_progress": sum(1 for s in sessions.values() if s.busy), "tools": app.state.tools, "tools_off": app.state.tool_notes}

    if WEB_DIST.is_dir():                         # the built frontend, when there is one
        app.mount("/", WebFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
