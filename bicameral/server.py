"""HTTP routes. Users register and log in; everything else needs the login cookie, and a user sees only their own sessions and conversations. The service is still meant
to be reached through a tunnel to localhost.

  POST /auth/register {email, password} -> 201 {"email"}; logs in      POST /auth/login {email, password} -> {"email"}
  POST /auth/logout                                                     GET /auth/me -> {"email"} or 401
  POST /auth/delete-account {password}  removes the account and all its data
  POST /sessions {conversation_id?}     -> {"session_id", "conversation_id", "live"}  (a new conversation, saved with its first message, or an earlier one of this user's continued;
                                           "live" is true when that conversation already had a running session, whose events then replay all of it)
  POST /sessions/{id}/messages {"text", "attachments"?} -> 202; the turn runs in the background
  GET  /sessions/{id}/events            server-sent events; ?after=<id> (or Last-Event-ID) resumes after that event
  GET  /conversations                   this user's conversations      GET /conversations/{id}/messages     DELETE /conversations/{id}
  POST /attachments (body = the file's bytes, ?name=) -> {"name", "text", "chars", "truncated"}: the text of a file to send with a message; nothing is stored
  GET  /healthz                         {"ok": true, "sessions": n, "turns_in_progress": n, "tools": [...], "tools_off": [why each other tool is off]}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import auth
from .actor import Actor
from .config import Settings
from .documents import DocumentError, extract_text
from .events import Event
from .limits import Limits, seconds_until_utc_midnight, utc_day
from .observer import Observer
from .orchestrator import Session
from .store import Store, StoreError

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15.0
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
COOKIE = "jaynes_session"
HISTORY_TURNS = 40                  # messages of an earlier conversation given back to the model when it is continued


class WebFiles(StaticFiles):
    """The built frontend, with caching that makes a deploy show up on a plain reload: the page is re-checked every time, and the hashed files under /assets (whose names change
    whenever their content does) are kept for a year."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return response


MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_CHARS = 100_000                    # of one file's text that goes to the model; the rest is cut off and the user is told
MAX_ATTACHED_CHARS = 250_000                      # of all the files of one message together


class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_ATTACHMENT_CHARS)


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=8000)
    attachments: List[AttachmentIn] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @model_validator(mode="after")
    def something_to_send(self) -> "MessageIn":
        if not self.text.strip() and not self.attachments:
            raise ValueError("write a message or attach a file")
        if sum(len(a.text) for a in self.attachments) > MAX_ATTACHED_CHARS:
            raise ValueError(f"the files together are too long: at most {MAX_ATTACHED_CHARS:,} characters")
        return self


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=500)


class PasswordIn(BaseModel):
    password: str = Field(max_length=500)


class NewSession(BaseModel):
    conversation_id: Optional[str] = Field(default=None, max_length=64)


def sse_frame(event: Event) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.flat())}\n\n"


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                               "form-action 'self'; frame-ancestors 'none'; object-src 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
MAX_BODY_SLACK_BYTES = 1_000_000         # beyond the largest file, what a request may carry (a message with its attachments' text, a login)


def create_app(actor: Optional[Actor] = None, observer: Optional[Observer] = None, store: Optional[Store] = None, cookie_secure: bool = False,
               max_upload_bytes: int = 10_000_000, limits: Optional[Limits] = None) -> FastAPI:
    """Without an actor and observer given, the real ones are built from the environment when the server starts (and it refuses to start without its keys)."""
    sessions: Dict[str, Session] = {}
    by_conversation: Dict[str, str] = {}                    # conversation id -> the live session continuing it
    tasks: Set[asyncio.Task] = set()
    login_failures = auth.Throttle(limit=5, window_seconds=15 * 60)         # per email
    address_failures = auth.Throttle(limit=30, window_seconds=15 * 60)      # per address
    registrations = auth.Throttle(limit=20, window_seconds=3600)            # per address

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.actor is None or app.state.observer is None or app.state.store is None:
            from .actor import GeminiActor
            from .observer import HaikuObserver
            from .tools import build_registry
            settings = Settings.from_env()
            app.state.store = app.state.store or Store(str(Path(settings.data_dir) / "jaynes.db"))
            app.state.cookie_secure = settings.cookie_secure
            app.state.max_upload_bytes = settings.max_upload_bytes
            app.state.limits = Limits(settings.max_messages_per_hour, settings.max_user_tokens_per_day, settings.max_global_tokens_per_day, settings.max_new_accounts_per_day)
            registry, notes = await build_registry(settings, app.state.store)
            app.state.tool_notes = notes
            app.state.max_tool_calls = settings.max_tool_calls_per_turn
            app.state.max_revisions = settings.max_revisions
            app.state.tools = registry.names
            app.state.actor = app.state.actor or GeminiActor(settings.gemini_api_key, settings.actor_model, registry=registry,
                                                                  vertex_project=settings.vertex_project, vertex_location=settings.vertex_location)
            app.state.observer = app.state.observer or HaikuObserver(settings.anthropic_api_key, settings.observer_model, settings.observer_timeout_seconds)
        yield
        for session in sessions.values():
            session.log.close()

    app = FastAPI(title="jaynes", lifespan=lifespan)
    app.state.actor = actor
    app.state.observer = observer
    app.state.store = store
    app.state.cookie_secure = cookie_secure
    app.state.max_upload_bytes = max_upload_bytes
    app.state.limits = limits                                # None: nothing is limited (tests, and a private install that sets every ceiling to 0 gets Limits of zeros)
    app.state.tools = []
    app.state.tool_notes = []
    app.state.max_tool_calls = 5
    app.state.max_revisions = 5
    app.state.sessions = sessions

    @app.middleware("http")
    async def protect(request: Request, call_next):
        """A request body beyond the largest file plus some slack is refused before it is read; every answer carries the security headers (and HSTS when served over https)."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > app.state.max_upload_bytes + MAX_BODY_SLACK_BYTES:
            response: Response = Response(status_code=413, content='{"detail":"request too large"}', media_type="application/json")
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if app.state.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    # ---- guards ------------------------------------------------------------------------------------------------------------------------------------------------

    def same_origin(request: Request) -> None:
        """A browser sends Origin on a cross-site or cross-port request; it must be this server's own address. (Cookies do not tell ports apart, so another page on localhost could
        otherwise act as the user.) Requests without an Origin, such as from a script, are not browsers acting for a user."""
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
            raise HTTPException(status_code=403, detail="cross-origin request refused")

    async def current_user(request: Request) -> Dict[str, str]:
        token = request.cookies.get(COOKIE)
        user = await app.state.store.run(app.state.store.user_for_login, auth.hash_token(token)) if token else None
        if user is None:
            raise HTTPException(status_code=401, detail="log in first")
        return user

    def address(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    hourly = auth.Throttle(limit=1, window_seconds=3600)      # replaced when the ceilings are known (below); one event per message, per user

    def ceilings() -> Optional[Limits]:
        return app.state.limits

    async def check_budget(user: Dict[str, str]) -> None:
        """Refuse a message the user, or Jaynes as a whole, may not afford today; the answer says which ceiling and when it lifts."""
        limit = ceilings()
        if limit is None:
            return
        if limit.messages_per_hour:
            hourly.limit = limit.messages_per_hour
            if hourly.blocked(user["id"]):
                wait = hourly.retry_after(user["id"])
                raise HTTPException(status_code=429, detail=f"you are sending messages too fast; try again in {max(1, wait // 60)} minute(s)", headers={"Retry-After": str(wait)})
        day, db = utc_day(), app.state.store
        if limit.user_tokens_per_day and await db.run(db.user_tokens, user["id"], day) >= limit.user_tokens_per_day:
            raise HTTPException(status_code=429, detail="you have used today's allowance; it renews at midnight UTC", headers={"Retry-After": str(seconds_until_utc_midnight())})
        if limit.global_tokens_per_day and (await db.run(db.day_totals, day))["tokens"] >= limit.global_tokens_per_day:
            raise HTTPException(status_code=503, detail="Jaynes has reached its limit for today and is resting until midnight UTC", headers={"Retry-After": str(seconds_until_utc_midnight())})

    def start_login(response: Response, user_id: str, token: str) -> None:
        response.set_cookie(COOKIE, token, max_age=int(auth.LOGIN_LIFETIME_SECONDS), httponly=True, samesite="strict", secure=app.state.cookie_secure, path="/")

    # ---- accounts ----------------------------------------------------------------------------------------------------------------------------------------------

    @app.post("/auth/register", status_code=201, dependencies=[Depends(same_origin)])
    async def register(body: Credentials, request: Request, response: Response) -> Dict[str, str]:
        where = address(request)
        if registrations.blocked(where):
            raise HTTPException(status_code=429, detail="too many registrations from here; try again later", headers={"Retry-After": str(registrations.retry_after(where))})
        try:
            email = auth.normalize_email(body.email)
            auth.check_password(body.password)
        except auth.AuthError as error:
            raise HTTPException(status_code=422, detail=str(error))
        limit = ceilings()
        if limit and limit.new_accounts_per_day and await app.state.store.run(app.state.store.accounts_created_since, time.time() - time.time() % 86400) >= limit.new_accounts_per_day:
            raise HTTPException(status_code=503, detail="new accounts are closed for today; try again after midnight UTC", headers={"Retry-After": str(seconds_until_utc_midnight())})
        password_hash = await asyncio.to_thread(auth.hash_password, body.password)
        try:
            user_id = await app.state.store.run(app.state.store.create_user, email, password_hash)
        except StoreError as error:
            raise HTTPException(status_code=409, detail=str(error))
        registrations.add(where)
        token, hashed = auth.new_token()
        await app.state.store.run(app.state.store.create_login, user_id, hashed, auth.LOGIN_LIFETIME_SECONDS)
        start_login(response, user_id, token)
        return {"email": email}

    @app.post("/auth/login", dependencies=[Depends(same_origin)])
    async def login(body: Credentials, request: Request, response: Response) -> Dict[str, str]:
        where = address(request)
        try:
            email = auth.normalize_email(body.email)
        except auth.AuthError:
            raise HTTPException(status_code=401, detail="wrong email or password")
        for throttle, key in ((login_failures, email), (address_failures, where)):
            if throttle.blocked(key):
                raise HTTPException(status_code=429, detail="too many failed attempts; try again later", headers={"Retry-After": str(throttle.retry_after(key))})
        user = await app.state.store.run(app.state.store.user_by_email, email)
        ok = await asyncio.to_thread(auth.verify_password, body.password, user["password_hash"] if user else None)
        if not (ok and user):
            login_failures.add(email)
            address_failures.add(where)
            raise HTTPException(status_code=401, detail="wrong email or password")
        login_failures.clear(email)
        token, hashed = auth.new_token()
        await app.state.store.run(app.state.store.create_login, user["id"], hashed, auth.LOGIN_LIFETIME_SECONDS)
        start_login(response, user["id"], token)
        return {"email": email}

    @app.post("/auth/logout", dependencies=[Depends(same_origin)])
    async def logout(request: Request, response: Response) -> Dict[str, bool]:
        token = request.cookies.get(COOKIE)
        if token:
            await app.state.store.run(app.state.store.end_login, auth.hash_token(token))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/auth/me")
    async def me(user: Dict[str, str] = Depends(current_user)) -> Dict[str, str]:
        return {"email": user["email"]}

    @app.post("/auth/delete-account", dependencies=[Depends(same_origin)])
    async def delete_account(body: PasswordIn, response: Response, user: Dict[str, str] = Depends(current_user)) -> Dict[str, bool]:
        record = await app.state.store.run(app.state.store.user_by_email, user["email"])
        if not (record and await asyncio.to_thread(auth.verify_password, body.password, record["password_hash"])):
            raise HTTPException(status_code=401, detail="wrong password")
        for session_id in [sid for sid, s in sessions.items() if s.user_id == user["id"]]:
            sessions.pop(session_id).log.close()
        for conversation_id in [c for c, sid in by_conversation.items() if sid not in sessions]:
            by_conversation.pop(conversation_id, None)
        await app.state.store.run(app.state.store.delete_user, user["id"])
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ---- sessions: one live conversation -------------------------------------------------------------------------------------------------------------------------

    def find(session_id: str, user: Dict[str, str]) -> Session:
        session = sessions.get(session_id)
        if session is None or session.user_id != user["id"]:             # another user's session looks exactly like one that does not exist
            raise HTTPException(status_code=404, detail="no such session")
        return session

    @app.post("/sessions", dependencies=[Depends(same_origin)])
    async def new_session(body: Optional[NewSession] = None, user: Dict[str, str] = Depends(current_user)) -> Dict[str, object]:
        db = app.state.store
        conversation_id = body.conversation_id if body else None
        history: List[Dict[str, str]] = []
        saved: List[Dict[str, Any]] = []
        if conversation_id:
            live = by_conversation.get(conversation_id)
            if live and live in sessions and sessions[live].user_id == user["id"]:
                return {"session_id": live, "conversation_id": conversation_id, "live": True}
            messages = await db.run(db.conversation_messages, user["id"], conversation_id)
            if messages is None:
                raise HTTPException(status_code=404, detail="no such conversation")
            saved = messages
            history = [{"role": m["role"], "content": m["text"]} for m in messages[-HISTORY_TURNS:]]
        else:
            conversation_id = uuid.uuid4().hex                       # saved when its first message is, so opening the page leaves nothing behind
        session_id = uuid.uuid4().hex

        async def spend(tokens: int, user_id: str = user["id"]) -> None:
            await db.run(db.add_usage, user_id, utc_day(), tokens, 1)

        sessions[session_id] = Session(session_id, app.state.actor, app.state.observer, app.state.max_tool_calls, app.state.max_revisions,
                                       user_id=user["id"], conversation_id=conversation_id, store=db, history=history, new_conversation=not body or not body.conversation_id, spend=spend)
        for index, message in enumerate(saved):                   # the page shows a conversation from its event log, so the saved messages go in it first: reopening it later, while the session lives, shows all of it
            if message["role"] == "user":
                sessions[session_id].log.append("user_message", text=message["text"])
            else:
                sessions[session_id].log.append("draft_complete", message_id=f"h{index}", text=message["text"])
        by_conversation[conversation_id] = session_id
        return {"session_id": session_id, "conversation_id": conversation_id, "live": False}

    @app.post("/sessions/{session_id}/messages", status_code=202, dependencies=[Depends(same_origin)])
    async def send(session_id: str, message: MessageIn, user: Dict[str, str] = Depends(current_user)) -> Dict[str, bool]:
        session = find(session_id, user)
        await check_budget(user)
        if ceilings() and ceilings().messages_per_hour:
            hourly.add(user["id"])
        task = asyncio.get_running_loop().create_task(session.handle_user_message(message.text.strip(), [(a.name, a.text) for a in message.attachments]))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"accepted": True}

    @app.get("/sessions/{session_id}/alive")
    async def alive(session_id: str, user: Dict[str, str] = Depends(current_user)) -> Dict[str, bool]:
        """Whether the session still exists: after a restart it does not, and the page starts the conversation again instead of waiting on it."""
        find(session_id, user)
        return {"alive": True}

    @app.get("/sessions/{session_id}/events")
    async def events(session_id: str, after: Optional[int] = Query(default=None, ge=0), last_event_id: Optional[str] = Header(default=None),
                     user: Dict[str, str] = Depends(current_user)) -> StreamingResponse:
        session = find(session_id, user)
        resume = after if after is not None else (int(last_event_id) if (last_event_id or "").isdigit() else 0)

        async def frames() -> AsyncIterator[str]:
            async for event in session.log.stream(resume, keepalive_seconds=KEEPALIVE_SECONDS):
                yield ": keepalive\n\n" if event is None else sse_frame(event)

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- saved conversations -----------------------------------------------------------------------------------------------------------------------------------

    @app.get("/conversations")
    async def conversations(user: Dict[str, str] = Depends(current_user)) -> List[Dict[str, object]]:
        return await app.state.store.run(app.state.store.list_conversations, user["id"])

    @app.get("/conversations/{conversation_id}/messages")
    async def conversation_messages(conversation_id: str, user: Dict[str, str] = Depends(current_user)) -> List[Dict[str, object]]:
        messages = await app.state.store.run(app.state.store.conversation_messages, user["id"], conversation_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return messages

    @app.delete("/conversations/{conversation_id}", dependencies=[Depends(same_origin)])
    async def delete_conversation(conversation_id: str, user: Dict[str, str] = Depends(current_user)) -> Dict[str, bool]:
        live = by_conversation.get(conversation_id)
        if live and live in sessions and sessions[live].user_id == user["id"]:
            if sessions[live].busy:
                raise HTTPException(status_code=409, detail="a reply is being written; try again in a moment")
            sessions.pop(live).log.close()
            by_conversation.pop(conversation_id, None)
        if not await app.state.store.run(app.state.store.delete_conversation, user["id"], conversation_id):
            raise HTTPException(status_code=404, detail="no such conversation")
        return {"ok": True}

    # ---- files sent with a message -----------------------------------------------------------------------------------------------------------------------------

    async def read_upload(request: Request) -> bytes:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > app.state.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"too large: at most {app.state.max_upload_bytes:,} bytes")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > app.state.max_upload_bytes:
                raise HTTPException(status_code=413, detail=f"too large: at most {app.state.max_upload_bytes:,} bytes")
        return bytes(data)

    @app.post("/attachments", dependencies=[Depends(same_origin)])
    async def read_attachment(request: Request, name: str = Query(default="", max_length=200), user: Dict[str, str] = Depends(current_user)) -> Dict[str, object]:
        """The text of a file to be sent with a message. Nothing is stored: the page keeps the text until the message is sent."""
        data = await read_upload(request)
        try:
            title, text = await asyncio.to_thread(extract_text, name, data)
        except DocumentError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return {"name": title, "text": text[:MAX_ATTACHMENT_CHARS], "chars": len(text), "truncated": len(text) > MAX_ATTACHMENT_CHARS}

    @app.get("/healthz")
    async def healthz(request: Request) -> Dict[str, object]:
        """Anyone may ask whether the service is up. The detail (tools, spending) is for the operator, on the machine itself: a request that came through the proxy gets only the first part."""
        brief: Dict[str, object] = {"ok": True}
        if request.headers.get("x-forwarded-for"):
            return brief
        db = app.state.store
        usage = await db.run(db.day_totals, utc_day()) if db is not None else {}
        return {**brief, "sessions": len(sessions), "turns_in_progress": sum(1 for s in sessions.values() if s.busy), "tools": app.state.tools, "tools_off": app.state.tool_notes,
                "usage_today": usage}

    if WEB_DIST.is_dir():                         # the built frontend, when there is one
        app.mount("/", WebFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
