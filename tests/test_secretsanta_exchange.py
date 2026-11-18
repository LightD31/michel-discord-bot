"""After the draw: anonymous messages, reports, parcel marks and the reminder (P2).

Every button here is clicked in a DM, so the bot finds the other side from the
draw itself; no custom id may name a member (anyone can read the ids of the
buttons they receive, and the recipient's would give their Santa away).
"""

from datetime import date

import pytest
from discord_fakes import FakeBot, FakeCtx, FakeGuild, FakeMember, FakeMessage, bare_extension, run
from interactions import Embed
from santa_fakes import MemorySanta

from extensions.secretsanta import SecretSantaExtension
from extensions.secretsanta.exchange import NO_PARCELS, NOT_IN_DRAW, RELAY_OFF
from features.secretsanta import SecretSantaSession
from features.secretsanta.relay import BURST, RelayLimiter
from src.core import clock
from src.discord_ext.mentions import NO_PINGS

# 3 gives to 4, 4 to 5, 5 to 6, 6 to 3; 3 organises.
RESULTS = [(3, 4), (4, 5), (5, 6), (6, 3)]


@pytest.fixture
def santa(store_config):
    store_config({"servers": {"1": {"moduleSecretSanta": {"enabled": True}}}})
    guild = FakeGuild(1, "Les Lutins")
    members = {uid: guild.add_member(FakeMember(uid, f"m{uid}")) for uid in (3, 4, 5, 6, 9)}
    bot = FakeBot(guild)
    repo = MemorySanta(
        SecretSantaSession(
            context_id="guild_1",
            channel_id=10,
            created_by=3,
            participants=[3, 4, 5, 6],
            is_drawn=True,
            exchange_date="2026-12-24",
        )
    )
    repo.results = list(RESULTS)
    ext = bare_extension(SecretSantaExtension, bot, repository=repo, relay_limiter=RelayLimiter())

    def dm_ctx(uid, custom_id, message=None):
        return FakeCtx(members[uid], None, bot=bot, custom_id=custom_id, message=message)

    return ext, repo, members, dm_ctx


def answer(ctx):
    reply = ctx.replies[-1]
    assert reply["ephemeral"] is True
    return reply["content"]


def all_custom_ids(message):
    return [b.custom_id for row in message.components or [] for b in row.components]


# ── Writing anonymously ────────────────────────────────────────────────────


async def test_the_santa_writes_and_only_their_recipient_gets_it(santa):
    ext, _, members, dm_ctx = santa
    ctx = dm_ctx(3, "secretsanta_write:guild_1")
    await run(SecretSantaExtension.on_write_button, ext, ctx)
    (modal,) = ctx.modals
    assert modal.custom_id == "secretsanta_write_modal:guild_1"

    ctx = dm_ctx(3, "secretsanta_write_modal:guild_1")
    await run(
        SecretSantaExtension.on_write_modal, ext, ctx, message="Tu préfères le thé ? @everyone"
    )
    assert answer(ctx) == "💌 Message remis, sans ton nom."
    (dm,) = members[4].dm_messages
    assert dm.embeds[0].title == "💌 Message de ton Père Noël secret"
    assert dm.embeds[0].description == "Tu préfères le thé ? @everyone"
    assert "m3" not in str(dm.embeds[0].to_dict()) and "<@3>" not in dm.embeds[0].description
    assert members[4].dm_kwargs[0]["allowed_mentions"] == NO_PINGS
    assert all_custom_ids(dm) == ["secretsanta_reply:guild_1", "secretsanta_report:guild_1:santa"]
    assert all(m.dm_messages == [] for uid, m in members.items() if uid != 4)


async def test_the_recipient_answers_their_santa(santa):
    ext, _, members, dm_ctx = santa
    ctx = dm_ctx(4, "secretsanta_reply:guild_1")
    await run(SecretSantaExtension.on_reply_button, ext, ctx)
    assert ctx.modals[0].custom_id == "secretsanta_reply_modal:guild_1"

    ctx = dm_ctx(4, "secretsanta_reply_modal:guild_1")
    await run(SecretSantaExtension.on_reply_modal, ext, ctx, message="Oui, du thé vert !")
    (dm,) = members[3].dm_messages  # 3 is 4's Santa
    assert dm.embeds[0].title == "💬 Réponse de ton destinataire"
    assert "<@4>" in dm.embeds[0].description and "du thé vert" in dm.embeds[0].description
    assert all_custom_ids(dm) == [
        "secretsanta_write:guild_1",
        "secretsanta_report:guild_1:recipient",
    ]


async def test_someone_outside_the_draw_cannot_write(santa):
    ext, _, members, dm_ctx = santa
    ctx = dm_ctx(9, "secretsanta_write:guild_1")
    await run(SecretSantaExtension.on_write_button, ext, ctx)
    assert answer(ctx) == NOT_IN_DRAW and ctx.modals == []
    ctx = dm_ctx(9, "secretsanta_reply_modal:guild_1")
    await run(SecretSantaExtension.on_reply_modal, ext, ctx, message="coucou")
    assert answer(ctx) == NOT_IN_DRAW
    assert all(m.dm_messages == [] for m in members.values())


async def test_a_server_can_switch_the_relay_off(santa, store_config):
    store_config(
        {"servers": {"1": {"moduleSecretSanta": {"enabled": True, "anonymousMessages": False}}}}
    )
    ext, _, members, dm_ctx = santa
    ctx = dm_ctx(3, "secretsanta_write:guild_1")
    await run(SecretSantaExtension.on_write_button, ext, ctx)
    assert answer(ctx) == RELAY_OFF and ctx.modals == []
    ctx = dm_ctx(3, "secretsanta_write_modal:guild_1")
    await run(SecretSantaExtension.on_write_modal, ext, ctx, message="coucou")
    assert answer(ctx) == RELAY_OFF and members[4].dm_messages == []


async def test_the_relay_stops_a_flood(santa):
    ext, _, members, dm_ctx = santa
    for i in range(BURST):
        ctx = dm_ctx(3, "secretsanta_write_modal:guild_1")
        await run(SecretSantaExtension.on_write_modal, ext, ctx, message=f"message {i}")
    ctx = dm_ctx(3, "secretsanta_write_modal:guild_1")
    await run(SecretSantaExtension.on_write_modal, ext, ctx, message="encore un")
    assert answer(ctx).startswith("Doucement !") and "<t:" in answer(ctx)
    assert len(members[4].dm_messages) == BURST


async def test_closed_dms_are_told_without_counting(santa):
    ext, _, members, dm_ctx = santa
    members[4].dms_closed = True
    ctx = dm_ctx(3, "secretsanta_write_modal:guild_1")
    await run(SecretSantaExtension.on_write_modal, ext, ctx, message="coucou")
    assert "n'a pas pu être remis" in answer(ctx)
    assert ext.relay_limiter.retry_at(("guild_1", 3, "write"), clock.now()) is None


async def test_a_message_after_the_session_was_replaced_is_refused(santa):
    ext, repo, members, dm_ctx = santa
    repo.session.is_drawn = False
    repo.results = None
    ctx = dm_ctx(3, "secretsanta_write_modal:guild_1")
    await run(SecretSantaExtension.on_write_modal, ext, ctx, message="coucou")
    assert answer(ctx) == "Ce tirage n'existe plus."


# ── Reporting ──────────────────────────────────────────────────────────────


def relayed(text):
    return FakeMessage(embeds=[Embed(title="💌", description=text)])


async def test_a_report_reaches_the_organiser_with_the_sender(santa):
    ext, _, members, dm_ctx = santa
    # 5 reports a message from their Santa, 4; the organiser is 3.
    ctx = dm_ctx(5, "secretsanta_report:guild_1:santa", message=relayed("Un message déplacé"))
    await run(SecretSantaExtension.on_report_button, ext, ctx)
    assert answer(ctx).startswith("🚩 Message signalé")
    (dm,) = members[3].dm_messages
    text = dm.embeds[0].description
    assert "Signalé par :** <@5>" in text and "Envoyé par :** <@4>" in text
    assert "Un message déplacé" in text
    assert members[3].dm_kwargs[0]["allowed_mentions"] == NO_PINGS


async def test_a_report_on_a_reply_names_the_recipient(santa):
    ext, _, members, dm_ctx = santa
    # 5 reports the reply of their recipient, 6.
    ctx = dm_ctx(5, "secretsanta_report:guild_1:recipient", message=relayed("Réponse"))
    await run(SecretSantaExtension.on_report_button, ext, ctx)
    assert "Envoyé par :** <@6>" in members[3].dm_messages[0].embeds[0].description


async def test_a_report_about_the_organiser_is_not_sent_to_them(santa):
    ext, _, members, dm_ctx = santa
    # 4's Santa is 3, the organiser.
    ctx = dm_ctx(4, "secretsanta_report:guild_1:santa", message=relayed("Message"))
    await run(SecretSantaExtension.on_report_button, ext, ctx)
    assert "contacte un administrateur" in answer(ctx)
    assert members[3].dm_messages == []


# ── Parcel marks ───────────────────────────────────────────────────────────


async def test_an_in_person_session_has_no_parcel_marks(santa):
    ext, repo, members, dm_ctx = santa
    ctx = dm_ctx(3, "secretsanta_sent:guild_1")
    await run(SecretSantaExtension.on_sent_button, ext, ctx)
    assert answer(ctx) == NO_PARCELS and repo.people == {}
    assert members[4].dm_messages == []


async def test_sent_tells_the_recipient_and_received_thanks_the_santa(santa):
    ext, repo, members, dm_ctx = santa
    repo.session.shipping = True
    ctx = dm_ctx(3, "secretsanta_sent:guild_1")
    await run(SecretSantaExtension.on_sent_button, ext, ctx)
    assert answer(ctx) == "📦 C'est noté ! Ton destinataire est prévenu."
    (news,) = members[4].dm_messages
    assert news.embeds[0].title == "📦 Ton cadeau est en route !"
    assert all_custom_ids(news) == ["secretsanta_received:guild_1"]

    ctx = dm_ctx(3, "secretsanta_sent:guild_1")
    await run(SecretSantaExtension.on_sent_button, ext, ctx)
    assert answer(ctx) == "Ton cadeau est déjà marqué envoyé."
    assert len(members[4].dm_messages) == 1

    ctx = dm_ctx(4, "secretsanta_received:guild_1")
    await run(SecretSantaExtension.on_received_button, ext, ctx)
    assert answer(ctx) == "🎁 C'est noté, merci ! Ton Père Noël secret est prévenu."
    (thanks,) = members[3].dm_messages
    assert "<@4>" in thanks.embeds[0].description
    assert await repo.gift_marks("guild_1") == ({3}, {4})
    assert clock.to_local(repo.expiries[3]).date() == date(2027, 1, 23)


async def test_a_parcel_mark_from_outside_the_draw_is_refused(santa):
    ext, repo, _, dm_ctx = santa
    repo.session.shipping = True
    ctx = dm_ctx(9, "secretsanta_received:guild_1")
    await run(SecretSantaExtension.on_received_button, ext, ctx)
    assert answer(ctx) == NOT_IN_DRAW and repo.people == {}


# ── The reminder before the exchange ───────────────────────────────────────


def three_days_before():
    return clock.at_local(date(2026, 12, 21), 10)


async def test_the_reminder_goes_to_santas_who_have_not_sent(santa):
    ext, repo, members, _ = santa
    repo.session.shipping = True
    await repo.mark_gift("guild_1", 4, "sent_at", None)
    count = await ext.remind_parcels("guild_1", {}, three_days_before())
    assert count == 3
    assert members[4].dm_messages == []
    text = members[3].dm_messages[0].embeds[0].description
    assert text.startswith("Plus que 3 jours avant l'échange") and "<@4>" in text
    assert "secretsanta_sent:guild_1" in all_custom_ids(members[3].dm_messages[0])
    # Once per session.
    assert await ext.remind_parcels("guild_1", {}, three_days_before()) == 0
    assert len(members[3].dm_messages) == 1


async def test_the_reminder_waits_for_its_day(santa):
    ext, repo, members, _ = santa
    repo.session.shipping = True
    early = clock.at_local(date(2026, 12, 20), 12)
    assert await ext.remind_parcels("guild_1", {}, early) == 0
    # A server reminding a week ahead.
    assert await ext.remind_parcels("guild_1", {"reminderDaysBefore": 7}, early) == 4
    assert members[5].dm_messages[0].embeds[0].description.startswith("Plus que 4 jours")


@pytest.mark.parametrize("change", ["in_person", "not_drawn", "no_date"])
async def test_no_reminder_without_parcels_a_draw_or_a_date(santa, change):
    ext, repo, members, _ = santa
    repo.session.shipping = change != "in_person"
    repo.session.is_drawn = change != "not_drawn"
    if change == "no_date":
        repo.session.exchange_date = None
    assert await ext.remind_parcels("guild_1", {}, three_days_before()) == 0
    assert all(m.dm_messages == [] for m in members.values())


async def test_the_task_reminds_every_guild_and_survives_one_failing(santa, monkeypatch):
    ext, repo, members, _ = santa
    seen = []

    async def remind(context_id, settings, now):
        seen.append(context_id)
        raise RuntimeError("MongoDB down")

    monkeypatch.setattr(ext, "remind_parcels", remind)
    await SecretSantaExtension.parcel_reminders.callback(ext)
    assert seen == ["guild_1"]
