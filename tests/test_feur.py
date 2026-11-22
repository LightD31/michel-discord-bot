"""When Feur answers: a message, or a question within it, that ends with the keyword; and /feur-stats."""

from types import SimpleNamespace

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

from extensions import feur
from extensions.feur import _should_respond
from features.feur.models import FeurStats


@pytest.mark.parametrize(
    ("content", "keyword", "expected"),
    [
        ("quoi", "quoi", True),
        ("tu fais quoi", "quoi", True),
        ("tu fais quoi ?", "quoi", True),
        ("tu fais quoi?!", "quoi", True),
        # A question ending with the keyword, then more text: this rule never
        # matched, since sentences were split on "?" before it looked for one (Q7).
        ("quoi ? t'es sérieux", "quoi", True),
        ("t'as dit quoi ?! j'ai rien compris", "quoi", True),
        ("bon. et après, quoi ? on rentre", "quoi", True),
        ("pourquoi ? je sais pas", "pourquoi", True),
        # The keyword must end the message or the question.
        ("c'est quoi ce truc ?", "quoi", False),
        ("quoi de neuf", "quoi", False),
        ("quoique", "quoi", False),
        ("pourquoi ? je sais pas", "quoi", False),
        ("rien à voir", "quoi", False),
    ],
)
def test_feur_answers_a_message_or_question_ending_with_the_keyword(content, keyword, expected):
    assert _should_respond(content, keyword) is expected


# ── The listener and /feur-stats (extensions/feur.py) ────────────────────────


class FeurRepo:
    events: list = []
    totals: list = []

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def record_event(self, user_id, kind, at):
        FeurRepo.events.append((self.guild_id, user_id, kind))

    async def get_guild_stats(self):
        return FeurStats(total=3, feur=2, pour_feur=1)

    async def get_user_stats(self, user_id):
        return FeurStats(total=1, feur=1, pour_feur=0)

    async def get_all_user_totals(self):
        return FeurRepo.totals


@pytest.fixture
def feur_guild(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleFeur": {"enabled": True}}}})
    FeurRepo.events, FeurRepo.totals = [], []
    monkeypatch.setattr(feur, "FeurRepository", FeurRepo)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    return guild, channel, bare_extension(feur.FeurExtension)


def said(guild, channel, content, *, bot=False):
    author = FakeMember(7, guild=guild)
    author.bot = bot
    message = FakeMessage(content=content, channel=channel, author=author)
    return message, SimpleNamespace(message=message)


@pytest.mark.parametrize(
    ("content", "reactions", "kind"),
    [
        ("Tu fais quoi", feur.FEUR_EMOJIS, "feur"),
        ("Mais POURQUOI", feur.POUR_FEUR_EMOJIS, "pour_feur"),
        ("on mange quoi ? des pâtes", feur.FEUR_EMOJIS, "feur"),
    ],
)
async def test_a_message_ending_with_the_keyword_gets_its_reactions(
    feur_guild, content, reactions, kind
):
    guild, channel, ext = feur_guild
    message, event = said(guild, channel, content)
    await feur.FeurExtension.on_message.callback(ext, event)
    assert message.added_reactions == reactions
    assert FeurRepo.events == [("1", "7", kind)]


@pytest.mark.parametrize(
    ("content", "bot", "enabled"),
    [
        ("quoi de neuf", False, True),  # the keyword isn't at the end
        ("Tu fais quoi", True, True),  # never a bot
        ("Tu fais quoi", False, False),  # the module is off
    ],
)
async def test_feur_stays_quiet(feur_guild, store_config, content, bot, enabled):
    guild, channel, ext = feur_guild
    store_config({"servers": {"1": {"moduleFeur": {"enabled": enabled}}}})
    message, event = said(guild, channel, content, bot=bot)
    await feur.FeurExtension.on_message.callback(ext, event)
    assert message.added_reactions == [] and FeurRepo.events == []


async def test_the_stats_list_the_top_members_still_here(feur_guild):
    guild, channel, ext = feur_guild
    for uid, name in ((11, "Léa"), (12, "Hugo")):
        guild.add_member(FakeMember(uid, name, guild=guild))
    # 13 left the server: skipped, the next one shows.
    FeurRepo.totals = [("12", 4), ("13", 9), ("11", 6)]
    ctx = FakeCtx(FakeMember(7, "Moi", guild=guild), channel)

    await run(feur.FeurExtension.feur_stats, ext, ctx)

    embed = ctx.replies[-1]["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert fields["🏆 Top 5 du serveur"] == "1. Léa: **6**\n2. Hugo: **4**"
    assert "Total : **3**" in fields["🏠 Stats du serveur"]
    assert ctx.deferred is not None  # answered after a defer: the lookups can take time
