"""Members who left while the bot was down are dropped, from a complete list only (O01)."""

from types import SimpleNamespace

import extensions.userinfo as userinfo


class Repo:
    def __init__(self):
        self.kept: list[str] | None = None

    async def bulk_upsert(self, data):
        return None

    async def delete_absent(self, ids):
        self.kept = list(ids)
        return 1


def member(uid, bot=False):
    return SimpleNamespace(id=uid, username=f"u{uid}", display_name=f"U{uid}", bot=bot)


async def run(monkeypatch, guild):
    repo = Repo()
    monkeypatch.setattr(userinfo, "UserInfoRepository", lambda gid: repo)
    ext = object.__new__(userinfo.UserInfoExtension)
    ext.bot = SimpleNamespace(guilds=[guild])
    await userinfo.UserInfoExtension.on_startup.callback(ext)
    return repo


async def test_a_complete_member_list_prunes_the_departed(monkeypatch):
    guild = SimpleNamespace(
        id=1, members=[member(1), member(2), member(9, bot=True)], member_count=3
    )
    repo = await run(monkeypatch, guild)
    assert repo.kept == ["1", "2"]


async def test_a_partial_member_list_prunes_nothing(monkeypatch):
    guild = SimpleNamespace(id=1, members=[member(1)], member_count=250)
    repo = await run(monkeypatch, guild)
    assert repo.kept is None
