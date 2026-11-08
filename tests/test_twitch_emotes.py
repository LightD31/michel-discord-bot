"""Twitch emote changes: the diff (features/twitch/emotes.py) and the hourly check."""

from types import SimpleNamespace

import pytest

import extensions.twitch.emotes as emotes_mod
from extensions.twitch import TwitchExtension
from features.twitch.emotes import diff_emotes, emote_image_url, emote_kind
from src.core.errors import DatabaseError


def emote(eid, name, *, kind="follower", tier=None):
    return SimpleNamespace(
        id=eid, name=name, emote_type=kind, tier=tier, images={"url_1x": f"https://img/{eid}"}
    )


STORED = {"1": {"name": "Hype"}, "2": {"name": "Old"}, "3": {"name": "Gone"}}


def test_the_diff_tells_added_replaced_and_removed_apart():
    current = [emote("1", "Hype"), emote("20", "Old"), emote("4", "Fresh")]

    diff = diff_emotes(current, STORED)

    assert [e.id for e in diff.added] == ["4"]
    assert [(old, e.id) for old, e in diff.replaced] == [("2", "20")]
    assert diff.removed == ["3"]
    assert diff


def test_nothing_changed_is_an_empty_diff():
    assert not diff_emotes([emote("1", "Hype")], {"1": {"name": "Hype"}})


def test_the_largest_image_is_used():
    assert emote_image_url({"url_1x": "s", "url_4x": "l"}) == "l"
    assert emote_image_url({"url_2x": "m", "url_1x": "s"}) == "m"
    assert emote_image_url(None) is None


@pytest.mark.parametrize(
    ("kind", "tier", "label"),
    [
        ("subscriptions", "1000", "Sub tier 1"),
        ("subscriptions", "2000", "Sub tier 2"),
        ("subscriptions", "3000", "Sub tier 3"),
        ("bitstier", None, "Bits"),
        ("follower", None, "Follower"),
        ("smilies", None, "Autre"),
    ],
)
def test_how_an_emote_is_unlocked(kind, tier, label):
    assert emote_kind(kind, tier) == label


# ── The hourly check ─────────────────────────────────────────────────────


class Repo:
    stored: dict = {}
    failing = False
    written: list = []

    def __init__(self, streamer_id):
        self.streamer_id = streamer_id

    async def load_all(self):
        if Repo.failing:
            raise DatabaseError("mongo down")
        return {k: dict(v) for k, v in Repo.stored.items()}

    async def insert_many(self, docs):
        Repo.written.append(("insert", docs))

    async def replace_all(self, docs):
        Repo.written.append(("replace", docs))


class Channel:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, embed=None, files=None):
        self.sent.append(embed.title)


@pytest.fixture
def check(monkeypatch, tmp_path):
    Repo.stored, Repo.failing, Repo.written = {}, False, []
    monkeypatch.setattr(emotes_mod, "TwitchEmotesRepository", Repo)
    monkeypatch.setattr(emotes_mod, "EMOTE_CACHE_DIR", str(tmp_path))
    current: list = []

    async def get_channel_emotes(user_id):
        return list(current)

    async def download(self, emote_id, url, streamer_id):
        path = tmp_path / f"{streamer_id}_{emote_id}.png"
        path.write_bytes(b"png")
        return str(path)

    async def create_notification_embed(guild_id, title, description, color=None):
        embed = SimpleNamespace(title=title, fields=[], url=None)
        embed.add_field = lambda **kw: embed.fields.append(kw)
        embed.set_thumbnail = lambda url: None
        embed.set_image = lambda url: None
        embed.set_images = lambda *a: None
        return embed

    monkeypatch.setattr(emotes_mod.EmotesMixin, "download_emote_image", download)
    ext = object.__new__(TwitchExtension)
    ext.twitch = SimpleNamespace(get_channel_emotes=get_channel_emotes)
    ext.create_notification_embed = create_notification_embed
    following, silent = Channel(), Channel()
    ext.streamers = {
        "a": SimpleNamespace(
            streamer_id="zerator",
            user_id="1",
            guild_id=1,
            notif_channel=following,
            notify_emote_changes=True,
        ),
        "b": SimpleNamespace(
            streamer_id="zerator",
            user_id="1",
            guild_id=2,
            notif_channel=silent,
            notify_emote_changes=False,
        ),
    }

    async def run():
        await TwitchExtension.check_new_emotes.callback(ext)

    return SimpleNamespace(run=run, current=current, following=following, silent=silent)


async def test_the_first_check_stores_the_emotes_silently(check):
    check.current[:] = [emote("1", "Hype"), emote("2", "Old")]

    await check.run()

    assert [op for op, _ in Repo.written] == ["insert"]
    assert check.following.sent == []


async def test_changes_reach_the_guilds_that_opted_in(check):
    Repo.stored = {"1": {"name": "Hype"}, "2": {"name": "Old"}, "3": {"name": "Gone"}}
    check.current[:] = [emote("1", "Hype"), emote("20", "Old"), emote("4", "Fresh")]

    await check.run()

    assert check.following.sent == ["Mise à jour d'emote", "Nouvel emote ajouté", "Emote supprimé"]
    assert check.silent.sent == []
    op, docs = Repo.written[-1]
    assert op == "replace" and sorted(d["_id"] for d in docs) == ["1", "20", "4"]


async def test_an_unchanged_set_with_its_images_cached_writes_nothing(check, tmp_path):
    Repo.stored = {"1": {"name": "Hype"}}
    (tmp_path / "zerator_1.png").write_bytes(b"png")
    check.current[:] = [emote("1", "Hype")]

    await check.run()

    assert Repo.written == []


async def test_an_unreadable_store_is_not_taken_for_a_first_check(check):
    """It was: every emote was inserted again, on top of the stored ones."""
    Repo.failing = True
    check.current[:] = [emote("1", "Hype")]

    await check.run()

    assert Repo.written == []
