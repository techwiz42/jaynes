import asyncio
import os
import tempfile

import httpx
import pytest

from bicameral.server import create_app
from bicameral.store import Store

from .fakes import OK, RETRACT, FakeActor, FakeObserver

PASSWORD = "correct horse battery"


def make(actor=None, observer=None, **kwargs):
    store = Store(os.path.join(tempfile.mkdtemp(), "jaynes.db"))
    app = create_app(actor or FakeActor("Hello"), observer or FakeObserver(OK, OK, OK, OK), store, **kwargs)
    return app, store


def browser(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def join(http, email):
    response = await http.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response


async def wait_for(condition, what="the condition"):
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def chat(app, http, text, session_id=None):
    if session_id is None:
        session_id = (await http.post("/sessions")).json()["session_id"]
    session = app.state.sessions[session_id]
    before = len(session.log.replay())
    assert (await http.post(f"/sessions/{session_id}/messages", json={"text": text})).status_code == 202
    await wait_for(lambda: not session.busy and len(session.log.replay()) > before, "the turn")
    return session_id


# ---- accounts ---------------------------------------------------------------------------------------------------------------------------------------------

async def test_registering_logs_you_in_with_a_private_cookie():
    app, _ = make()
    async with browser(app) as http:
        response = await join(http, "Pat@Example.com")
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
        assert (await http.get("/auth/me")).json() == {"email": "pat@example.com"}


async def test_the_cookie_is_marked_secure_when_configured():
    app, _ = make(cookie_secure=True)
    async with browser(app) as http:
        assert "secure" in (await join(http, "a@example.com")).headers["set-cookie"].lower()


async def test_nothing_works_without_a_login():
    app, _ = make()
    async with browser(app) as http:
        assert (await http.get("/auth/me")).status_code == 401
        assert (await http.post("/sessions")).status_code == 401
        assert (await http.get("/conversations")).status_code == 401
        assert (await http.post("/attachments", params={"name": "a.txt"}, content=b"hi")).status_code == 401
        assert (await http.get("/sessions/x/events")).status_code == 401
        assert (await http.get("/healthz")).status_code == 200                      # the one open route, and it names no user
        http.cookies.set("jaynes_session", "not-a-real-token")
        assert (await http.get("/auth/me")).status_code == 401


async def test_logging_out_ends_the_login():
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        token = http.cookies.get("jaynes_session")
        assert (await http.post("/auth/logout")).status_code == 200
        http.cookies.set("jaynes_session", token)                                    # a stolen copy of the old cookie is dead too
        assert (await http.get("/auth/me")).status_code == 401


async def test_login_checks_the_password_and_does_not_reveal_which_part_was_wrong():
    app, _ = make()
    async with browser(app) as first:
        await join(first, "a@example.com")
    async with browser(app) as http:
        wrong_password = await http.post("/auth/login", json={"email": "a@example.com", "password": "not the password"})
        unknown_email = await http.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
        assert wrong_password.status_code == unknown_email.status_code == 401 and wrong_password.json() == unknown_email.json()
        assert (await http.post("/auth/login", json={"email": "A@Example.com", "password": PASSWORD})).status_code == 200
        assert (await http.get("/auth/me")).json() == {"email": "a@example.com"}


async def test_repeated_wrong_passwords_are_throttled_even_for_the_right_one():
    app, _ = make()
    async with browser(app) as first:
        await join(first, "a@example.com")
    async with browser(app) as http:
        for _ in range(5):
            assert (await http.post("/auth/login", json={"email": "a@example.com", "password": "wrong wrong wrong"})).status_code == 401
        blocked = await http.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD})
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1


async def test_bad_registrations_are_refused():
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
    async with browser(app) as http:
        assert (await http.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})).status_code == 409
        assert (await http.post("/auth/register", json={"email": "not-an-email", "password": PASSWORD})).status_code == 422
        assert (await http.post("/auth/register", json={"email": "b@example.com", "password": "short"})).status_code == 422


async def test_a_page_on_another_origin_cannot_act_for_the_user():
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        evil = {"origin": "http://localhost:9999"}
        assert (await http.post("/sessions", headers=evil)).status_code == 403
        assert (await http.post("/auth/logout", headers=evil)).status_code == 403
        assert (await http.post("/attachments", params={"name": "a.txt"}, content=b"hi", headers=evil)).status_code == 403
        assert (await http.post("/sessions", headers={"origin": "http://test"})).status_code == 200


async def test_deleting_the_account_removes_the_data_and_needs_the_password():
    app, store = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        await chat(app, http, "the vault code is 4417")
        assert (await http.post("/auth/delete-account", json={"password": "wrong wrong wrong"})).status_code == 401
        assert (await http.post("/auth/delete-account", json={"password": PASSWORD})).status_code == 200
        assert (await http.get("/auth/me")).status_code == 401
    assert store.count_users() == 0


# ---- one user cannot reach another's anything -------------------------------------------------------------------------------------------------------------

async def test_users_cannot_see_or_use_each_others_sessions_or_conversations():
    app, _ = make()
    async with browser(app) as alice, browser(app) as bob:
        await join(alice, "alice@example.com")
        await join(bob, "bob@example.com")
        session_id = await chat(app, alice, "my private plan")
        conversation_id = (await alice.get("/conversations")).json()[0]["id"]

        assert (await bob.get(f"/sessions/{session_id}/events")).status_code == 404
        assert (await bob.post(f"/sessions/{session_id}/messages", json={"text": "hi"})).status_code == 404
        assert (await bob.get("/conversations")).json() == []
        assert (await bob.get(f"/conversations/{conversation_id}/messages")).status_code == 404
        assert (await bob.delete(f"/conversations/{conversation_id}")).status_code == 404
        assert (await bob.post("/sessions", json={"conversation_id": conversation_id})).status_code == 404

        assert len((await alice.get(f"/conversations/{conversation_id}/messages")).json()) == 2        # and it was all untouched


# ---- conversations are kept ----------------------------------------------------------------------------------------------------------------------------------

async def test_a_conversation_and_its_approved_replies_are_saved_and_can_be_continued():
    actor = FakeActor("Hello there", "Second answer")
    app, _ = make(actor)
    async with browser(app) as http:
        await join(http, "a@example.com")
        session_id = await chat(app, http, "first question")
        conversation_id = (await http.get("/conversations")).json()[0]["id"]
        messages = (await http.get(f"/conversations/{conversation_id}/messages")).json()
        assert [(m["role"], m["text"]) for m in messages] == [("user", "first question"), ("assistant", "Hello there")]
        assert (await http.get("/conversations")).json()[0]["title"] == "first question"

        again = await http.post("/sessions", json={"conversation_id": conversation_id})
        assert again.json() == {"session_id": session_id, "conversation_id": conversation_id, "live": True}          # a live one is not duplicated, and the page is told so

        app.state.sessions.clear()                                                   # as after a restart: nothing live, everything saved
        resumed = await http.post("/sessions", json={"conversation_id": conversation_id})
        assert resumed.json()["conversation_id"] == conversation_id and resumed.json()["session_id"] != session_id and resumed.json()["live"] is False
        await chat(app, http, "second question", resumed.json()["session_id"])
        sent = actor.calls[-1]
        assert [m["content"] for m in sent] == ["first question", "Hello there", "second question"]       # the earlier turns came back to the model


async def test_a_continued_conversation_shows_its_saved_messages_whether_or_not_the_session_is_live():
    app, _ = make(FakeActor("Hello there", "Second answer"))
    async with browser(app) as http:
        await join(http, "a@example.com")
        await chat(app, http, "first question")
        conversation_id = (await http.get("/conversations")).json()[0]["id"]
        app.state.sessions.clear()                                                   # as after a restart
        resumed = (await http.post("/sessions", json={"conversation_id": conversation_id})).json()
        await chat(app, http, "second question", resumed["session_id"])
        reopened = (await http.post("/sessions", json={"conversation_id": conversation_id})).json()
        assert reopened["live"] is True and reopened["session_id"] == resumed["session_id"]
        shown = [(e.type, e.data.get("text")) for e in app.state.sessions[reopened["session_id"]].log.replay() if e.type in ("user_message", "draft_complete")]
    assert shown == [("user_message", "first question"), ("draft_complete", "Hello there"), ("user_message", "second question"), ("draft_complete", "Second answer")]


async def test_the_page_can_ask_whether_a_session_still_exists():
    app, _ = make()
    async with browser(app) as alice, browser(app) as bob:
        await join(alice, "a@example.com")
        await join(bob, "b@example.com")
        session_id = (await alice.post("/sessions")).json()["session_id"]
        assert (await alice.get(f"/sessions/{session_id}/alive")).json() == {"alive": True}
        assert (await bob.get(f"/sessions/{session_id}/alive")).status_code == 404          # another user's session looks like one that does not exist
        app.state.sessions.clear()                                                           # as after a restart
        assert (await alice.get(f"/sessions/{session_id}/alive")).status_code == 404


async def test_a_reply_the_observer_did_not_approve_is_not_kept():
    app, _ = make(FakeActor("wrong", "right"), FakeObserver(RETRACT, OK))
    async with browser(app) as http:
        await join(http, "a@example.com")
        await chat(app, http, "when?")
        conversation_id = (await http.get("/conversations")).json()[0]["id"]
        texts = [m["text"] for m in (await http.get(f"/conversations/{conversation_id}/messages")).json()]
    assert texts == ["when?", "right"]


async def test_deleting_a_conversation_removes_it():
    app, store = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        await chat(app, http, "lighthouse keeper")
        conversation_id = (await http.get("/conversations")).json()[0]["id"]
        assert (await http.delete(f"/conversations/{conversation_id}")).status_code == 200
        assert (await http.get("/conversations")).json() == []


# ---- files sent with a message -------------------------------------------------------------------------------------------------------------------------------------

async def test_bad_files_are_refused_with_a_reason():
    app, _ = make(max_upload_bytes=1000)
    async with browser(app) as http:
        await join(http, "a@example.com")
        assert (await http.post("/attachments", params={"name": "big.txt"}, content=b"x" * 1001)).status_code == 413
        assert (await http.post("/attachments", params={"name": "pic.png"}, content=b"\x89PNG....")).status_code == 422
        assert (await http.post("/attachments", params={"name": "bin.txt"}, content=b"ab\x00cd")).status_code == 422
        assert (await http.post("/attachments", params={"name": "empty.txt"}, content=b"   ")).status_code == 422
        broken = await http.post("/attachments", params={"name": "x.pdf"}, content=b"%PDF-1.4 this is not really a pdf")
        assert broken.status_code == 422 and "PDF" in broken.json()["detail"]


async def test_a_pdf_without_text_is_said_to_be_unreadable():
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        response = await http.post("/attachments", params={"name": "blank.pdf"}, content=buffer.getvalue())
        assert response.status_code == 422 and "no text" in response.json()["detail"]


async def test_opening_the_page_leaves_nothing_saved_until_a_message_is_sent():
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        started = (await http.post("/sessions")).json()
        assert (await http.get("/conversations")).json() == []                              # no empty "New conversation" rows
        await chat(app, http, "now I say something", started["session_id"])
        listed = (await http.get("/conversations")).json()
        assert [c["id"] for c in listed] == [started["conversation_id"]] and listed[0]["title"] == "now I say something"


async def test_a_conversation_id_that_belongs_to_someone_else_cannot_be_written_into():
    app, store = make()
    async with browser(app) as alice, browser(app) as bob:
        await join(alice, "alice@example.com")
        await join(bob, "bob@example.com")
        await chat(app, alice, "alice was here")
        alice_id = store.user_by_email("alice@example.com")["id"]
        bob_id = store.user_by_email("bob@example.com")["id"]
        conversation_id = store.list_conversations(alice_id)[0]["id"]
        assert store.add_message(bob_id, conversation_id, "user", "intruder", create=True) is False      # even asking for it to be created
        assert [m["text"] for m in store.conversation_messages(alice_id, conversation_id)] == ["alice was here", "Hello"]
