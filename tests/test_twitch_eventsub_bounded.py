"""EventSub starts and stops without blocking the bot loop (an unreachable Twitch froze it)."""

import asyncio
import time

import aiohttp
import pytest

from src.integrations.twitch_eventsub import BoundedEventSubWebsocket, EventSubStartError


class FakeTwitch:
    session_timeout = aiohttp.ClientTimeout(total=5)

    def has_required_auth(self, *args):
        return True


def unreachable_socket(steps: list[int]) -> BoundedEventSubWebsocket:
    socket = BoundedEventSubWebsocket(FakeTwitch(), connection_url="ws://127.0.0.1:9/")
    socket.reconnect_delay_steps = steps
    return socket


def wait_for_thread_end(socket: BoundedEventSubWebsocket) -> None:
    socket._socket_thread.join(timeout=5)
    assert not socket._socket_thread.is_alive()
    assert socket._socket_loop.is_closed()


async def test_a_late_welcome_times_out_without_freezing_the_loop():
    socket = unreachable_socket([5, 5, 5])  # retries far longer than the deadline
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    started = time.monotonic()
    with pytest.raises(EventSubStartError):
        await socket.start_async(timeout=0.3)
    task.cancel()
    assert time.monotonic() - started < 2
    assert ticks >= 10  # the loop kept running while EventSub was connecting
    assert socket._running is False
    await asyncio.to_thread(wait_for_thread_end, socket)
    await socket.stop_async()  # nothing left to stop


async def test_a_dead_socket_thread_fails_the_start_at_once():
    socket = unreachable_socket([0])  # one failed attempt, then the library gives up
    started = time.monotonic()
    with pytest.raises(EventSubStartError):
        await socket.start_async(timeout=30)
    assert time.monotonic() - started < 5
    await asyncio.to_thread(wait_for_thread_end, socket)


async def test_stop_before_any_start_is_a_no_op():
    await unreachable_socket([0]).stop_async()


async def test_a_second_start_is_refused():
    socket = unreachable_socket([5, 5])
    task = asyncio.create_task(socket.start_async(timeout=1))
    await asyncio.sleep(0.05)
    with pytest.raises(RuntimeError):
        await socket.start_async()
    with pytest.raises(EventSubStartError):
        await task
    await asyncio.to_thread(wait_for_thread_end, socket)


async def test_a_welcomed_socket_starts_and_stops_within_its_deadline(unused_tcp_port):
    from aiohttp import web

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        welcome = {"id": "s1", "keepalive_timeout_seconds": 30, "status": "connected"}
        await ws.send_json(
            {"metadata": {"message_type": "session_welcome"}, "payload": {"session": welcome}}
        )
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    try:
        socket = BoundedEventSubWebsocket(
            FakeTwitch(), connection_url=f"ws://127.0.0.1:{unused_tcp_port}/"
        )
        await socket.start_async(timeout=5)
        assert socket.active_session.id == "s1"
        await socket.stop_async(timeout=5)
        await asyncio.to_thread(wait_for_thread_end, socket)
        assert socket._running is False
    finally:
        await runner.cleanup()


# --------------------------------------------------------------------------
# Reconnect resubscription (Twitch's websocket cost cap)
# --------------------------------------------------------------------------


class _Pages:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self._items:
            yield item


class SubscriptionTwitch(FakeTwitch):
    def __init__(self, subs):
        self.subs = subs
        self.deleted: list[str] = []

    async def get_eventsub_subscriptions(self, **kwargs):
        return _Pages(self.subs)

    async def delete_eventsub_subscription(self, subscription_id, target_token=None):
        self.deleted.append(subscription_id)


def _sub(sub_id, session_id, method="websocket"):
    from types import SimpleNamespace

    return SimpleNamespace(id=sub_id, transport={"method": method, "session_id": session_id})


def _socket_on(twitch, session_id="new"):
    from types import SimpleNamespace

    socket = BoundedEventSubWebsocket(twitch, connection_url="ws://127.0.0.1:9/")
    socket.active_session = SimpleNamespace(id=session_id)
    return socket


async def test_purge_deletes_only_other_sessions_websocket_subscriptions():
    twitch = SubscriptionTwitch(
        [_sub("old-1", "old"), _sub("cur-1", "new"), _sub("hook", None, method="webhook")]
    )
    assert await _socket_on(twitch).purge_stale_subscriptions() == 1
    assert twitch.deleted == ["old-1"]


async def test_resubscribe_frees_old_sessions_before_subscribing_again():
    twitch = SubscriptionTwitch([_sub("old-1", "old"), _sub("old-2", "old")])
    socket = _socket_on(twitch)
    socket._active_subscriptions = {"old-1": {"sub_type": "a"}, "old-2": {"sub_type": "b"}}
    seen: list[tuple[str, list[str]]] = []

    async def subscribe(**sub):
        seen.append((sub["sub_type"], list(twitch.deleted)))
        socket._active_subscriptions[f"new-{sub['sub_type']}"] = sub
        return f"new-{sub['sub_type']}"

    socket._subscribe = subscribe
    await socket._resubscribe()
    assert seen == [("a", ["old-1", "old-2"]), ("b", ["old-1", "old-2"])]
    assert set(socket._active_subscriptions) == {"new-a", "new-b"}
    assert socket.resubscribe_failed is False


async def test_one_failed_resubscription_keeps_the_others_and_flags_the_socket(caplog):
    socket = _socket_on(SubscriptionTwitch([]))
    socket._active_subscriptions = {
        "old-1": {"sub_type": "a", "condition": {"broadcaster_user_id": "1"}},
        "old-2": {"sub_type": "b", "condition": {"broadcaster_user_id": "1"}},
    }

    async def subscribe(**sub):
        if sub["sub_type"] == "a":
            raise RuntimeError("websocket transport subscriptions total cost exceeded")
        socket._active_subscriptions["new-b"] = sub
        return "new-b"

    socket._subscribe = subscribe
    await socket._resubscribe()
    assert set(socket._active_subscriptions) == {"old-1", "new-b"}
    assert socket.resubscribe_failed is True
    assert "exception while resubscribing a" in caplog.text
