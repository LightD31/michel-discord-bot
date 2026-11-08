"""The extensions that kept their own message ids now go through the shared helper.

MDI's schedule, each vlrgg team's planning and each Twitch streamer's planning
used to write ``channelId:messageId`` back into config.json; they now resolve
their message by slot (``bot_messages``, src.core.message_refs). That no module
schema declares a message id any more is checked in tests/test_module_registry.py.
"""

from types import SimpleNamespace

import pytest

import extensions.mdi.notifications as mdi_notifications
import extensions.twitch.schedule as twitch_schedule
import extensions.vlrgg.notifications as vlrgg_notifications
from extensions.mdi import MdiExtension
from extensions.mdi._common import SCHEDULE_SLOT, GuildConfig, GuildState
from extensions.twitch import TwitchExtension
from extensions.twitch._common import StreamerInfo
from extensions.vlrgg import VlrggExtension
from extensions.vlrgg._common import TeamConfig, TeamState


class Resolver:
    """Stands in for fetch_or_create_persistent_message: records the slots asked for."""

    def __init__(self) -> None:
        self.asked: list[dict] = []
        self.message = SimpleNamespace(id=1)

    async def __call__(self, bot, **kwargs):
        self.asked.append(kwargs)
        return self.message if kwargs.get("channel_id") else None


@pytest.fixture
def resolver(monkeypatch):
    fake = Resolver()
    for module in (mdi_notifications, vlrgg_notifications, twitch_schedule):
        monkeypatch.setattr(module, "fetch_or_create_persistent_message", fake)
    return fake


# ── MDI ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mdi(monkeypatch):
    edits: list = []
    forgotten: list = []

    async def fake_edit(message, **kwargs):
        if message.id == "broken":
            raise RuntimeError("Unknown Message")
        edits.append((message, kwargs))

    async def fake_event_url(slug):
        return f"https://raider.io/events/{slug}"

    monkeypatch.setattr(mdi_notifications, "edit_message_if_changed", fake_edit)
    monkeypatch.setattr(mdi_notifications, "_event_url", fake_event_url)
    monkeypatch.setattr(
        mdi_notifications, "forget_persistent_message", lambda *a: forgotten.append(a)
    )
    ext = object.__new__(MdiExtension)
    ext.bot = object()
    ext._build_schedule_embed = lambda *args: "schedule-embed"
    ext._schedule_hash = lambda matches: f"hash-{len(matches)}"
    state = GuildState(
        server_id="1",
        guild_config=GuildConfig.from_dict({"notificationChannelId": "10"}),
        notification_channel=SimpleNamespace(id=10),
    )
    return ext, state, edits, forgotten


async def test_mdi_schedule_resolves_its_slot_and_skips_an_unchanged_render(resolver, mdi):
    ext, state, edits, _ = mdi

    await ext._refresh_schedule_message(state, [])
    await ext._refresh_schedule_message(state, [])  # same matches: nothing to edit

    assert resolver.asked[0]["slot"] == SCHEDULE_SLOT
    assert resolver.asked[0]["channel_id"] == "10"
    assert resolver.asked[0]["pin"] is True  # pinSchedule defaults on
    assert len(edits) == 1
    assert edits[0][1] == {
        "content": "",
        "embeds": ["schedule-embed"],
        "logger": mdi_notifications.logger,
    }


async def test_mdi_schedule_is_resolved_again_after_a_failed_edit(resolver, mdi):
    ext, state, edits, forgotten = mdi
    resolver.message = SimpleNamespace(id="broken")

    await ext._refresh_schedule_message(state, [])

    assert edits == [] and forgotten == [("1", SCHEDULE_SLOT)]
    assert state.schedule_message is None


def test_mdi_no_longer_reads_a_message_id_from_its_config():
    assert not hasattr(GuildConfig.from_dict({}), "schedule_message_id")


# ── vlrgg ────────────────────────────────────────────────────────────────


def test_a_vlrgg_team_names_its_planning_channel_and_slot():
    team = TeamConfig.from_dict({"name": "Team.Liquid", "channelId": " 10 ", "pin": True})

    assert team.channel_id == "10" and team.pin
    assert team.slot == "vlrgg:team:Team.Liquid"
    assert TeamConfig.from_dict({"name": "KC"}).channel_id is None


async def test_a_vlrgg_team_schedule_goes_to_its_slot(resolver, monkeypatch):
    edits: list = []

    async def fake_edit(message, **kwargs):
        edits.append(kwargs["embeds"])

    async def shorten(embeds, tags):
        return embeds

    async def fetch(tc):
        return ["planning"], [], None

    async def nothing(*args):
        return None

    monkeypatch.setattr(vlrgg_notifications, "edit_message_if_changed", fake_edit)
    monkeypatch.setattr(vlrgg_notifications, "shorten_embeds", shorten)
    ext = object.__new__(VlrggExtension)
    ext.bot = object()
    ext._fetch_team_schedule = fetch
    ext._handle_match_transitions = nothing
    team = TeamState(
        team_config=TeamConfig.from_dict({"name": "KC", "channelId": "10"}), server_id="1"
    )

    await ext._update_team_schedule(team)

    assert resolver.asked[0]["slot"] == "vlrgg:team:KC"
    assert resolver.asked[0]["guild_id"] == "1"
    assert edits == [["planning"]]


# ── Twitch ───────────────────────────────────────────────────────────────


async def test_a_twitch_planning_message_is_resolved_by_streamer_slot(resolver):
    ext = object.__new__(TwitchExtension)
    ext.bot = object()
    with_planning = StreamerInfo(1, "zerator", {"twitchPlanningChannelId": "10"})
    without = StreamerInfo(1, "other", {})

    assert await ext._planning_message(with_planning) is resolver.message
    assert await ext._planning_message(without) is None

    assert len(resolver.asked) == 1
    assert resolver.asked[0]["slot"] == "twitch:planning:zerator"
    assert resolver.asked[0]["channel_id"] == 10
