import asyncio

from bicameral.events import EventLog


def test_ids_count_up_and_replay_starts_after_an_id():
    log = EventLog()
    for text in "abc":
        log.append("token", text=text)
    assert [e.id for e in log.replay()] == [1, 2, 3]
    assert [e.data["text"] for e in log.replay(after=1)] == ["b", "c"]
    assert log.replay(after=3) == []


def test_the_wire_form_is_flat():
    log = EventLog()
    assert log.append("user_message", text="hi").flat() == {"id": 1, "type": "user_message", "text": "hi"}


async def test_a_listener_gets_the_backlog_then_live_events_without_repeats_or_gaps():
    log = EventLog()
    log.append("token", text="1")
    log.append("token", text="2")
    seen = []

    async def listen():
        async for event in log.stream(after=1):
            seen.append(event.id)

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    log.append("token", text="3")
    log.append("token", text="4")
    await asyncio.sleep(0)
    log.close()
    await asyncio.wait_for(task, 1)
    assert seen == [2, 3, 4]


async def test_reconnecting_mid_stream_loses_and_repeats_nothing():
    log = EventLog()
    for i in range(5):
        log.append("token", text=str(i))
    first = []
    async for event in log.stream(after=0):
        first.append(event.id)
        if event.id == 3:
            break
    log.append("token", text="5")
    log.close()
    second = [e.id async for e in log.stream(after=first[-1])]
    assert first + second == [1, 2, 3, 4, 5, 6]


async def test_two_listeners_each_get_everything():
    log = EventLog()
    results = [[], []]

    async def listen(i):
        async for event in log.stream():
            results[i].append(event.id)

    tasks = [asyncio.create_task(listen(i)) for i in range(2)]
    await asyncio.sleep(0)
    log.append("token", text="a")
    log.append("token", text="b")
    await asyncio.sleep(0)
    log.close()
    await asyncio.gather(*tasks)
    assert results == [[1, 2], [1, 2]]


async def test_a_quiet_stream_yields_keepalives():
    log = EventLog()
    stream = log.stream(keepalive_seconds=0.01)
    assert await stream.__anext__() is None
    await stream.aclose()
