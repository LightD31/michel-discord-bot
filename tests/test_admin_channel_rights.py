"""Admin commands acting on another channel check the author's rights there (S02).

Discord checks a command's default permissions in the channel it is run from;
the bot then acts on the target channel with its own rights.
"""

from types import SimpleNamespace

import pytest
from interactions import Permissions, errors

from extensions.admin import AdminExtension
from src.discord_ext.channels import member_can
from src.discord_ext.errors import failure_reply
from src.discord_ext.messages import mentions_member_could_send

AUTHOR = SimpleNamespace(id=1, username="mod")
MOD_RIGHTS = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES | Permissions.MANAGE_MESSAGES


class FakeRole:
    def __init__(self, mentionable):
        self.mentionable = mentionable


class FakeChannel:
    def __init__(self, rights, *, edit_error=None, roles=None):
        self.id = 5
        self.name = "general"
        self.type = 0
        self.rights = rights
        self.edit_error = edit_error
        self.guild = SimpleNamespace(get_role=lambda rid: (roles or {}).get(rid))
        self.calls = []

    def permissions_for(self, member):
        assert member is AUTHOR
        return self.rights

    async def purge(self, **kwargs):
        self.calls.append(("purge", kwargs))
        return 1

    async def send(self, content, **kwargs):
        self.calls.append(("send", content, kwargs))
        return SimpleNamespace(channel=self)

    async def edit(self, **kwargs):
        if self.edit_error:
            raise self.edit_error
        self.calls.append(("edit", kwargs))

    async def add_permission(self, *args, **kwargs):
        self.calls.append(("add_permission", kwargs))


class FakeCtx:
    def __init__(self, channel=None):
        self.author = self.user = AUTHOR
        self.channel = channel
        self.guild = SimpleNamespace(default_role=object())
        self.replies = []

    async def defer(self, **kwargs):
        pass

    async def send(self, content=None, **kwargs):
        self.replies.append(content)


def forbidden():
    response = SimpleNamespace(status=403, reason="Forbidden", method="PATCH", url="/x")
    return errors.Forbidden(response, {"message": "Missing Permissions", "code": 50013})


# member_can ------------------------------------------------------------------


def test_member_can_needs_every_bit():
    channel = FakeChannel(Permissions.VIEW_CHANNEL)
    assert member_can(AUTHOR, channel, Permissions.VIEW_CHANNEL)
    assert not member_can(AUTHOR, channel, Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES)


@pytest.mark.parametrize("error", [RuntimeError("other guild"), AttributeError("no guild")])
def test_member_can_says_no_when_it_cant_tell(error):
    def raising(member):
        raise error

    assert not member_can(AUTHOR, SimpleNamespace(permissions_for=raising), Permissions.NONE)
    assert not member_can(AUTHOR, SimpleNamespace(), Permissions.NONE)  # a DM channel
    assert not member_can(None, FakeChannel(Permissions.ALL), Permissions.NONE)


# The commands -----------------------------------------------------------------


async def test_delete_refuses_a_channel_where_the_author_cant_manage_messages():
    target = FakeChannel(Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES)
    ctx = FakeCtx(FakeChannel(MOD_RIGHTS))
    await AdminExtension.delete.callback(object(), ctx, 10, target, None, None)
    assert target.calls == []
    assert "<#5>" in ctx.replies[0]


async def test_send_refuses_a_channel_the_author_cant_write_in():
    target = FakeChannel(Permissions.VIEW_CHANNEL)
    ctx = FakeCtx()
    await AdminExtension.send.callback(object(), ctx, "salut", target)
    assert target.calls == []


async def test_send_pings_only_what_the_author_could_ping():
    roles = {7: FakeRole(mentionable=True), 8: FakeRole(mentionable=False)}
    target = FakeChannel(MOD_RIGHTS, roles=roles)
    ctx = FakeCtx()
    await AdminExtension.send.callback(object(), ctx, "@everyone <@&7> <@&8> <@42>", target)
    [(_, _, kwargs)] = target.calls
    allowed = kwargs["allowed_mentions"]
    assert allowed.parse == []
    assert allowed.roles == [7] and allowed.users == [42]


def test_mention_everyone_lets_every_written_mention_through():
    target = FakeChannel(MOD_RIGHTS | Permissions.MENTION_EVERYONE)
    allowed = mentions_member_could_send(AUTHOR, target, "@here <@&8>")
    assert allowed.parse == ["everyone"] and allowed.roles == [8]


@pytest.mark.parametrize("command", ["lock", "unlock"])
async def test_lock_and_unlock_refuse_a_channel_the_author_cant_manage(command):
    target = FakeChannel(MOD_RIGHTS)
    ctx = FakeCtx()
    await getattr(AdminExtension, command).callback(object(), ctx, target)
    assert target.calls == []


async def test_slowmode_reports_a_refusal_without_the_library_text():
    target = FakeChannel(Permissions.VIEW_CHANNEL | Permissions.MANAGE_CHANNELS)
    target.edit_error = forbidden()
    ctx = FakeCtx()
    await AdminExtension.slowmode.callback(object(), ctx, 10, target)
    [reply] = ctx.replies
    assert "droits nécessaires" in reply
    assert "50013" not in reply and "Missing" not in reply


def test_an_unexpected_failure_gets_a_reference(caplog):
    import logging

    logger = logging.getLogger("test.failure")
    with caplog.at_level(logging.ERROR, logger="test.failure"):
        reply = failure_reply("Impossible de bannir ce membre", KeyError("secret"), logger)
    assert "secret" not in reply and "référence" in reply
    assert caplog.records[0].exc_info is not None
