"""The announcement's buttons: Participer (a wish-list form), 📝 Ma liste, Se retirer.

The repository's conditional updates are tested against MongoDB in
``test_mongo_secretsanta.py``; here an in-memory one plays the same rules, and
the buttons are checked for the answer, the form and the refreshed embed.
"""

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
from santa_fakes import MemorySanta

import extensions.secretsanta.sessions as sessions_module
from extensions.secretsanta import SecretSantaExtension
from extensions.secretsanta.buttons import ALREADY_IN, DRAWN, GONE, NOT_IN, WISHLIST_FIELD
from features.secretsanta import SecretSantaSession
from features.secretsanta.relay import RelayLimiter
from src.core import clock

CALLBACKS = {
    "join": SecretSantaExtension.on_join_button,
    "join_modal": SecretSantaExtension.on_join_modal,
    "wishlist": SecretSantaExtension.on_wishlist_button,
    "wishlist_modal": SecretSantaExtension.on_wishlist_modal,
    "leave": SecretSantaExtension.on_leave_button,
}


@pytest.fixture
def santa(monkeypatch):
    edits = []

    async def edit(message, **kwargs):
        edits.append(kwargs)

    monkeypatch.setattr(sessions_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    channel.messages[77] = FakeMessage(77, channel=channel)
    creator = guild.add_member(FakeMember(3, "creator"))
    member = guild.add_member(FakeMember(4, "member"))
    session = SecretSantaSession(
        context_id="guild_1",
        channel_id=10,
        message_id=77,
        created_by=3,
        participants=[3],
        budget="20 €",
        exchange_date="2026-12-24",
    )
    repo = MemorySanta(session)

    def click(who, action):
        prefix = {
            "join_modal": "secretsanta_join_modal",
            "wishlist_modal": "secretsanta_wishlist_modal",
        }.get(action, f"secretsanta_{action}")
        ctx = FakeCtx(who, channel, message=FakeMessage(78), custom_id=f"{prefix}:guild_1")
        ext = bare_extension(
            SecretSantaExtension, ctx.bot, repository=repo, relay_limiter=RelayLimiter()
        )
        return ctx, ext, CALLBACKS[action]

    return repo, creator, member, click, edits


def answer(ctx):
    (reply,) = ctx.replies
    assert reply["ephemeral"] is True
    return reply["content"]


async def test_participer_opens_the_wish_list_form(santa):
    repo, _, member, click, edits = santa
    ctx, ext, callback = click(member, "join")
    await run(callback, ext, ctx)
    (modal,) = ctx.modals
    assert modal.custom_id == "secretsanta_join_modal:guild_1"
    (field,) = modal.components
    assert field.custom_id == WISHLIST_FIELD and field.required is False
    assert repo.session.participants == [3] and ctx.replies == []  # the form joins


async def test_sending_the_form_joins_with_the_wish_list(santa):
    repo, _, member, click, edits = santa
    ctx, ext, callback = click(member, "join_modal")
    await run(callback, ext, ctx, envies="  Des chaussettes, taille 42  ")
    assert repo.session.participants == [3, 4]
    assert repo.people[4].wishlist == "Des chaussettes, taille 42"
    # Deleted 30 days after the exchange.
    assert clock.to_local(repo.expiries[4]).date().isoformat() == "2027-01-23"
    assert answer(ctx).startswith("Tu es inscrit·e au Père Noël Secret avec ta liste")
    assert "messages privés" in answer(ctx)
    description = edits[-1]["embed"].description
    assert "Participants (2)" in description and "20 €" in description
    assert "jeudi 24 décembre 2026" in description


async def test_the_wish_list_is_optional(santa):
    repo, _, member, click, _ = santa
    ctx, ext, callback = click(member, "join_modal")
    await run(callback, ext, ctx, envies="   ")
    assert repo.session.participants == [3, 4] and 4 not in repo.people
    assert answer(ctx).startswith("Tu es inscrit·e au Père Noël Secret ! 🎁")


async def test_joining_twice_or_after_the_draw_changes_nothing(santa):
    repo, creator, member, click, edits = santa
    ctx, ext, callback = click(creator, "join")
    await run(callback, ext, ctx)
    assert answer(ctx) == ALREADY_IN and ctx.modals == []

    repo.session.is_drawn = True
    ctx, ext, callback = click(member, "join")
    await run(callback, ext, ctx)
    assert answer(ctx) == DRAWN
    assert repo.session.participants == [3] and edits == []


async def test_a_form_sent_after_the_draw_says_so(santa):
    repo, _, member, click, edits = santa
    repo.lose_race_to_draw = True
    ctx, ext, callback = click(member, "join_modal")
    await run(callback, ext, ctx, envies="un livre")
    assert answer(ctx) == DRAWN
    assert repo.session.participants == [3] and edits == [] and repo.people == {}


async def test_ma_liste_is_prefilled_and_saved(santa):
    repo, creator, _, click, _ = santa
    await repo.set_wishlist("guild_1", 3, "un livre", None)
    ctx, ext, callback = click(creator, "wishlist")
    await run(callback, ext, ctx)
    (modal,) = ctx.modals
    assert modal.custom_id == "secretsanta_wishlist_modal:guild_1"
    assert modal.components[0].value == "un livre"

    ctx, ext, callback = click(creator, "wishlist_modal")
    await run(callback, ext, ctx, envies="deux livres")
    assert repo.people[3].wishlist == "deux livres"
    assert "enregistrée" in answer(ctx)


async def test_ma_liste_is_for_participants_until_the_draw(santa):
    repo, creator, member, click, _ = santa
    ctx, ext, callback = click(member, "wishlist")
    await run(callback, ext, ctx)
    assert answer(ctx) == NOT_IN and ctx.modals == []

    ctx, ext, callback = click(member, "wishlist_modal")
    await run(callback, ext, ctx, envies="tout")
    assert answer(ctx) == NOT_IN and 4 not in repo.people

    repo.session.is_drawn = True
    ctx, ext, callback = click(creator, "wishlist_modal")
    await run(callback, ext, ctx, envies="trop tard")
    assert answer(ctx) == DRAWN and 3 not in repo.people


async def test_leaving_removes_the_member_and_their_list(santa):
    repo, creator, _, click, edits = santa
    await repo.set_wishlist("guild_1", 3, "un livre", None)
    ctx, ext, callback = click(creator, "leave")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Tu as été retiré·e du Père Noël Secret."
    assert repo.session.participants == [] and repo.people[3].wishlist is None
    assert "Aucun participant" in edits[-1]["embed"].description


async def test_leaving_without_taking_part_or_after_the_draw_is_refused(santa):
    repo, creator, member, click, _ = santa
    ctx, ext, callback = click(member, "leave")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Tu ne participes pas à cette session."

    repo.session.is_drawn = True
    ctx, ext, callback = click(creator, "leave")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Le tirage a déjà été effectué, tu ne peux plus te retirer."
    assert repo.session.participants == [3]


@pytest.mark.parametrize("action", ["join", "wishlist", "leave"])
async def test_a_deleted_session_is_reported(santa, action):
    repo, _, member, click, _ = santa
    repo.session = None
    ctx, ext, callback = click(member, action)
    await run(callback, ext, ctx)
    assert answer(ctx) == GONE


async def test_in_a_group_dm_the_answer_goes_by_dm(santa):
    repo, _, member, click, edits = santa
    repo.session.context_id = "channel_10"
    ctx, ext, callback = click(member, "join_modal")
    ctx.guild = None
    await run(callback, ext, ctx, envies="")
    assert ctx.replies == []  # an ephemeral would only show to the DM's creator
    assert member.dms[0].startswith("🎅 **Père Noël Secret**\nTu es inscrit·e")
    assert edits == []  # no guild embed to refresh
