"""Regression tests for the Twitch EventSub wiring.

These cover the failure modes that silently killed stream update / stream end
notifications while the polling ``update`` task kept the planning message and
the Discord scheduled event looking healthy.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytz
from twitchAPI.type import EventSubSubscriptionConflict

from extensions.twitch import TwitchExtension
from extensions.twitch import eventsub as eventsub_module
from extensions.twitch._common import StreamerInfo

GUILD_ID = 809125340280520724
NOTIF_CHANNEL_ID = "809134370510209054"


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs.get("embed", args))
        return SimpleNamespace(id=1)


class FakeBot:
    user = SimpleNamespace(id=999)

    async def fetch_member(self, user_id, guild_id):
        return SimpleNamespace(display_name="Michel", avatar_url="http://avatar")

    async def fetch_channel(self, channel_id):
        return FakeChannel()


async def _agen(items):
    for item in items:
        yield item


class FakeTwitch:
    """Minimal stand-in for the twitchAPI client used by the extension."""

    def __init__(self, live: bool = False) -> None:
        self.live = live

    def get_users(self, user_ids=None, logins=None, **kwargs):
        login = (logins or ["zerator"])[0]
        return _agen(
            [
                SimpleNamespace(
                    id="12345",
                    login=login,
                    display_name=login,
                    profile_image_url="http://img",
                    offline_image_url="http://offline",
                )
            ]
        )

    def get_streams(self, user_id=None, **kwargs):
        stream = SimpleNamespace(
            id="stream-1",
            user_id="12345",
            user_login="zerator",
            title="Titre",
            game_name="Jeu",
            viewer_count=42,
            thumbnail_url="http://thumb/{width}x{height}",
            started_at=datetime.now(pytz.UTC),
        )
        return _agen([stream] if self.live else [])

    def get_videos(self, **kwargs):
        return _agen([])


class FakeEventSub:
    """Records subscriptions; optionally fails on chosen topics."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._failing = failing or set()

    def _listener(self, topic: str):
        async def listen(broadcaster_user_id: str, callback):
            if topic in self._failing:
                raise EventSubSubscriptionConflict("subscription already exists")
            self.calls.append((topic, broadcaster_user_id))
            return f"{topic}-id"

        return listen

    def __getattr__(self, name: str):
        if name.startswith("listen_"):
            return self._listener(name)
        raise AttributeError(name)


class _FakeAuthHelper:
    def __init__(self, **kwargs) -> None:
        pass

    async def bind(self) -> None:
        return None


def _fake_twitch_factory():
    """Stand-in for ``await Twitch(client_id, client_secret)``."""

    async def factory(client_id, client_secret, **kwargs):
        return FakeTwitch()

    return factory


async def _noop(*args, **kwargs) -> None:
    return None


def make_streamer(streamer_id: str = "zerator", guild_id: int = GUILD_ID, **overrides):
    config = {
        "twitchNotificationChannelId": NOTIF_CHANNEL_ID,
        "notifyStreamUpdate": True,
        "notifyStreamEnd": True,
        **overrides,
    }
    streamer = StreamerInfo(guild_id=guild_id, streamer_id=streamer_id, config=config)
    streamer.user_id = "12345"
    streamer.notif_channel = FakeChannel()
    return streamer


def make_extension(streamers=None, live: bool = False) -> TwitchExtension:
    """Build an extension instance without touching Discord or the config file."""
    ext = object.__new__(TwitchExtension)
    ext.bot = FakeBot()
    ext.twitch = FakeTwitch(live=live)
    ext.eventsub = None
    ext.stop = False
    ext._run_lock = asyncio.Lock()
    ext.enabled_servers = [str(GUILD_ID)]
    ext.timezone = pytz.timezone("Europe/Paris")
    ext.streamers = {f"{s.guild_id}_{s.streamer_id}": s for s in (streamers or [make_streamer()])}
    return ext


# --------------------------------------------------------------------------
# Websocket liveness
# --------------------------------------------------------------------------


def _fake_socket(receive_done: bool):
    task = SimpleNamespace(done=lambda: receive_done)
    return SimpleNamespace(
        _running=True,
        active_session=SimpleNamespace(id="session-1"),
        _tasks=[task],
    )


def test_eventsub_is_alive_when_receive_loop_runs():
    ext = make_extension()
    ext.eventsub = _fake_socket(receive_done=False)
    assert ext.eventsub_is_alive() is True


def test_eventsub_is_dead_when_receive_loop_finished():
    """twitchAPI leaves _running/active_session set after its receive loop breaks."""
    ext = make_extension()
    ext.eventsub = _fake_socket(receive_done=True)
    assert ext.eventsub_is_alive() is False


def test_eventsub_is_dead_without_socket():
    ext = make_extension()
    assert ext.eventsub_is_alive() is False


def _record_restarts(ext) -> list:
    restarts: list = []

    async def fake_run():
        restarts.append(True)

    ext.run = fake_run
    return restarts


async def test_watchdog_restarts_a_dead_socket():
    ext = make_extension()
    ext.eventsub = _fake_socket(receive_done=True)
    restarts = _record_restarts(ext)

    await TwitchExtension.eventsub_watchdog.callback(ext)
    assert restarts == [True]


async def test_watchdog_leaves_a_healthy_socket_alone():
    ext = make_extension()
    ext.eventsub = _fake_socket(receive_done=False)
    restarts = _record_restarts(ext)

    await TwitchExtension.eventsub_watchdog.callback(ext)
    assert restarts == []


# --------------------------------------------------------------------------
# Subscription registration
# --------------------------------------------------------------------------


async def test_all_three_topics_are_subscribed():
    ext = make_extension()
    ext.eventsub = FakeEventSub()

    await ext._subscribe_streamer("12345", "zerator")

    assert [topic for topic, _ in ext.eventsub.calls] == [
        "listen_stream_online",
        "listen_stream_offline",
        "listen_channel_update_v2",
    ]


async def test_a_failing_topic_does_not_skip_the_others():
    """stream.online is subscribed first; its failure used to drop offline+update."""
    ext = make_extension()
    ext.eventsub = FakeEventSub(failing={"listen_stream_online"})

    await ext._subscribe_streamer("12345", "zerator")

    assert [topic for topic, _ in ext.eventsub.calls] == [
        "listen_stream_offline",
        "listen_channel_update_v2",
    ]


async def test_start_eventsub_subscribes_a_shared_streamer_once(monkeypatch):
    """Two guilds following the same login must not double-subscribe.

    Twitch answers a duplicate subscription with a 409, and that used to abort
    the remaining topics for the streamer.
    """
    ext = make_extension(streamers=[make_streamer(guild_id=GUILD_ID), make_streamer(guild_id=1234)])
    for streamer in ext.streamers.values():
        streamer.user_id = None

    socket = FakeEventSub()
    socket.start = lambda: None
    monkeypatch.setattr(eventsub_module, "EventSubWebsocket", lambda *a, **kw: socket)
    monkeypatch.setattr(eventsub_module, "Twitch", _fake_twitch_factory())
    monkeypatch.setattr(eventsub_module, "UserAuthenticationStorageHelper", _FakeAuthHelper)
    ext.update = _noop
    ext.client_id = "id"
    ext.client_secret = "secret"

    await ext._start_eventsub()

    assert socket.calls == [
        ("listen_stream_online", "12345"),
        ("listen_stream_offline", "12345"),
        ("listen_channel_update_v2", "12345"),
    ]
    # Both guild entries still get their broadcaster id resolved.
    assert {s.user_id for s in ext.streamers.values()} == {"12345"}


async def test_run_collapses_concurrent_calls():
    ext = make_extension()
    starts = []

    async def slow_start():
        starts.append(True)
        await asyncio.sleep(0.05)

    ext._start_eventsub = slow_start
    await asyncio.gather(ext.run(), ext.run())
    assert starts == [True]


def test_revocation_handler_is_awaitable_by_twitchapi():
    """twitchAPI awaits revocation_handler(payload) directly.

    An ``@listen()`` decorator turned this into an unbound Listener, so the
    restart raised TypeError instead of reconnecting.
    """
    ext = make_extension()
    assert asyncio.iscoroutinefunction(ext.on_revocation)


# --------------------------------------------------------------------------
# Startup hydration
# --------------------------------------------------------------------------


async def test_planning_failure_still_resolves_the_notification_channel():
    """A broken planning message used to abort hydration for that streamer.

    notif_channel then stayed None and every notification was dropped silently.
    """
    ext = make_extension(streamers=[make_streamer(twitchPlanningChannelId="123")])
    streamer = next(iter(ext.streamers.values()))
    streamer.notif_channel = None
    ext._load_streamer_states = _noop
    ext._match_scheduled_event = lambda *a: None

    async def boom(_streamer):
        raise RuntimeError("missing permissions in the planning channel")

    ext._hydrate_planning_message = boom
    ext.bot.fetch_guild = _noop

    await ext._hydrate_streamers()

    assert streamer.notif_channel is not None


# --------------------------------------------------------------------------
# Notification delivery
# --------------------------------------------------------------------------


async def test_stream_end_notification_is_sent():
    ext = make_extension()
    streamer = next(iter(ext.streamers.values()))
    streamer.stream_start_time = datetime.now(pytz.UTC) - timedelta(hours=2)
    streamer.stream_title = "Un titre"
    streamer.stream_categories = ["Jeu A", "Jeu B"]

    await ext.send_stream_end_notification(streamer, "ZeratoR")

    assert len(streamer.notif_channel.sent) == 1
    embed = streamer.notif_channel.sent[0]
    assert embed.title == "ZeratoR a terminé son live"
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Durée"] == "2h 0min"
    assert fields["Catégories"] == "• Jeu A\n• Jeu B"


async def test_stream_end_notification_respects_the_toggle():
    ext = make_extension(streamers=[make_streamer(notifyStreamEnd=False)])
    streamer = next(iter(ext.streamers.values()))

    await ext.send_stream_end_notification(streamer, "ZeratoR")

    assert streamer.notif_channel.sent == []


async def test_stream_end_notification_needs_a_channel():
    ext = make_extension()
    streamer = next(iter(ext.streamers.values()))
    streamer.notif_channel = None

    await ext.send_stream_end_notification(streamer, "ZeratoR")  # must not raise
