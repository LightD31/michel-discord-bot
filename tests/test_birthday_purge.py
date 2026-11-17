"""/anniversaire purge: the confirmation really deletes, on every server.

It never could: the button event was read as its context (no ``custom_id``),
and the count was ``sum()`` over an async generator.
"""

from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

from extensions.birthday import BirthdayExtension


class Repo:
    def __init__(self, guild_id, stored):
        self.guild_id, self.stored = guild_id, stored

    async def delete(self, user_id):
        return int(self.stored.pop((self.guild_id, user_id), None) is not None)


@pytest.fixture
def purge(store_config):
    store_config(
        {
            "servers": {
                "1": {"moduleBirthday": {"enabled": True}},
                "2": {"moduleBirthday": {"enabled": True}},
                "3": {"moduleBirthday": {"enabled": False}},
            }
        }
    )
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    author = guild.add_member(FakeMember(5, "alice"))
    stored = {("1", 5): "a", ("2", 5): "b", ("1", 6): "c"}

    def press(custom_id):
        ctx = FakeCtx(author, channel)
        button = FakeCtx(author, channel, custom_id=custom_id)

        async def wait_for_component(**kwargs):
            return SimpleNamespace(ctx=button)  # the event, as interactions.py hands it

        ctx.bot.wait_for_component = wait_for_component
        ext = bare_extension(BirthdayExtension, ctx.bot, _repo=lambda gid: Repo(str(gid), stored))
        return ext, ctx, button

    return press, stored


async def test_confirming_deletes_the_birthday_on_every_enabled_server(purge):
    press, stored = purge
    ext, ctx, button = press("birthday_purge_confirm")
    await run(BirthdayExtension.anniversaire_purge, ext, ctx)
    assert stored == {("1", 6): "c"}  # only the other member's
    assert button.origin_edits[-1]["content"] == "Anniversaire supprimé sur 2 serveur(s) ✅"


async def test_cancelling_deletes_nothing(purge):
    press, stored = purge
    ext, ctx, button = press("birthday_purge_cancel")
    await run(BirthdayExtension.anniversaire_purge, ext, ctx)
    assert len(stored) == 3
    assert button.origin_edits[-1]["content"] == "Suppression annulée."
