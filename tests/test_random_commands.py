"""/pick and /shuffle echo what the member typed: never a ping, never past 2000 characters."""

from extensions.random_ import NO_PINGS, RandomExtension


class FakeCtx:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


async def test_pick_echoes_without_pinging():
    ctx = FakeCtx()
    await RandomExtension.pick.callback(object(), ctx, "@everyone;@here", ";")
    [(content, kwargs)] = ctx.sent
    assert "@" in content and kwargs["allowed_mentions"] is NO_PINGS


async def test_shuffle_stays_within_a_message():
    ctx = FakeCtx()
    items = ";".join("x" * 14 for _ in range(100))  # 1500 characters, the option's cap
    await RandomExtension.shuffle.callback(object(), ctx, items, ";")
    [(content, kwargs)] = ctx.sent
    assert len(content) <= 2000 and kwargs["allowed_mentions"] is NO_PINGS
