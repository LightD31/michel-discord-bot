"""/rattrapage (P20, A11): which messages, the prompt, the links, and the guards it shares with /ask."""

from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

import extensions.catchup as catchup_mod
from extensions.catchup import CatchupExtension
from features.ai import ModelAnswer
from features.ai.catchup import ChannelMessage, prompt, since_last, with_links
from src.core.errors import DatabaseError

MODELS = [
    {"api": "openrouter", "model_id": "vendor/alpha", "provider": "alpha", "display_name": "Alpha"}
]
ME = "7"


def msg(i, author="8", name="Hugo", content="salut", bot=False):
    return ChannelMessage(str(i), author, name, content, bot)


def test_since_my_last_message_without_mine_or_empty_ones():
    history = [
        msg(1),
        msg(2, ME, "Léa"),
        msg(3),
        msg(4, content="  "),
        msg(5, ME, "Léa"),
        msg(6),
        msg(7),
    ]
    assert [m.id for m in since_last(history, ME)] == ["6", "7"]
    assert [m.id for m in since_last(history[:1], ME)] == ["1"]  # never wrote: everything


def test_the_prompt_numbers_the_messages_and_drops_the_at_signs():
    messages = prompt(
        [msg(1, content="@everyone soirée ce soir ?"), msg(2, name="Zoé", content="oui !")],
        persona="Tu es Michel.",
        member="Léa",
        channel="général",
        server="Les Copains",
    )
    assert messages[0]["content"].startswith("Tu es Michel.\n\nTu résumes pour Léa")
    assert messages[1]["content"] == "[1] Hugo : everyone soirée ce soir ?\n[2] Zoé : oui !"


def test_references_become_links_and_unknown_ones_go():
    messages = [msg(10), msg(11)]
    text = with_links(
        "- Soirée ce soir [2]\n- @Hugo râle [9]", messages, lambda i: f"https://lien/{i}"
    )
    assert text == "- Soirée ce soir [↗](https://lien/11)\n- Hugo râle"


class Client:
    def __init__(self, content="- Une soirée est prévue [3]", error=None):
        self.content, self.error = content, error
        self.asked = []

    async def complete(self, model, messages, max_tokens):
        self.asked.append(messages)
        return ModelAnswer(model=model, content=self.content, error=self.error)


class Usage:
    calls: list = []
    hit = None
    down = False

    async def reserve(self, user_id, calls, limits, now):
        if Usage.down:
            raise DatabaseError("down")
        Usage.calls.append((user_id, calls))
        return Usage.hit


def discord_message(i, author):
    return SimpleNamespace(id=i, author=author, content=f"message {i}")


@pytest.fixture
def world(store_config, monkeypatch):
    servers = {"1": {"moduleIA": {"enabled": True, "catchupChannelIds": ["10"]}}}
    store_config({"config": {"AI": {"models": MODELS}}, "servers": servers})
    Usage.calls, Usage.hit, Usage.down = [], None, False
    monkeypatch.setattr(catchup_mod, "AiUsageRepository", Usage)
    monkeypatch.setattr(catchup_mod.llm, "configured_apis", lambda: ["openrouter"])
    guild = FakeGuild(1, "Les Copains")
    channel = guild.add_channel(FakeChannel(10, "général"))
    lea = guild.add_member(FakeMember(7, "Léa"))
    hugo = guild.add_member(FakeMember(8, "Hugo"))
    # 150 messages, newest first as Discord gives them: Léa wrote the 140th.
    history = [discord_message(i, lea if i == 140 else hugo) for i in range(150, 0, -1)]

    async def fetch_messages(limit=50, before=None):
        older = [m for m in history if before is None or m.id < before]
        return older[:limit]

    channel.fetch_messages = fetch_messages
    ext = bare_extension(CatchupExtension, None, ai_client=Client())
    ctx = FakeCtx(lea, channel)
    return SimpleNamespace(ext=ext, ctx=ctx, servers=servers, channel=channel, history=history)


async def test_the_summary_is_mine_alone_and_links_its_key_moment(world):
    await run(CatchupExtension.rattrapage, world.ext, world.ctx)
    (reply,) = world.ctx.replies
    assert reply["ephemeral"] is True
    embed = reply["embeds"][0]
    assert embed.title == "📚 Rattrapage de #général"
    # Since Léa's 140th: messages 141 to 150; the third is 143.
    assert embed.description == "- Une soirée est prévue [↗](https://discord.com/channels/1/10/143)"
    assert Usage.calls == [(ME, 1)]
    (asked,) = world.ext.ai_client.asked
    assert asked[1]["content"].splitlines()[0] == "[1] Hugo : message 141"


async def test_two_pages_are_read_when_i_wrote_long_ago(world):
    for m in world.history:
        m.author = world.ctx.guild.get_member(8)
    pages = await world.ext.history(world.channel)
    assert len(pages) == 150 and pages[0].id == 1 and pages[-1].id == 150


async def test_a_channel_not_opted_in_says_who_can_change_it(world):
    world.servers["1"]["moduleIA"]["catchupChannelIds"] = []
    await run(CatchupExtension.rattrapage, world.ext, world.ctx)
    assert "administrateur" in world.ctx.last and Usage.calls == []


async def test_few_messages_cost_nothing(world):
    del world.history[:7]  # Léa's message is now among the last three
    for m in world.history:
        if m.id in (147, 140):
            m.author = world.ctx.author
    await run(CatchupExtension.rattrapage, world.ext, world.ctx)
    assert "Rien à rattraper : 3 messages" in world.ctx.last
    assert Usage.calls == [] and world.ext.ai_client.asked == []


async def test_the_kill_switch_and_the_quota_are_those_of_ask(world, store_config):
    store_config({"config": {"AI": {"models": MODELS, "paused": True}}, "servers": world.servers})
    await run(CatchupExtension.rattrapage, world.ext, world.ctx)
    assert "désactivée" in world.ctx.last and world.ext.ai_client.asked == []

    store_config({"config": {"AI": {"models": MODELS}}, "servers": world.servers})
    Usage.hit = "user"
    ctx = FakeCtx(world.ctx.author, world.channel)
    await run(CatchupExtension.rattrapage, world.ext, ctx)
    assert "limite quotidienne" in ctx.last and world.ext.ai_client.asked == []

    Usage.hit, Usage.down = None, True
    ctx = FakeCtx(world.ctx.author, world.channel)
    await run(CatchupExtension.rattrapage, world.ext, ctx)
    assert "quotas" in ctx.last and world.ext.ai_client.asked == []


async def test_a_failed_answer_says_to_try_again(world):
    world.ext.ai_client = Client(content="", error="HTTP 500")
    await run(CatchupExtension.rattrapage, world.ext, world.ctx)
    assert "réessaie" in world.ctx.last


async def test_a_thread_follows_its_channel_only_when_the_admins_say_so(world):
    thread = FakeChannel(11, "fil")
    thread.guild = world.ctx.guild
    thread.parent_id = 10
    thread.fetch_messages = world.channel.fetch_messages
    ctx = FakeCtx(world.ctx.author, thread)
    await run(CatchupExtension.rattrapage, world.ext, ctx)
    assert "pas ouvert" in ctx.last and Usage.calls == []

    world.servers["1"]["moduleIA"]["catchupThreads"] = True
    ctx = FakeCtx(world.ctx.author, thread)
    await run(CatchupExtension.rattrapage, world.ext, ctx)
    assert ctx.replies[0]["embeds"][0].title == "📚 Rattrapage de #fil"
