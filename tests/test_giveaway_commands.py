"""/concours lancer, tirer, annuler, nouveau-tirage and liste, and its entry buttons."""

from datetime import timedelta

import pytest
from discord_fakes import (
    FakeChannel,
    FakeCtx,
    FakeGuild,
    FakeMember,
    FakeMessage,
    FakeRole,
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
            return None
        g.drawn = True
        return g.model_copy(deep=True)

    async def _change(self, message_id, now, change):
        g = await self.get_by_message(message_id)
        if g is None or g.drawn or g.cancelled or g.ends_at <= now:
            return None
        before = g.model_copy(deep=True)
        change(g.entrants)
        return before

    async def add_entrant(self, message_id, user_id, now):
        return await self._change(
            message_id, now, lambda e: e.append(user_id) if user_id not in e else None
        )

    async def remove_entrant(self, message_id, user_id, now):
        return await self._change(
            message_id, now, lambda e: e.remove(user_id) if user_id in e else None
        )

    async def release_draw(self, giveaway_id):
        self.items[giveaway_id].drawn = False

    async def update_winners(self, giveaway_id, winners):
        self.items[giveaway_id].winners = list(winners)

    async def list_active(self):
        return [g for g in self.items.values() if not g.drawn and not g.cancelled]

    async def list_drawn(self, limit):
        drawn = [g for g in self.items.values() if g.drawn]
        return sorted(drawn, key=lambda g: g.ends_at, reverse=True)[:limit]


@pytest.fixture
def setup(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleGiveaway": {"enabled": True}}}})

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(giveaway_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    host = guild.add_member(FakeMember(3, "alice"))
    for uid in (7, 8, 9):
        guild.add_member(FakeMember(uid))
    repo = MemoryGiveaways()
    ext = bare_extension(
        GiveawayExtension, _repo=lambda guild_id: repo, _refreshes=RecordingRefreshes()
    )
    ctx = FakeCtx(host, channel)
    ext.bot = ctx.bot
    return ext, ctx, repo, channel


class RecordingRefreshes:
    def __init__(self):
        self.requests = []

    def request(self, key, refresh):
        self.requests.append((key, refresh))


async def start(ext, ctx, *, prize, duration, winners="1", description="", **options):
    """``/concours lancer`` opens its form (B2); the host fills it in."""
    await run(GiveawayExtension.giveaway_start, ext, ctx, **options)
    if not ctx.modals:
        return
    form = ctx.modals.pop()
    submit = FakeCtx(ctx.author, ctx.channel, bot=ctx.bot, custom_id=form.custom_id)
    await run(
        GiveawayExtension.on_start_form,
        ext,
        submit,
        prize=prize,
        duration=duration,
        winners=str(winners),
        description=description,
    )
    ctx.replies.extend(submit.replies)


async def test_start_posts_the_announcement_and_stores_the_giveaway(setup):
    ext, ctx, repo, _ = setup
    await start(ext, ctx, prize="Un jeu", duration="2h", winners=2)
    (stored,) = repo.items.values()
    (announcement,) = [r for r in ctx.replies if r.get("embeds")]
    assert "Un jeu" in announcement["embeds"][0].title
    buttons = announcement["components"][0].components
    assert [b.custom_id for b in buttons] == ["giveaway:enter", "giveaway:leave"]
    assert buttons[0].emoji.name == "🎉"
    assert stored.winners_count == 2 and stored.host_id == "3"
    assert stored.ends_at - clock.now() == pytest.approx(
        timedelta(hours=2), abs=timedelta(seconds=5)
    )
    assert stored.ends_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("duration", "error"),
    [
        ("bientôt", "Je n'ai pas compris la durée « bientôt »"),
        ("5s", "Durée minimale"),
        ("90d", "maximale"),
    ],
)
async def test_start_refuses_a_bad_duration(setup, duration, error):
    ext, ctx, repo, _ = setup
    await start(ext, ctx, prize="Un jeu", duration=duration)
    assert error in ctx.last
    assert repo.items == {}


def posted(repo, channel, entrants=(), **fields):
    """A running giveaway with its announcement in *channel*, entered by *entrants*."""
    message = FakeMessage(500, embeds=[Embed(title="🎁 GIVEAWAY — Un jeu")], channel=channel)
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
        entrants=list(entrants),
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
    # The prize is the host's text: only the winners are pinged.
    mentions = message.reply_kwargs[0]["allowed_mentions"]
    assert mentions.users == [7] and not mentions.roles and not mentions.parse
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
    assert "/concours tirer" in ctx.last


async def test_reroll_picks_someone_who_has_not_won(setup):
    ext, ctx, repo, channel = setup
    giveaway, message = posted(repo, channel, entrants=["7", "8"], drawn=True, winners=["7"])
    await run(GiveawayExtension.giveaway_reroll, ext, ctx, "500", winners=1)
    assert giveaway.winners == ["7", "8"]
    assert "<@8>" in message.replies[0]
    assert message.reply_kwargs[0]["allowed_mentions"].users == [8]


async def test_reroll_with_nobody_left(setup):
    ext, ctx, repo, channel = setup
    posted(repo, channel, entrants=["7"], drawn=True, winners=["7"])
    await run(GiveawayExtension.giveaway_reroll, ext, ctx, "500")
    assert "Pas assez de participants" in ctx.last


async def test_the_host_never_enters_unless_allowed(setup):
    ext, ctx, repo, channel = setup
    giveaway, _ = posted(repo, channel, entrants=["3"])  # only the host entered
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


async def test_a_failed_save_takes_the_announcement_down(setup):
    ext, ctx, repo, channel = setup

    async def broken(giveaway):
        raise RuntimeError("mongo down")

    repo.add = broken
    await start(ext, ctx, prize="Un jeu", duration="2h")
    assert all(m.deleted for m in channel.messages.values())
    assert "pas pu enregistrer" in ctx.last


async def click(ext, member, message, button="enter"):
    ctx = FakeCtx(member, message.channel, message=message, custom_id=f"giveaway:{button}")
    handler = GiveawayExtension.on_enter if button == "enter" else GiveawayExtension.on_leave
    await run(handler, ext, ctx)
    return ctx


async def test_a_member_enters_once_and_the_announcement_is_refreshed(setup):
    ext, _, repo, channel = setup
    giveaway, message = posted(repo, channel)
    bob = channel.guild.add_member(FakeMember(7, "bob"))

    first = await click(ext, bob, message)
    assert first.last == "🎉 Ta participation est enregistrée !"
    assert first.replies[-1]["ephemeral"] is True
    assert giveaway.entrants == ["7"]
    again = await click(ext, bob, message)
    assert again.last == "Tu participes déjà." and giveaway.entrants == ["7"]

    [(key, refresh)] = ext._refreshes.requests  # the repeated click changed nothing
    assert key == 500
    await refresh()
    (edit,) = message.edits
    participants = next(f for f in edit["embeds"][0].fields if f.name == "Participants")
    assert participants.value == "<@7>"
    assert [b.custom_id for b in edit["components"][0].components] == [
        "giveaway:enter",
        "giveaway:leave",
    ]


async def test_a_member_withdraws(setup):
    ext, _, repo, channel = setup
    giveaway, message = posted(repo, channel, entrants=["7"])
    bob = channel.guild.add_member(FakeMember(7, "bob"))
    left = await click(ext, bob, message, "leave")
    assert left.last == "Ta participation est retirée." and giveaway.entrants == []
    again = await click(ext, bob, message, "leave")
    assert again.last == "Tu ne participais pas."


async def test_the_host_cannot_enter_unless_allowed(setup):
    ext, ctx, repo, channel = setup
    giveaway, message = posted(repo, channel)
    refused = await click(ext, ctx.author, message)
    assert "organisateur ne peut pas" in refused.last and giveaway.entrants == []
    giveaway.allow_host_win = True
    await click(ext, ctx.author, message)
    assert giveaway.entrants == ["3"]


@pytest.mark.parametrize(
    "fields",
    [{"drawn": True}, {"cancelled": True}, {"ends_at": clock.now() - timedelta(seconds=1)}],
)
async def test_a_closed_giveaway_takes_no_entries(setup, fields):
    ext, _, repo, channel = setup
    giveaway, message = posted(repo, channel)
    for name, value in fields.items():
        setattr(giveaway, name, value)
    bob = channel.guild.add_member(FakeMember(7, "bob"))
    late = await click(ext, bob, message)
    assert late.last == "Ce giveaway est terminé." and giveaway.entrants == []
    assert ext._refreshes.requests == []


async def test_buttons_where_giveaways_are_off(setup, store_config):
    ext, _, repo, channel = setup
    _, message = posted(repo, channel)
    store_config({"servers": {}})
    bob = channel.guild.add_member(FakeMember(7, "bob"))
    off = await click(ext, bob, message)
    assert "Giveaways n'est pas activé" in off.last and "tableau de bord" in off.last


async def test_cancel_removes_the_buttons(setup):
    ext, ctx, repo, channel = setup
    _, message = posted(repo, channel)
    await run(GiveawayExtension.giveaway_cancel, ext, ctx, "500")
    assert message.edits[-1]["components"] == []


def typing(ctx, text=""):
    ctx.input_text = text
    return ctx


async def test_end_and_cancel_offer_the_open_giveaways(setup):
    ext, ctx, repo, channel = setup
    posted(repo, channel)
    drawn, _ = posted(repo, channel, drawn=True)
    drawn.message_id, drawn.prize = "501", "Un vieux jeu"
    await run(GiveawayExtension.open_giveaway_choices, ext, typing(ctx))
    (choice,) = ctx.replies[-1]["choices"]
    assert choice["value"] == "500" and choice["name"].startswith("Un jeu · fin le ")

    await run(GiveawayExtension.drawn_giveaway_choices, ext, typing(ctx, "vieux"))
    assert [c["value"] for c in ctx.replies[-1]["choices"]] == ["501"]
    await run(GiveawayExtension.drawn_giveaway_choices, ext, typing(ctx, "500"))
    assert ctx.replies[-1]["choices"] == []


async def test_no_choices_where_giveaways_are_off(setup, store_config):
    ext, ctx, repo, channel = setup
    posted(repo, channel)
    store_config({"servers": {}})
    await run(GiveawayExtension.open_giveaway_choices, ext, typing(ctx))
    assert ctx.replies[-1]["choices"] == []


@pytest.mark.parametrize("winners", ["0", "deux", "99"])
async def test_the_form_refuses_a_winner_count_it_cannot_use(setup, winners):
    ext, ctx, repo, _ = setup
    await start(ext, ctx, prize="Un jeu", duration="2h", winners=winners)
    assert ctx.last.startswith(f"❌ Je n'ai pas compris « {winners} » gagnants")
    assert repo.items == {}


async def test_a_giveaway_for_a_role_and_a_level(setup, store_config):
    """B3: the conditions show on the announcement and are checked when a member enters."""
    from discord_fakes import FakeRole

    ext, ctx, repo, channel = setup
    store_config(
        {"servers": {"1": {"moduleGiveaway": {"enabled": True}, "moduleXp": {"enabled": True}}}}
    )
    vip = FakeRole(600, "VIP")
    await start(
        ext, ctx, prize="Un jeu", duration="3j", role=vip, min_level=5, description="Bonne chance"
    )
    (stored,) = repo.items.values()
    assert (stored.required_role_id, stored.min_level) == ("600", 5)
    assert stored.description == "Bonne chance"
    (announcement,) = [r for r in ctx.replies if r.get("embeds")]
    fields = {f.name: f.value for f in announcement["embeds"][0].fields}
    assert fields["Pour participer"] == "Rôle <@&600> · niveau 5 ou plus"

    message = FakeMessage(int(stored.message_id), channel=channel)
    levels = {"7": 9, "8": 2}

    async def member_level(guild_id, user_id):
        return levels.get(user_id, 0)

    ext._member_level = member_level
    outsider = channel.guild.add_member(FakeMember(9, "eve"))
    refused = await click(ext, outsider, message)
    assert refused.last == "🔒 Ce giveaway est réservé au rôle <@&600>."
    newcomer = channel.guild.add_member(FakeMember(8, "dan", roles=[vip]))
    low = await click(ext, newcomer, message)
    assert low.last.startswith("🔒 Ce giveaway demande le niveau 5 : tu es niveau 2.")
    regular = channel.guild.add_member(FakeMember(7, "bob", roles=[vip]))
    await click(ext, regular, message)
    assert stored.entrants == ["7"]


async def test_a_minimum_level_needs_the_xp_module(setup):
    ext, ctx, repo, _ = setup
    await run(GiveawayExtension.giveaway_start, ext, ctx, min_level=5)
    assert ctx.modals == [] and "système d'XP n'est pas actif" in ctx.last


async def test_the_draw_skips_entrants_who_left_or_lost_the_role(setup):
    """P14: the role is checked again at the draw, on the entrants drawn only."""
    ext, ctx, repo, channel = setup
    guild = channel.guild
    vip = FakeRole(55)
    guild.get_member(8).roles = [vip]
    guild.members.pop(9)  # 9 left the server
    giveaway, message = posted(
        repo, channel, entrants=["7", "8", "9"], required_role_id="55", winners_count=3
    )
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    assert giveaway.winners == ["8"]  # 7 lost the role, 9 is gone


async def test_a_reroll_also_skips_who_left(setup):
    ext, ctx, repo, channel = setup
    channel.guild.members.pop(8)
    giveaway, message = posted(repo, channel, entrants=["7", "8", "9"], drawn=True, winners=["7"])
    await run(GiveawayExtension.giveaway_reroll, ext, ctx, "500", winners=2)
    assert giveaway.winners == ["7", "9"]


async def test_an_unreadable_member_still_wins(setup, monkeypatch):
    """Discord not answering is no reason to lose a prize."""
    ext, ctx, repo, channel = setup
    guild = channel.guild
    guild.members.pop(7)

    async def outage(uid):
        raise RuntimeError("503")

    guild.fetch_member = outage
    giveaway, _ = posted(repo, channel, entrants=["7"])
    await run(GiveawayExtension.giveaway_end, ext, ctx, "500")
    assert giveaway.winners == ["7"]
