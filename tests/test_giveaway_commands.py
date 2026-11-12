"""/giveaway start, end, cancel, reroll and list, called as Discord would (T04)."""

from datetime import timedelta

import pytest
from discord_fakes import (
    FakeChannel,
    FakeCtx,
    FakeGuild,
    FakeMember,
    FakeMessage,
    bare_extension,
    run,
)
from interactions import Embed

import extensions.giveaway as giveaway_module
from extensions.giveaway import GiveawayExtension
from features.giveaway import Giveaway
from src.core import clock


class MemoryGiveaways:
    """The giveaways of one guild, kept in memory with the repository's semantics."""

    def __init__(self):
        self.items: dict[str, Giveaway] = {}

    async def add(self, giveaway):
        gid = f"{len(self.items) + 1:024x}"
        self.items[gid] = giveaway.model_copy(update={"id": gid})
        return gid

    async def get_by_message(self, message_id):
        return next((g for g in self.items.values() if g.message_id == str(message_id)), None)

    async def mark_cancelled(self, giveaway_id):
        g = self.items[giveaway_id]
        if g.drawn or g.cancelled:
            return False
        g.cancelled = True
        return True

    async def claim_draw(self, giveaway_id):
        g = self.items[giveaway_id]
        if g.drawn or g.cancelled:
            return False
        g.drawn = True
        return True

    async def release_draw(self, giveaway_id):
        self.items[giveaway_id].drawn = False

    async def update_winners(self, giveaway_id, winners):
        self.items[giveaway_id].winners = list(winners)

    async def list_active(self):
        return [g for g in self.items.values() if not g.drawn and not g.cancelled]


class Reactors:
    def __init__(self, users):
        self._users = users

    def __aiter__(self):
        async def gen():
            for user in self._users:
                yield user

        return gen()


class Reaction:
    def __init__(self, emoji, users):
        self.emoji = emoji
        self._users = users

    def users(self):
        return Reactors(self._users)


@pytest.fixture
def setup(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleGiveaway": {"enabled": True}}}})

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(giveaway_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    host = guild.add_member(FakeMember(3, "alice"))
    repo = MemoryGiveaways()
    ext = bare_extension(GiveawayExtension, _repo=lambda guild_id: repo)
    ctx = FakeCtx(host, channel)
    ext.bot = ctx.bot
    return ext, ctx, repo, channel


async def start(ext, ctx, **options):
    await run(GiveawayExtension.giveaway_start, ext, ctx, **options)


async def test_start_posts_the_announcement_and_stores_the_giveaway(setup):
    ext, ctx, repo, _ = setup
    await start(ext, ctx, prize="Un jeu", duration="2h", winners=2)
    (stored,) = repo.items.values()
    (announcement,) = [r for r in ctx.replies if r.get("embeds")]
    assert "Un jeu" in announcement["embeds"][0].title
    assert stored.winners_count == 2 and stored.host_id == "3"
    assert stored.ends_at - clock.now() == pytest.approx(
        timedelta(hours=2), abs=timedelta(seconds=5)
    )
    assert stored.ends_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("duration", "error"),
    [("bientôt", "Format de durée invalide"), ("5s", "Durée minimale"), ("90d", "maximale")],
)
async def test_start_refuses_a_bad_duration(setup, duration, error):
    ext, ctx, repo, _ = setup
    await start(ext, ctx, prize="Un jeu", duration=duration)
    assert error in ctx.last
    assert repo.items == {}


def posted(repo, channel, entrants=(), **fields):
    """A running giveaway with its announcement in *channel*, reacted to by *entrants*."""
    message = FakeMessage(500, embeds=[Embed(title="🎁 GIVEAWAY — Un jeu")], channel=channel)
    message.reactions = [Reaction("🎉", [FakeMember(int(e)) for e in entrants])]
    channel.messages[message.id] = message
    gid = f"{len(repo.items) + 1:024x}"
    repo.items[gid] = Giveaway(
        _id=gid,
        guild_id="1",
        channel_id=str(channel.id),
        message_id="500",
        host_id="3",
        prize="Un jeu",
        allow_host_win=False,
        ends_at=clock.now() + timedelta(hours=1),
        **fields,
    )
    return repo.items[gid], message


async def test_end_draws_now_and_announces_the_winner(setup):
    ext, ctx, repo, channel = setup
    giveaway, message = posted(repo, channel, entrants=["7"])
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    assert ctx.deferred is True
    assert giveaway.drawn and giveaway.winners == ["7"]
    assert "<@7>" in message.replies[0]
    assert "Tirage effectué" in ctx.last


async def test_end_twice_says_it_is_already_closed(setup):
    ext, ctx, repo, channel = setup
    posted(repo, channel, entrants=["7"])
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    assert "déjà clos" in ctx.last


async def test_end_of_an_unknown_message(setup):
    ext, ctx, _, _ = setup
    await run(GiveawayExtension.giveaway_end, ext, ctx, "404")
    assert "Aucun giveaway" in ctx.last


async def test_cancel_marks_it_and_edits_the_announcement(setup):
    ext, ctx, repo, channel = setup
    giveaway, message = posted(repo, channel)
    await run(GiveawayExtension.giveaway_cancel, ext, ctx, "500")
    assert giveaway.cancelled
    assert message.edits, "the announcement shows the cancellation"
    assert "annulé" in ctx.last
    # A cancelled giveaway can't be drawn any more.
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    assert "annulé" in ctx.last


async def test_reroll_needs_a_drawn_giveaway(setup):
    ext, ctx, repo, channel = setup
    posted(repo, channel, entrants=["7", "8"])
    await run(GiveawayExtension.giveaway_reroll, ext, ctx, "500")
    assert "/giveaway end" in ctx.last


async def test_reroll_picks_someone_who_has_not_won(setup):
    ext, ctx, repo, channel = setup
    giveaway, message = posted(repo, channel, entrants=["7", "8"], drawn=True, winners=["7"])
    await run(GiveawayExtension.giveaway_reroll, ext, ctx, "500", winners=1)
    assert giveaway.winners == ["7", "8"]
    assert "<@8>" in message.replies[0]


async def test_reroll_with_nobody_left(setup):
    ext, ctx, repo, channel = setup
    posted(repo, channel, entrants=["7"], drawn=True, winners=["7"])
    await run(GiveawayExtension.giveaway_reroll, ext, ctx, "500")
    assert "Pas assez de participants" in ctx.last


async def test_the_host_never_enters_unless_allowed(setup):
    ext, ctx, repo, channel = setup
    giveaway, _ = posted(repo, channel, entrants=["3"])  # only the host reacted
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    assert giveaway.drawn and giveaway.winners == []


async def test_list_shows_the_running_giveaways(setup):
    ext, ctx, repo, channel = setup
    posted(repo, channel)
    await run(GiveawayExtension.giveaway_list, ext, ctx)
    (embed,) = ctx.replies[-1]["embeds"]
    assert "Un jeu" in embed.description
    assert ctx.replies[-1]["ephemeral"] is True


async def test_list_when_nothing_runs(setup):
    ext, ctx, _, _ = setup
    await run(GiveawayExtension.giveaway_list, ext, ctx)
    assert ctx.last == "Aucun giveaway en cours."
