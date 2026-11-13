"""XP from messages: the cooldown, level-ups and their reward roles (T04)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeGuild, FakeMember, FakeMessage, FakeRole, bare_extension
from interactions import Permissions

import extensions.xp.leveling as leveling_module
from extensions.xp import XpExtension
from features.xp import TTLCache


class MemoryXp:
    """One guild's xp collection, with the repository's atomic award."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.events: list[tuple] = []

    async def ensure_collection(self, guild_name=None):
        return False

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def insert_new_user(self, user_id, initial_xp, timestamp):
        self.users.setdefault(
            user_id, {"_id": user_id, "xp": initial_xp, "time": timestamp, "msg": 1, "lvl": 0}
        )

    async def award_xp(self, user_id, xp_gained, message_delta, timestamp, *, min_interval=None):
        user = self.users.get(user_id)
        if user is None or (min_interval is not None and user["time"] > timestamp - min_interval):
            return None
        user["xp"] += xp_gained
        user["msg"] += message_delta
        user["time"] = timestamp
        return dict(user)

    async def log_event(self, user_id, xp_gained, new_xp, at, source):
        self.events.append((user_id, xp_gained, source))

    async def set_level(self, user_id, level):
        self.users[user_id]["lvl"] = level


class IdleTask:
    """A Task that starts and runs without doing anything."""

    running = False

    def start(self):
        self.running = True

    async def __call__(self):
        return None


@pytest.fixture
def setup(monkeypatch, store_config):
    config = store_config(
        {
            "servers": {
                "1": {
                    "moduleXp": {
                        "enabled": True,
                        "levelUpMessageList": [
                            {"text": "GG {mention}, niveau {lvl} !", "weight": 1}
                        ],
                        "levelRewards": {"1": "501", "2": "502"},
                    }
                }
            }
        }
    )
    monkeypatch.setattr(leveling_module.random, "randint", lambda low, high: 20)
    guild = FakeGuild(1)
    guild.add_role(FakeRole(501, position=2))
    guild.add_role(FakeRole(502, position=3))
    channel = guild.add_channel(FakeChannel(10))
    member = guild.add_member(FakeMember(7, "bob"))
    repo = MemoryXp()
    ext = bare_extension(
        XpExtension,
        SimpleNamespace(user=guild.me),
        _rank_cache=TTLCache(ttl=60),
        _xp_collections=set(),
        _repo=lambda guild_id: repo,
    )
    return ext, repo, member, channel, config


START = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def message_from(author, channel, at):
    msg = FakeMessage(content="salut", author=author, channel=channel)
    msg.created_at = at
    return SimpleNamespace(message=msg)


async def say(ext, member, channel, at):
    await XpExtension.on_message.callback(ext, message_from(member, channel, at))


async def test_a_first_message_creates_the_member(setup):
    ext, repo, member, channel, _ = setup
    await say(ext, member, channel, START)
    assert repo.users["7"]["xp"] == 20 and repo.users["7"]["msg"] == 1


async def test_messages_within_the_cooldown_earn_nothing(setup):
    ext, repo, member, channel, _ = setup
    await say(ext, member, channel, START)
    await say(ext, member, channel, START + timedelta(seconds=30))
    assert repo.users["7"]["xp"] == 20
    await say(ext, member, channel, START + timedelta(seconds=61))
    assert repo.users["7"]["xp"] == 40
    assert repo.events == [("7", 20, "message")]


async def test_a_level_up_is_announced_and_rewarded(setup):
    ext, repo, member, channel, _ = setup
    repo.users["7"] = {"_id": "7", "xp": 95, "time": 0, "msg": 5, "lvl": 0}  # 100 XP is level 1
    await say(ext, member, channel, START)
    (announce,) = channel.sent
    assert announce.content == "GG <@7>, niveau 1 !"
    assert repo.users["7"]["lvl"] == 1
    assert [r.id for r in member.roles] == [501]


async def test_a_reward_that_became_a_staff_role_is_not_granted(setup):
    ext, repo, member, channel, _ = setup
    channel.guild.roles[501].permissions = Permissions.BAN_MEMBERS
    repo.users["7"] = {"_id": "7", "xp": 95, "time": 0, "msg": 5, "lvl": 0}
    await say(ext, member, channel, START)
    assert repo.users["7"]["lvl"] == 1
    assert member.roles == []


async def test_a_reward_role_missing_from_the_guild_is_skipped(setup):
    ext, repo, member, channel, _ = setup
    del channel.guild.roles[501]
    repo.users["7"] = {"_id": "7", "xp": 95, "time": 0, "msg": 5, "lvl": 0}
    await say(ext, member, channel, START)
    assert member.roles == []


async def test_the_next_reward_replaces_the_previous_one(setup):
    ext, repo, member, channel, _ = setup
    member.roles = [FakeRole(501)]
    repo.users["7"] = {"_id": "7", "xp": 315, "time": 0, "msg": 5, "lvl": 1}  # 320 → level 2
    await say(ext, member, channel, START)
    assert [r.id for r in member.roles] == [502]


async def test_stacked_rewards_keep_the_previous_ones(setup):
    ext, repo, member, channel, config = setup
    config["servers"]["1"]["moduleXp"]["stackLevelRewards"] = True
    member.roles = [FakeRole(501)]
    repo.users["7"] = {"_id": "7", "xp": 315, "time": 0, "msg": 5, "lvl": 1}
    await say(ext, member, channel, START)
    assert sorted(r.id for r in member.roles) == [501, 502]


async def test_bots_and_servers_without_xp_earn_nothing(setup, store_config):
    ext, repo, member, channel, _ = setup
    robot = channel.guild.add_member(FakeMember(8, "robot", bot=True))
    await say(ext, robot, channel, START)
    store_config({"servers": {}})
    await say(ext, member, channel, START)
    assert repo.users == {}


async def test_an_admin_template_may_ping_everyone(setup):
    ext, repo, member, channel, config = setup
    config["servers"]["1"]["moduleXp"]["levelUpMessageList"] = [
        {"text": "@everyone {mention} passe niveau {lvl}", "weight": 1}
    ]
    repo.users["7"] = {"_id": "7", "xp": 95, "time": 0, "msg": 5, "lvl": 0}
    await say(ext, member, channel, START)
    (announce,) = channel.sent
    assert "everyone" in announce.sent_with["allowed_mentions"].parse


async def test_a_plain_template_never_pings_everyone(setup):
    ext, repo, member, channel, _ = setup
    repo.users["7"] = {"_id": "7", "xp": 95, "time": 0, "msg": 5, "lvl": 0}
    await say(ext, member, channel, START)
    (announce,) = channel.sent
    assert "everyone" not in announce.sent_with["allowed_mentions"].parse


async def test_xp_works_after_a_startup_with_mongodb_down(setup):
    """A failed ping at startup used to switch XP off until the next restart (B04)."""
    ext, repo, member, channel, _ = setup

    async def mongo_down():
        from src.core.errors import DatabaseError

        raise DatabaseError("mongo down")

    repo.ensure_indexes = mongo_down
    for task in ("_cache_cleanup_task", "_voice_xp_tick", "leaderboardpermanent"):
        setattr(ext, task, IdleTask())
    await XpExtension.on_startup.callback(ext)

    await say(ext, member, channel, START)
    assert repo.users["7"]["xp"] == 20


async def test_members_already_in_voice_earn_after_a_restart(setup, store_config):
    """Sessions only started on join events: whoever was in voice earned nothing (B14)."""
    ext, repo, member, channel, config = setup
    config["servers"]["1"]["moduleXp"]["voiceXpEnabled"] = True
    store_config(config)
    guild = member.guild
    bot_member = guild.me
    voice = SimpleNamespace(id=30)
    guild.voice_states = [
        SimpleNamespace(member=member, channel=voice),
        SimpleNamespace(member=bot_member, channel=voice),
        SimpleNamespace(member=None, channel=voice),
    ]
    ext.bot = SimpleNamespace(user=guild.me, get_guild=lambda gid: guild)
    ext._voice_sessions = {("1", "8"): 123.0}

    assert ext._resume_voice_sessions() == 1
    assert set(ext._voice_sessions) == {("1", "7"), ("1", "8")}
    assert ext._voice_sessions[("1", "8")] == 123.0  # a running session keeps its clock
