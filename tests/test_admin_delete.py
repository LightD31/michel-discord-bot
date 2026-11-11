"""/delete answers before a long purge, reports what it deleted and checks its message ids."""

from types import SimpleNamespace

from interactions import Permissions

from extensions.admin import AdminExtension


class FakeChannel:
    id = 5

    def __init__(self, events, deleted=3):
        self.events = events
        self.deleted = deleted

    def permissions_for(self, member):
        return Permissions.ALL

    async def purge(self, **kwargs):
        self.events.append(("purge", kwargs))
        return self.deleted


class FakeCtx:
    def __init__(self, channel):
        self.channel = channel
        self.user = SimpleNamespace(id=1, username="mod")
        self.author = self.user
        self.events = channel.events

    async def defer(self, **kwargs):
        self.events.append(("defer", kwargs))

    async def send(self, content=None, **kwargs):
        self.events.append(("send", content))


async def test_the_purge_runs_after_the_ack_and_reports_its_count():
    events = []
    ctx = FakeCtx(FakeChannel(events, deleted=3))
    await AdminExtension.delete.callback(object(), ctx, 50, None, "123", None)
    assert [name for name, _ in events] == ["defer", "purge", "send"]
    assert events[1][1]["before"] == 123 and "after" not in events[1][1]
    assert events[2][1].startswith("3 message(s)")


async def test_a_malformed_message_id_purges_nothing():
    events = []
    ctx = FakeCtx(FakeChannel(events))
    await AdminExtension.delete.callback(object(), ctx, 50, None, "hier", None)
    assert [name for name, _ in events] == ["send"]
    assert "ID de message" in events[0][1]
