"""/warn, /timeout, /kick and the infraction history, with the role hierarchy (T04)."""

from datetime import UTC, datetime, timedelta

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole, bare_extension, run
from interactions import Embed

from extensions.moderation import ModerationExtension


class MemoryInfractions:
    def __init__(self):
        self.items = {}

    async def next_case_id(self):
        return len(self.items) + 1

    async def add(self, infraction):
        self.items[infraction.id] = infraction
        return infraction

    async def get(self, case_id):
        return self.items.get(case_id)

    async def list_for_user(self, user_id):
        return [i for i in self.items.values() if i.user_id == user_id]

    async def set_active(self, case_id, active):
        self.items[case_id].active = active
        return True

    async def count_active_warnings(self, user_id):
        return sum(
            i.type == "warn" and i.active and i.user_id == user_id for i in self.items.values()
        )


@pytest.fixture
def setup(store_config):
    store_config(
        {
            "servers": {
                "1": {
                    "moduleModeration": {
                        "enabled": True,
                        "modLogChannelId": "20",
                        "dmOnAction": True,
                    }
                }
            }
        }
    )
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    modlog = guild.add_channel(FakeChannel(20, "modlog"))
    mod = guild.add_member(FakeMember(3, "modo", roles=[FakeRole(name="Modo", position=10)]))
    member = guild.add_member(FakeMember(7, "bob", roles=[FakeRole(name="Membre", position=2)]))
    repo = MemoryInfractions()
    ctx = FakeCtx(mod, channel)
    ext = bare_extension(ModerationExtension, ctx.bot, repository=lambda guild_id: repo)
    return ext, ctx, repo, member, modlog


async def test_warn_records_dms_and_logs_the_case(setup):
    ext, ctx, repo, member, modlog = setup
    await run(ModerationExtension.warn, ext, ctx, member, reason="Spam")
    (case,) = repo.items.values()
    assert (case.type, case.user_id, case.moderator_id, case.reason) == ("warn", "7", "3", "Spam")
    assert case.created_at.utcoffset() == timedelta(0)
    assert "avertissement" in member.dms[0] and "Spam" in member.dms[0]
    assert isinstance(modlog.sent[0].embeds[0], Embed)
    assert "Avertissements actifs : **1**" in ctx.last


async def test_warns_count_up(setup):
    ext, ctx, _, member, _ = setup
    await run(ModerationExtension.warn, ext, ctx, member)
    await run(ModerationExtension.warn, ext, ctx, member)
    assert "Avertissements actifs : **2**" in ctx.last


async def test_a_moderator_cannot_act_on_someone_who_outranks_them(setup):
    ext, ctx, repo, _, _ = setup
    boss = ctx.guild.add_member(FakeMember(8, "boss", roles=[FakeRole(position=30)]))
    await run(ModerationExtension.warn, ext, ctx, boss)
    assert "rang égal ou supérieur" in ctx.last and repo.items == {}


async def test_nobody_moderates_themselves(setup):
    ext, ctx, repo, _, _ = setup
    await run(ModerationExtension.kick, ext, ctx, ctx.author)
    assert "toi-même" in ctx.last and ctx.guild.kicks == []


async def test_the_bot_role_must_outrank_the_target(setup):
    ext, ctx, _, _, _ = setup
    ctx.author.roles = [FakeRole(position=80)]
    high = ctx.guild.add_member(FakeMember(8, "haut", roles=[FakeRole(position=60)]))  # bot is 50
    await run(ModerationExtension.warn, ext, ctx, high)
    assert "Mon rôle est trop bas" in ctx.last


async def test_timeout_mutes_until_the_end_and_records_it(setup):
    ext, ctx, repo, member, _ = setup
    await run(ModerationExtension.timeout, ext, ctx, member, "1h30m", reason="Calme-toi")
    (until,) = member.timeouts
    assert until - datetime.now(UTC) == pytest.approx(
        timedelta(minutes=90), abs=timedelta(seconds=5)
    )
    (case,) = repo.items.values()
    assert case.type == "timeout" and case.duration_seconds == 5400
    assert case.expires_at - case.created_at == pytest.approx(
        timedelta(minutes=90), abs=timedelta(seconds=1)
    )
    assert "1 h 30 min" in ctx.last


async def test_timeout_refuses_a_bad_duration(setup):
    ext, ctx, repo, member, _ = setup
    await run(ModerationExtension.timeout, ext, ctx, member, "demain")
    assert "Durée invalide" in ctx.last
    assert member.timeouts == [] and repo.items == {}


async def test_kick_removes_the_member_and_records_the_case(setup):
    ext, ctx, repo, member, _ = setup
    await run(ModerationExtension.kick, ext, ctx, member, reason="Trop c'est trop")
    ((kicked, kwargs),) = ctx.guild.kicks
    assert kicked is member and kwargs["reason"] == "Trop c'est trop"
    assert [c.type for c in repo.items.values()] == ["kick"]
    assert "expulsé" in member.dms[0]


async def test_the_module_must_be_on(setup, store_config):
    ext, ctx, repo, member, _ = setup
    store_config({"servers": {}})
    await run(ModerationExtension.warn, ext, ctx, member)
    assert "pas activé" in ctx.last and repo.items == {}


async def test_history_lists_view_and_revoke(setup):
    ext, ctx, repo, member, modlog = setup
    await run(ModerationExtension.warn, ext, ctx, member, reason="Spam")

    await run(ModerationExtension.infraction_list, ext, ctx, member)
    assert ctx.replies[-1]["embeds"]

    await run(ModerationExtension.infraction_view, ext, ctx, 1)
    assert ctx.replies[-1]["embeds"]

    await run(ModerationExtension.infraction_remove, ext, ctx, 1, reason="Erreur")
    assert repo.items[1].active is False and "révoqué" in ctx.last
    await run(ModerationExtension.infraction_remove, ext, ctx, 1)
    assert "déjà révoqué" in ctx.last
    await run(ModerationExtension.infraction_view, ext, ctx, 42)
    assert "introuvable" in ctx.last


async def test_history_of_a_member_without_sanctions(setup):
    ext, ctx, _, member, _ = setup
    await run(ModerationExtension.infraction_list, ext, ctx, member)
    assert "Aucune sanction" in ctx.last
