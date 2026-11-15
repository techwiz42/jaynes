"""The ceilings that make a public service affordable, and what faces the internet: security headers, the proxy, the size of a request."""
import asyncio
import sqlite3

import httpx
import pytest

from bicameral.config import ConfigError, Settings
from bicameral.limits import Limits, estimate_tokens, seconds_until_utc_midnight, utc_day
from bicameral.orchestrator import Session
from bicameral.store import SCHEMA_VERSION, Store

from .fakes import OK, FakeActor, FakeObserver
from .test_users_api import PASSWORD, browser, chat, join, make, wait_for


def test_tokens_are_estimated_from_characters_and_a_day_is_a_utc_day():
    assert estimate_tokens(0) == 0 and estimate_tokens(1) == 1 and estimate_tokens(4) == 1 and estimate_tokens(5) == 2
    assert utc_day(0) == "1970-01-01" and utc_day(86400 * 365) == "1971-01-01"
    assert seconds_until_utc_midnight(86400 * 3 + 86300) == 100


def test_the_ceilings_come_from_the_environment_and_zero_turns_one_off():
    env = {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"}
    s = Settings.from_env(env)
    assert (s.max_messages_per_hour, s.max_user_tokens_per_day, s.max_global_tokens_per_day, s.max_new_accounts_per_day) == (30, 300_000, 3_000_000, 50)
    s = Settings.from_env({**env, "MAX_MESSAGES_PER_HOUR": "0", "MAX_GLOBAL_TOKENS_PER_DAY": "10"})
    assert s.max_messages_per_hour == 0 and s.max_global_tokens_per_day == 10
    with pytest.raises(ConfigError):
        Settings.from_env({**env, "MAX_USER_TOKENS_PER_DAY": "lots"})


def test_spending_is_counted_per_user_and_day_and_survives_a_deleted_account(tmp_path):
    store = Store(str(tmp_path / "j.db"))
    a, b = store.create_user("a@example.com", "h"), store.create_user("b@example.com", "h")
    store.add_usage(a, "2026-01-01", 100)
    store.add_usage(a, "2026-01-01", 50)
    store.add_usage(b, "2026-01-01", 7)
    store.add_usage(a, "2026-01-02", 1)
    assert store.user_tokens(a, "2026-01-01") == 150 and store.user_tokens(b, "2026-01-01") == 7
    assert store.day_totals("2026-01-01") == {"tokens": 157, "messages": 3, "users": 2}
    store.delete_user(a)
    assert store.user_tokens(a, "2026-01-01") == 0
    assert store.day_totals("2026-01-01")["tokens"] == 157                 # the day's total does not shrink when someone leaves


def test_a_version_2_database_gains_the_usage_table(tmp_path):
    path = tmp_path / "j.db"
    Store(str(path))
    with sqlite3.connect(path) as db:
        db.executescript("DROP TABLE usage; PRAGMA user_version = 2;")
    store = Store(str(path))
    store.add_usage("u", "2026-01-01", 5)
    assert store.day_totals("2026-01-01")["tokens"] == 5
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


async def test_a_turn_reports_what_it_cost_even_when_it_fails():
    spent = []

    async def spend(tokens):
        spent.append(tokens)

    session = Session("s", FakeActor("a reply of some length"), FakeObserver(OK), spend=spend)
    await session.handle_user_message("x" * 4000)
    assert len(spent) == 1 and spent[0] >= 1000 + 5                         # the question was read by the actor and the observer, the reply was written
    broken = Session("s", FakeActor(), FakeObserver(OK), spend=spend)       # an actor with nothing to say fails the turn
    await broken.handle_user_message("hi")
    assert len(spent) == 2


async def test_messages_per_hour_are_limited_per_user():
    app, _ = make(limits=Limits(messages_per_hour=2, user_tokens_per_day=0, global_tokens_per_day=0, new_accounts_per_day=0))
    async with browser(app) as alice, browser(app) as bob:
        await join(alice, "alice@example.com")
        await join(bob, "bob@example.com")
        session_id = await chat(app, alice, "one")
        await chat(app, alice, "two", session_id)
        refused = await alice.post(f"/sessions/{session_id}/messages", json={"text": "three"})
        assert refused.status_code == 429 and "too fast" in refused.json()["detail"] and int(refused.headers["retry-after"]) > 0
        await chat(app, bob, "bob is not held up by alice")


async def test_a_user_who_has_used_the_days_allowance_is_stopped_and_others_are_not():
    app, store = make(limits=Limits(messages_per_hour=0, user_tokens_per_day=100, global_tokens_per_day=0, new_accounts_per_day=0))
    async with browser(app) as alice, browser(app) as bob:
        await join(alice, "alice@example.com")
        await join(bob, "bob@example.com")
        session_id = await chat(app, alice, "hello " * 200)                 # about 300+ tokens in and out
        await wait_for(lambda: store.user_tokens(store.user_by_email("alice@example.com")["id"], utc_day()) > 100, "the spending to be counted")
        refused = await alice.post(f"/sessions/{session_id}/messages", json={"text": "more"})
        assert refused.status_code == 429 and "allowance" in refused.json()["detail"]
        assert (await bob.post("/sessions")).status_code == 200


async def test_when_everyone_together_has_used_the_days_budget_jaynes_rests():
    app, store = make(limits=Limits(messages_per_hour=0, user_tokens_per_day=0, global_tokens_per_day=50, new_accounts_per_day=0))
    async with browser(app) as http:
        await join(http, "a@example.com")
        session_id = (await http.post("/sessions")).json()["session_id"]
        store.add_usage("someone", utc_day(), 60)
        refused = await http.post(f"/sessions/{session_id}/messages", json={"text": "hi"})
        assert refused.status_code == 503 and "resting" in refused.json()["detail"] and "retry-after" in refused.headers


async def test_new_accounts_are_capped_per_day():
    app, _ = make(limits=Limits(messages_per_hour=0, user_tokens_per_day=0, global_tokens_per_day=0, new_accounts_per_day=2))
    async with browser(app) as one, browser(app) as two, browser(app) as three:
        await join(one, "one@example.com")
        await join(two, "two@example.com")
        refused = await three.post("/auth/register", json={"email": "three@example.com", "password": PASSWORD})
        assert refused.status_code == 503 and "closed for today" in refused.json()["detail"]


async def test_without_limits_nothing_is_held_back():
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        session_id = await chat(app, http, "one")
        for text in ("two", "three", "four"):
            await chat(app, http, text, session_id)


async def test_every_answer_carries_the_security_headers_and_https_adds_hsts():
    app, _ = make()
    async with browser(app) as http:
        headers = (await http.get("/healthz")).headers
        assert "default-src 'self'" in headers["content-security-policy"] and "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
        assert "strict-transport-security" not in headers
    secure, _ = make(cookie_secure=True)
    async with browser(secure) as http:
        assert "max-age" in (await http.get("/healthz")).headers["strict-transport-security"]


async def test_an_oversized_request_is_refused_before_it_is_read():
    app, _ = make(max_upload_bytes=1000)
    async with browser(app) as http:
        response = await http.post("/auth/login", content=b"x" * 2_000_000, headers={"content-type": "application/json"})
        assert response.status_code == 413 and "content-security-policy" in response.headers


async def test_through_the_proxy_healthz_says_only_that_it_is_up():
    app, _ = make()
    async with browser(app) as http:
        assert (await http.get("/healthz", headers={"x-forwarded-for": "203.0.113.9"})).json() == {"ok": True}
        assert "usage_today" in (await http.get("/healthz")).json()
