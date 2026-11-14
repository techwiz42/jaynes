import asyncio

import httpx
import pytest

from bicameral.server import create_app

from .fakes import OK, RETRACT, FakeActor, FakeObserver


def client(actor, observer):
    app = create_app(actor, observer)
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def settle(app, session_id):
    session = app.state.sessions[session_id]
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not session.busy and len(session.log.replay()) > 1:
            return session
    raise AssertionError("the turn did not finish")


async def test_a_message_is_accepted_with_202_and_the_events_follow():
    app, http = client(FakeActor("Hello"), FakeObserver(OK))
    async with http:
        session_id = (await http.post("/sessions")).json()["session_id"]
        response = await http.post(f"/sessions/{session_id}/messages", json={"text": "hi"})
        assert response.status_code == 202
        session = await settle(app, session_id)
        session.log.close()
        body = (await http.get(f"/sessions/{session_id}/events")).text
    assert "event: user_message" in body and "event: token" in body and "event: observer_verdict" in body
    assert body.index("id: 1\n") < body.index("id: 2\n")


async def test_events_resume_after_an_id_from_the_query_or_the_header():
    app, http = client(FakeActor("Hello"), FakeObserver(OK))
    async with http:
        session_id = (await http.post("/sessions")).json()["session_id"]
        await http.post(f"/sessions/{session_id}/messages", json={"text": "hi"})
        session = await settle(app, session_id)
        session.log.close()
        by_query = (await http.get(f"/sessions/{session_id}/events", params={"after": 2})).text
        by_header = (await http.get(f"/sessions/{session_id}/events", headers={"Last-Event-ID": "2"})).text
    for body in (by_query, by_header):
        assert "id: 1\n" not in body and "id: 2\n" not in body and "id: 3\n" in body


async def test_a_retraction_shows_up_on_the_wire():
    app, http = client(FakeActor("wrong", "right"), FakeObserver(RETRACT))
    async with http:
        session_id = (await http.post("/sessions")).json()["session_id"]
        await http.post(f"/sessions/{session_id}/messages", json={"text": "when?"})
        for _ in range(100):
            await asyncio.sleep(0.01)
            if any(e.type == "draft_complete" and e.data["message_id"] == "m2" for e in app.state.sessions[session_id].log.replay()):
                break
        app.state.sessions[session_id].log.close()
        body = (await http.get(f"/sessions/{session_id}/events")).text
    assert "event: retraction" in body and '"kind": "correction"' in body


async def test_bad_requests():
    app, http = client(FakeActor(), FakeObserver())
    async with http:
        assert (await http.post("/sessions/nope/messages", json={"text": "hi"})).status_code == 404
        assert (await http.get("/sessions/nope/events")).status_code == 404
        session_id = (await http.post("/sessions")).json()["session_id"]
        assert (await http.post(f"/sessions/{session_id}/messages", json={"text": ""})).status_code == 422
        assert (await http.post(f"/sessions/{session_id}/messages", json={"text": "x" * 8001})).status_code == 422


async def test_healthz_counts_sessions_and_turns():
    app, http = client(FakeActor(), FakeObserver())
    async with http:
        await http.post("/sessions")
        assert (await http.get("/healthz")).json() == {"ok": True, "sessions": 1, "turns_in_progress": 0, "tools": []}
