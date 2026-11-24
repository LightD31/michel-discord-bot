"""🚩 Signaler: a member reports a message, a moderator handles it from the card.

And the moderators' right-click menus on a member, which show the reports.

The extension runs on the fakes of ``discord_fakes`` with in-memory stores;
``test_mongo_reports.py`` checks the real repository's updates.
"""

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
    discord_error,
    run,
)
from interactions import Permissions
from test_moderation_flows import MemoryInfractions

from extensions.moderation import ModerationExtension
from extensions.moderation.reports import quote_of, report_card
from features.moderation import MessageReport, ReportLimiter
from features.moderation.reports import KEEP_FOR, Reporter
from src.core import clock


class MemoryReports:
    def __init__(self):
        self.docs: dict[str, MessageReport] = {}

    async def add(self, *, message_id, channel_id, author_id, reporter_id, reason, now):
        doc = self.docs.get(message_id)
        if doc is None:
            doc = self.docs[message_id] = MessageReport(
                _id=message_id,
                channel_id=channel_id,
                author_id=author_id,
                created_at=now,
                expires_at=now + KEEP_FOR,
            )
        elif any(r.user_id == reporter_id for r in doc.reporters):
            return None
        doc.reporters.append(Reporter(user_id=reporter_id, reason=reason, at=now))
        return doc.model_copy(deep=True)

    async def get(self, message_id):
        doc = self.docs.get(message_id)
        return doc.model_copy(deep=True) if doc else None

    async def set_card(self, message_id, channel_id, card_id):
        self.docs[message_id].card_channel_id = channel_id
        self.docs[message_id].card_message_id = card_id

    async def claim(self, message_id, status, handler_id, now):
        doc = self.docs.get(message_id)
        if doc is None or not doc.is_open:
            return None
        doc.status, doc.handled_by, doc.handled_at = status, handler_id, now
        return doc.model_copy(deep=True)

    async def reopen(self, message_id, status):
        doc = self.docs[message_id]
        if doc.status == status:
            doc.status, doc.handled_by, doc.handled_at = "open", None, None

    async def set_case(self, message_id, case_id):
        self.docs[message_id].case_id = case_id

    async def count_against(self, author_id):
        return sum(d.author_id == author_id for d in self.docs.values())


SETTINGS = {"enabled": True, "modLogChannelId": "20", "dmOnAction": True}


@pytest.fixture
def world(store_config):
    store_config({"servers": {"1": {"moduleModeration": dict(SETTINGS)}}})
    guild = FakeGuild(1, "Les Copains")
    general = guild.add_channel(FakeChannel(10, "général"))
    modlog = guild.add_channel(FakeChannel(20, "modlog"))
    modo_role = FakeRole(name="Modo", position=10)
    mod = guild.add_member(
        FakeMember(
            3,
            "modo",
            roles=[modo_role],
            permissions=Permissions.MODERATE_MEMBERS
            | Permissions.MANAGE_MESSAGES
            | Permissions.VIEW_CHANNEL,
        )
    )
    author = guild.add_member(FakeMember(7, "bob", roles=[FakeRole(name="Membre", position=2)]))
    alice = guild.add_member(FakeMember(8, "alice"))
    carol = guild.add_member(FakeMember(9, "carol"))
    message = FakeMessage(500, content="t'es nul\n@everyone", author=author, channel=general)
    message.attachments = [object()]
    general.messages[500] = message
    infractions, reports = MemoryInfractions(), MemoryReports()
    ext = bare_extension(
        ModerationExtension,
        None,
        repository=lambda gid: infractions,
        reports=lambda gid: reports,
        report_limits=ReportLimiter(),
    )
    import asyncio

    ext.report_lock = asyncio.Lock()

    def ctx_for(member, *, custom_id="", target=None, channel=general):
        ctx = FakeCtx(member, channel, custom_id=custom_id)
        ext.bot = ctx.bot
        ctx.target = target
        return ctx

    return {
        "ext": ext,
        "guild": guild,
        "general": general,
        "modlog": modlog,
        "mod": mod,
        "author": author,
        "alice": alice,
        "carol": carol,
        "message": message,
        "reports": reports,
        "infractions": infractions,
        "ctx": ctx_for,
    }


async def report(w, member, reason="Il insulte"):
    ext = w["ext"]
    menu = w["ctx"](member, target=w["message"])
    await run(ModerationExtension.report_message, ext, menu)
    if not menu.modals:
        return menu
    form = w["ctx"](member, custom_id=menu.modals[0].custom_id)
    await run(ModerationExtension.on_report_form, ext, form, reason=reason)
    return form


async def click(w, member, action):
    ctx = w["ctx"](member, custom_id=f"report:{action}:500", channel=w["modlog"])
    await run(ModerationExtension.on_report_button, w["ext"], ctx)
    return ctx


def card(w):
    """The report's card: the first message in the mod-log channel (the cases follow it)."""
    return w["modlog"].sent[0]


async def test_a_report_posts_one_card_and_thanks_the_member(world):
    w = world
    form = await report(w, w["alice"])
    assert "Merci, c'est transmis" in form.last and form.replies[-1]["ephemeral"]
    (sent,) = w["modlog"].sent
    embed = sent.embeds[0]
    assert "> t'es nul" in embed.description and "📎 1 pièce jointe" in embed.description
    fields = {f.name: f.value for f in embed.fields}
    assert "<@7>" in fields["Auteur"] and fields["Salon"] == "<#10>"
    assert "<@8> — Il insulte" in fields["Signalé par"]
    assert fields["Statut"] == "🟥 À traiter"
    # No content: an embed never pings, the quoted @everyone included.
    assert sent.content is None
    labels = [b.label for row in sent.components for b in row.components]
    assert labels == ["Aller au message", "Supprimer", "Avertir", "Exclure 10 min", "Classer"]
    assert sent.components[0].components[0].url.endswith("/1/10/500")


async def test_a_second_report_raises_the_counter_on_the_same_card(world):
    w = world
    await report(w, w["alice"])
    await report(w, w["carol"], reason="Spam")
    (sent,) = w["modlog"].sent  # still one card
    embed = sent.embeds[0]
    assert embed.title == "🚩 Message signalé ×2"
    fields = {f.name: f.value for f in embed.fields}
    assert "<@9> — Spam" in fields["Signalé par 2 membres"]
    assert "> t'es nul" in embed.description  # the quote survives the edit


async def test_reporting_twice_or_ones_own_message_is_refused(world):
    w = world
    await report(w, w["alice"])
    again = await report(w, w["alice"])
    assert again.modals == [] and "déjà signalé" in again.last
    own = await report(w, w["author"])
    assert own.modals == [] and "propre message" in own.last
    assert len(w["reports"].docs["500"].reporters) == 1


async def test_a_member_reporting_too_fast_is_asked_to_wait(world):
    w = world
    limits = w["ext"].report_limits
    for _ in range(limits.limit):
        limits.record("1", "8", clock.now())
    menu = await report(w, w["alice"])
    assert menu.modals == [] and "patiente" in menu.last


def test_the_limiter_forgets_after_its_window():
    limits = ReportLimiter(limit=2, window=timedelta(minutes=10))
    now = clock.now()
    limits.record("1", "8", now)
    limits.record("1", "8", now)
    assert limits.is_limited("1", "8", now)
    assert not limits.is_limited("1", "9", now)  # per member
    assert not limits.is_limited("2", "8", now)  # and per server
    assert not limits.is_limited("1", "8", now + timedelta(minutes=10))


async def test_without_a_modlog_channel_the_member_is_told_who_can_fix_it(world, store_config):
    store_config({"servers": {"1": {"moduleModeration": {"enabled": True}}}})
    w = world
    menu = await report(w, w["alice"])
    assert menu.modals == [] and "Salon des logs de modération" in menu.last


async def test_delete_removes_the_message_and_closes_the_card(world):
    w = world
    await report(w, w["alice"])
    ctx = await click(w, w["mod"], "delete")
    assert w["message"].deleted, ctx.replies
    assert "supprimé" in ctx.last
    embed = card(w).embeds[0]
    status = {f.name: f.value for f in embed.fields}["Statut"]
    assert status.startswith("🗑️ Message supprimé par <@3>") and "cas #1" in status
    labels = [b.label for row in card(w).edits[-1]["components"] for b in row.components]
    assert labels == ["Aller au message"]  # handled: only the link is left
    (case,) = w["infractions"].items.values()
    assert (case.type, case.user_id) == ("note", "7")
    assert "Cas #1" in w["modlog"].sent[1].embeds[0].title  # logged like any case
    # The reporter is thanked, without the outcome.
    assert "les modérateurs l'ont traité" in w["alice"].dms[-1]
    assert "supprim" not in w["alice"].dms[-1]


async def test_a_card_is_handled_once(world):
    w = world
    await report(w, w["alice"])
    await click(w, w["mod"], "dismiss")
    late = await click(w, w["mod"], "delete")
    assert "Déjà traité" in late.last and not w["message"].deleted


async def test_warn_asks_for_a_reason_then_warns_the_author(world):
    w = world
    await report(w, w["alice"])
    ctx = await click(w, w["mod"], "warn")
    (form,) = ctx.modals
    assert form.custom_id == "report_reason:warn:500"
    submit = w["ctx"](w["mod"], custom_id=form.custom_id, channel=w["modlog"])
    await run(ModerationExtension.on_report_action_form, w["ext"], submit, reason="Reste poli")
    (case,) = w["infractions"].items.values()
    assert (case.type, case.user_id, case.reason) == ("warn", "7", "Reste poli")
    assert "avertissement" in w["author"].dms[-1] and "Reste poli" in w["author"].dms[-1]
    # The author's DM never says who reported them.
    assert "alice" not in " ".join(w["author"].dms)
    status = {f.name: f.value for f in card(w).embeds[0].fields}["Statut"]
    assert status.startswith("⚠️ Auteur averti par <@3>")


async def test_timeout_excludes_ten_minutes(world):
    w = world
    await report(w, w["alice"])
    ctx = await click(w, w["mod"], "timeout")
    submit = w["ctx"](w["mod"], custom_id=ctx.modals[0].custom_id, channel=w["modlog"])
    await run(ModerationExtension.on_report_action_form, w["ext"], submit)
    (until,) = w["author"].timeouts
    assert timedelta(minutes=9) < until - clock.now() <= timedelta(minutes=10)
    (case,) = w["infractions"].items.values()
    assert (case.type, case.duration_seconds) == ("timeout", 600)


async def test_a_refused_timeout_reopens_the_report(world):
    w = world
    await report(w, w["alice"])

    async def refuse(**_):
        raise discord_error()

    w["author"].timeout = refuse
    ctx = await click(w, w["mod"], "timeout")
    submit = w["ctx"](w["mod"], custom_id=ctx.modals[0].custom_id, channel=w["modlog"])
    await run(ModerationExtension.on_report_action_form, w["ext"], submit)
    assert "je n'ai pas les droits" in submit.last
    assert w["reports"].docs["500"].is_open and w["infractions"].items == {}


async def test_each_button_needs_the_rights_it_uses(world):
    w = world
    await report(w, w["alice"])
    member = w["carol"]
    for action in ("delete", "warn", "timeout", "dismiss"):
        ctx = await click(w, member, action)
        assert ctx.last.startswith("🔒") and ctx.modals == [], action
    # Deleting needs "Gérer les messages" in the message's channel, not elsewhere.
    w["general"].overrides[w["mod"].id] = Permissions.VIEW_CHANNEL
    ctx = await click(w, w["mod"], "delete")
    assert "« Gérer les messages » dans <#10>" in ctx.last and not w["message"].deleted
    assert w["reports"].docs["500"].is_open


async def test_a_moderator_cannot_sanction_someone_above_them(world):
    w = world
    w["author"].roles.append(FakeRole(name="Admin", position=20))
    await report(w, w["alice"])
    ctx = await click(w, w["mod"], "warn")
    assert ctx.modals == [] and "rang égal ou supérieur" in ctx.last


def test_the_quote_is_clipped_and_counts_attachments():
    assert quote_of("") == "*(message sans texte)*"
    assert quote_of("a\nb", 2) == "> a\n> b\n📎 2 pièces jointes"
    assert len(quote_of("x" * 5000)) < 1100


def test_more_than_five_reporters_are_summed_up():
    now = clock.now()
    doc = MessageReport(
        _id="1",
        channel_id="2",
        author_id="3",
        created_at=now,
        expires_at=now,
        reporters=[Reporter(user_id=str(10 + i), reason="r", at=now) for i in range(7)],
    )
    embed, _ = report_card(doc, "> x", "1")
    value = {f.name: f.value for f in embed.fields}["Signalé par 7 membres"]
    assert value.count("<@") == 5 and value.endswith("… et 2 autres")


# ── Right-click a member: 🛡️ Historique and ⚠️ Avertir… ────────────────


async def test_history_lists_cases_and_reports(world):
    w = world
    await report(w, w["alice"])
    ctx = w["ctx"](w["mod"], target=w["author"])
    await run(ModerationExtension.history_menu, w["ext"], ctx)
    assert "Aucune sanction" in ctx.last and "🚩 1 message signalé" in ctx.last

    warn = w["ctx"](w["mod"], target=w["author"])
    await run(ModerationExtension.warn_menu, w["ext"], warn)
    (form,) = warn.modals
    assert form.custom_id == "modwarn:7" and form.title == "Avertir bob"
    submit = w["ctx"](w["mod"], custom_id=form.custom_id)
    await run(ModerationExtension.on_warn_form, w["ext"], submit, reason="Calme-toi")
    assert "averti (cas **#1**)" in submit.last
    assert "Calme-toi" in w["author"].dms[-1]

    again = w["ctx"](w["mod"], target=w["author"])
    await run(ModerationExtension.history_menu, w["ext"], again)
    (embed,) = again.replies[-1]["embeds"]
    assert "1 sanction(s)" in embed.description and "🚩 1 message signalé" in embed.description


async def test_the_menus_are_for_moderators_only(world):
    w = world
    for menu in (ModerationExtension.history_menu, ModerationExtension.warn_menu):
        assert menu.default_member_permissions == Permissions.MODERATE_MEMBERS
        # A server can loosen the default: the right is checked again.
        ctx = w["ctx"](w["carol"], target=w["author"])
        await run(menu, w["ext"], ctx)
        assert ctx.last.startswith("🔒") and ctx.modals == []
    # The form itself checks too.
    submit = w["ctx"](w["carol"], custom_id="modwarn:7")
    await run(ModerationExtension.on_warn_form, w["ext"], submit)
    assert submit.last.startswith("🔒") and w["infractions"].items == {}


async def test_warn_follows_the_role_hierarchy(world):
    w = world
    ctx = w["ctx"](w["mod"], target=w["mod"])
    await run(ModerationExtension.warn_menu, w["ext"], ctx)
    assert "toi-même" in ctx.last and ctx.modals == []
