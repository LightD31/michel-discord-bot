"""The status trackers serve every enabled guild, each from its live settings.

Satisfactory, Streamlabs, Speedons, Olympics and Minecraft used to take the
first enabled guild's settings at import. They now read every enabled guild at
each refresh, so a dashboard save applies without a reload.
"""

import threading
from types import SimpleNamespace

import pytest


class FakeMessages:
    """Stands in for GuildMessages: one fake message per guild."""

    def __init__(self) -> None:
        self.kept: list[str] | None = None
        self.asked: list[tuple[str, dict]] = []

    async def get(self, guild_id, settings):
        self.asked.append((str(guild_id), settings))
        return f"message-{guild_id}"

    def keep_only(self, guild_ids):
        self.kept = list(guild_ids)


def _servers(module: str, **guilds: dict) -> dict:
    return {"servers": {gid: {module: cfg} for gid, cfg in guilds.items()}}


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


# ── Satisfactory ─────────────────────────────────────────────────────────


async def test_satisfactory_refreshes_each_guild_from_its_own_server(store_config, monkeypatch):
    import extensions.satisfactory as sat

    store_config(
        _servers(
            "moduleSatisfactory",
            **{
                "1": {
                    "enabled": True,
                    "satisfactoryServerIp": "a",
                    "satisfactoryServerPort": "1234",
                    "satisfactoryServerToken": "t1",
                },
                "2": {
                    "enabled": True,
                    "satisfactoryServerIp": "b",
                    "satisfactoryServerToken": "t2",
                },
                "3": {"enabled": True},  # no server yet
                "4": {
                    "enabled": False,
                    "satisfactoryServerIp": "c",
                    "satisfactoryServerToken": "t",
                },
            },
        )
    )
    clients: list[tuple] = []

    class FakeAPI:
        def __init__(self, address, token):
            clients.append((address, token, _on_main_thread()))

        def query_server_state(self):
            assert not _on_main_thread()  # blocking requests call, run off the loop
            return {"serverGameState": {"numConnectedPlayers": 2, "techTier": 5}}

    edits: list[tuple] = []

    async def fake_edit(message, **kwargs):
        edits.append((message, kwargs["embed"]))

    monkeypatch.setattr(sat, "_build_api", FakeAPI)
    monkeypatch.setattr(sat, "edit_message_if_changed", fake_edit)
    ext = object.__new__(sat.Satisfactory)
    ext.messages = FakeMessages()
    ext._apis = {}

    await sat.Satisfactory.update_message.callback(ext)
    await sat.Satisfactory.update_message.callback(ext)

    assert ext.messages.kept == ["1", "2", "3"]
    # One client per server, built off the loop and reused at the next refresh.
    assert clients == [("a:1234", "t1", False), ("b:7777", "t2", False)]
    assert [message for message, _ in edits] == ["message-1", "message-2"] * 2
    assert "`a`" in edits[0][1].fields[0].value


# ── Streamlabs ───────────────────────────────────────────────────────────


async def test_streamlabs_fetches_a_shared_team_once_per_refresh(store_config):
    import extensions.streamlabs as sl

    team_a = "https://streamlabscharity.com/teams/@a/x"
    store_config(
        _servers(
            "moduleStreamlabsCharity",
            **{
                "1": {"enabled": True, "streamlabsTeamUrl": team_a},
                "2": {"enabled": True, "streamlabsTeamUrl": team_a},
                "3": {
                    "enabled": True,
                    "streamlabsTeamUrl": "https://streamlabscharity.com/teams/@b/y",
                },
                "4": {"enabled": True},  # no team: idle
            },
        )
    )
    fetched: list[str] = []
    shown: list[tuple] = []

    async def fake_fetch_team(url):
        fetched.append(url)
        return {"id": url}, {}

    async def fake_update(message, url, campaign, members):
        shown.append((message, url))

    ext = object.__new__(sl.StreamlabsCharityExtension)
    ext.messages = FakeMessages()
    ext.fetch_team = fake_fetch_team
    ext.update_message = fake_update

    await sl.StreamlabsCharityExtension.streamlabscharity.callback(ext)

    assert fetched == [team_a, "https://streamlabscharity.com/teams/@b/y"]
    assert [m for m, _ in shown] == ["message-1", "message-2", "message-3"]
    assert ext.messages.kept == ["1", "2", "3", "4"]


# ── Olympics ─────────────────────────────────────────────────────────────


async def test_olympics_alerts_reach_every_enabled_guild_with_its_logo(store_config, monkeypatch):
    import extensions.olympics.tasks as tasks

    store_config(
        {
            "config": {"olympics": {"apiUrl": "https://olympics.test/api"}},
            **_servers(
                "moduleOlympics",
                **{
                    "1": {
                        "enabled": True,
                        "olympicsChannelId": "10",
                        "olympicsLogoUrl": "https://l/1",
                    },
                    "2": {"enabled": True, "olympicsChannelId": "20"},
                    "3": {"enabled": True},  # no channel: skipped
                },
            ),
        }
    )
    sent: dict[str, list] = {"10": [], "20": []}

    class Channel:
        def __init__(self, cid):
            self.cid = cid

        async def send(self, embeds):
            sent[self.cid].extend(embeds)

    async def fetch_channel(cid):
        return Channel(cid)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(tasks.asyncio, "sleep", no_sleep)
    from extensions.olympics import OlympicsExtension

    ext = object.__new__(OlympicsExtension)
    ext.bot = SimpleNamespace(fetch_channel=fetch_channel)
    ext.known_medals = {"old"}
    medal = {"key": "new"}

    async def fake_medals():
        return [medal]

    async def fake_standings():
        return []

    async def fake_save():
        return None

    ext._fetch_france_medals = fake_medals
    ext._fetch_medal_standings = fake_standings
    ext._save_state = fake_save
    ext._medal_key = lambda m: m["key"]
    logos: list[str] = []

    def fake_embed(m, standing, logo_url=""):
        logos.append(logo_url)
        return f"embed-{logo_url or 'none'}"

    ext._build_medal_alert_embed = fake_embed

    await OlympicsExtension.check_medals.callback(ext)

    assert sent == {"10": ["embed-https://l/1"], "20": ["embed-none"]}
    assert ext.known_medals == {"old", "new"}


async def test_olympics_does_not_poll_without_an_enabled_guild(store_config):
    from extensions.olympics import OlympicsExtension

    store_config(_servers("moduleOlympics", **{"1": {"enabled": False}}))
    ext = object.__new__(OlympicsExtension)

    async def must_not_fetch():
        raise AssertionError("polled while the module is off everywhere")

    ext._fetch_france_medals = must_not_fetch
    await OlympicsExtension.check_medals.callback(ext)


# ── Speedons ─────────────────────────────────────────────────────────────


async def test_speedons_renders_a_shared_campaign_once(store_config):
    import extensions.speedons as sp

    store_config(
        _servers(
            "moduleSpeedons",
            **{
                "1": {"enabled": True, "speedonsApiUrl": "https://api.ex/c1/"},
                "2": {"enabled": True, "speedonsApiUrl": "https://api.ex/c1"},
                "3": {"enabled": True},  # no campaign: idle
            },
        )
    )
    rendered: list[tuple] = []
    published: list[tuple] = []

    async def fake_render(base_url, icon_url):
        rendered.append((base_url, icon_url))
        return ["pages"], "live", 0

    async def fake_publish(guild_id, settings, embeds, live, page_index):
        published.append((guild_id, embeds, live))

    ext = object.__new__(sp.SpeedonsExtension)
    ext.schedule_messages = FakeMessages()
    ext.live_messages = FakeMessages()
    ext.paginators = {"9": object()}  # a guild the module no longer runs in
    ext._publish = fake_publish
    original = sp.render_schedule
    sp.render_schedule = fake_render
    try:
        await sp.SpeedonsExtension.get_speedons_schedule.callback(ext)
    finally:
        sp.render_schedule = original

    assert rendered == [("https://api.ex/c1", None)]
    assert [g for g, _, _ in published] == ["1", "2"]
    assert ext.paginators == {}


async def test_speedons_schedule_renders_runs_and_the_amount(monkeypatch):
    import extensions.speedons as sp

    data = {
        "https://api.ex/c/events": [
            {
                "id": "e1",
                "start_date": "2000-01-01T10:00:00+00:00",
                "end_date": "2000-01-01T11:00:00+00:00",
                "participants": [{"attendee_id": "a1", "role": "ACTOR"}],
                "properties": {"game": "Celeste", "category": "Any%"},
            }
        ],
        "https://api.ex/c/attendees": [{"id": "a1", "name": "Runner"}],
        "https://api.ex/c": {"amount": "12.5"},
        "https://api.ex/c/challenges": [],
    }

    async def fake_get_data(url):
        return data[url]

    monkeypatch.setattr(sp, "get_data", fake_get_data)
    embeds, live, page = await sp.render_schedule("https://api.ex/c", None)

    assert embeds[0].title == "Speedons 4 (12.50€)"
    assert embeds[0].fields[0].name == "Celeste"
    assert "Runner : **Runner**" in embeds[0].fields[0].value
    assert live.title == "Pas de run en cours (12.50€)"
    assert page == 0


# ── Minecraft ────────────────────────────────────────────────────────────


async def test_minecraft_status_runs_for_each_guild_with_a_server(store_config):
    from extensions.minecraft import Minecraft

    store_config(
        _servers(
            "moduleMinecraft",
            **{
                "1": {"enabled": True, "minecraftIp": "10.0.0.1", "minecraftUrl": "a.ex"},
                "2": {"enabled": True, "minecraftUrl": "b.ex"},
                "3": {"enabled": True},  # no server: skipped
            },
        )
    )
    seen: list[tuple] = []

    async def fake_update(guild_id, config, settings):
        seen.append((guild_id, settings.address))

    ext = object.__new__(Minecraft)
    ext.status_messages = FakeMessages()
    ext._last_stats_tables = {"9": "old"}
    ext.channel_edit_timestamps = {}
    ext.scheduled_events = {"9": object()}
    ext._events_recovered = {"9"}
    ext._update_status = fake_update

    await Minecraft.status.callback(ext)

    assert seen == [("1", "a.ex"), ("2", "b.ex")]
    # A guild where the module was switched off loses its state.
    assert ext._last_stats_tables == {} and ext.scheduled_events == {}
    assert ext._events_recovered == set()


async def test_minecraft_server_lookup_runs_off_the_loop(monkeypatch):
    import extensions.minecraft.status as status
    from features.minecraft import MinecraftSettings

    calls: list[tuple] = []

    class FakeServer:
        def __init__(self, host, port):
            calls.append(("direct", host, port))

        @staticmethod
        def lookup(address):
            calls.append(("lookup", address, _on_main_thread()))
            raise OSError("no SRV record")

    monkeypatch.setattr(status, "JavaServer", FakeServer)
    settings = MinecraftSettings.from_config(
        {"minecraftUrl": "a.ex", "minecraftIp": "10.0.0.1", "minecraftPort": "25565"}
    )

    server = await status.StatusMixin._server(object(), settings)

    assert isinstance(server, FakeServer)
    assert calls == [("lookup", "a.ex", False), ("direct", "10.0.0.1", 25565)]


@pytest.mark.parametrize(
    "module", ["satisfactory", "streamlabs", "speedons", "olympics", "minecraft"]
)
def test_the_trackers_no_longer_reload_on_save(module):
    import importlib

    package = importlib.import_module(f"extensions.{module}")
    classes = [
        cls
        for cls in vars(package).values()
        if isinstance(cls, type) and "MODULE_KEYS" in getattr(cls, "__dict__", {})
    ]
    assert classes and all(not getattr(cls, "RELOAD_ON_SAVE", False) for cls in classes)
