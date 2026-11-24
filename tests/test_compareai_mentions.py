"""Michel answering a mention (extensions/compareai/mentions.py): the guardrails.

The rules a message must pass are unit-tested in ``test_ai_mentions.py``
(``mention_question``); here the listener around them: the module switch, the
cooldown, the quota, the reply chain and an answer that never pings.
"""

from types import SimpleNamespace

import pytest

from extensions.compareai.mentions import (
    COOLDOWN_EMOJI,
    FAILURE_EMOJI,
    QUOTA_EMOJI,
    MentionsMixin,
)
from features.ai import Cooldown, RecentAnswers
from src.discord_ext.mentions import NO_PINGS

BOT_ID = 99


class Channel:
    name = "général"

    def __init__(self):
        self.sent = []
        self.typing = 0

    async def trigger_typing(self):
        self.typing += 1

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=1000 + len(self.sent))


class Message:
    def __init__(self, content, *, author_bot=False, everyone=False, reply_to=None, mid=500):
        self.id = mid
        self.content = content
        self.guild = SimpleNamespace(id=1, name="Les Copains", me=SimpleNamespace(roles=[]))
        self.author = SimpleNamespace(id=7, bot=author_bot, display_name="Léa")
        self.webhook_id = None
        self.mention_everyone = everyone
        self.channel = Channel()
        self.reactions = []
        self.referenced = reply_to
        self.message_reference = (
            SimpleNamespace(message_id=reply_to.id) if reply_to is not None else None
        )

    async def fetch_referenced_message(self):
        return self.referenced

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


def harness(*, answer="Bonjour !", models=("m",), over_quota=False):
    ext = object.__new__(MentionsMixin)
    ext.bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID))
    ext.recent_answers = RecentAnswers()
    ext.mention_cooldown = Cooldown()
    ext.calls = []

    async def usable_models(guild_id):
        return list(models)

    async def reserve_calls(user_id, count, limits):
        ext.calls.append(("reserve", user_id, count))
        return "quota" if over_quota else None

    async def gather_history(channel, limit, repo, *, before, extra):
        ext.calls.append(("history", before, [m.content for m in extra]))
        return []

    def build_messages(settings, **parts):
        ext.calls.append(("build", parts["question"], parts["server"], parts["author"]))
        return ["prompt"]

    async def answer_once(candidates, settings, messages, *, user_id, limits, guild_id):
        return SimpleNamespace(content=answer) if answer else None

    ext._usable_models = usable_models
    ext._reserve_calls = reserve_calls
    ext._gather_history = gather_history
    ext._build_messages = build_messages
    ext._answer_once = answer_once
    ext._resolve_mentions = lambda guild_id, text: text
    return ext


@pytest.fixture
def enabled(store_config):
    def configure(**module):
        store_config(
            {
                "config": {"AI": module.pop("ai", {})},
                "servers": {"1": {"moduleIA": {"enabled": True, **module}}},
            }
        )

    configure(mentionReplies=True, mentionCooldownSeconds=60)
    return configure


async def mention(ext, message):
    await MentionsMixin.on_ai_mention.callback(ext, SimpleNamespace(message=message))


async def test_a_mention_is_answered_without_pinging_anyone(enabled):
    ext = harness()
    message = Message(f"<@{BOT_ID}> c'est quoi un Zevent ?")

    await mention(ext, message)

    assert message.channel.sent == [
        ("Bonjour !", {"reply_to": message, "allowed_mentions": NO_PINGS})
    ]
    assert ("build", "c'est quoi un Zevent ?", "Les Copains", "Léa") in ext.calls
    assert ("reserve", "7", 1) in ext.calls  # one call counted against the quota
    assert "1001" in ext.recent_answers  # a reply to it continues the conversation


async def test_a_reply_to_an_answer_reads_the_chain(enabled):
    ext = harness()
    answer = Message("Le Zevent est un marathon caritatif.", mid=400)
    ext.recent_answers.add("400")
    follow_up = Message("Et il dure combien de temps ?", reply_to=answer)

    await mention(ext, follow_up)

    assert ("history", 500, ["Le Zevent est un marathon caritatif."]) in ext.calls
    assert follow_up.channel.sent


@pytest.mark.parametrize(
    "message",
    [
        Message("bonjour tout le monde"),  # no mention, no reply
        Message(f"<@{BOT_ID}> salut", author_bot=True),  # bots never
        Message(f"@everyone <@{BOT_ID}> salut", everyone=True),
        Message(f"<@&123> <@{BOT_ID}> salut"),  # a role ping is for people
    ],
)
async def test_michel_stays_quiet(enabled, message):
    ext = harness()
    await mention(ext, message)
    assert message.channel.sent == [] and message.reactions == [] and ext.calls == []


async def test_nothing_is_answered_where_mention_replies_are_off(enabled):
    enabled(mentionReplies=False)
    ext = harness()
    message = Message(f"<@{BOT_ID}> salut")
    await mention(ext, message)
    assert message.channel.sent == [] and ext.calls == []


async def test_the_cooldown_reacts_once_then_stays_quiet(enabled):
    ext = harness()
    await mention(ext, Message(f"<@{BOT_ID}> un"))
    second, third = Message(f"<@{BOT_ID}> deux"), Message(f"<@{BOT_ID}> trois")
    await mention(ext, second)
    await mention(ext, third)
    assert second.reactions == [COOLDOWN_EMOJI] and second.channel.sent == []
    assert third.reactions == [] and third.channel.sent == []


async def test_a_paused_ai_or_a_spent_quota_answers_with_a_reaction(enabled):
    enabled(mentionReplies=True, ai={"paused": True})
    paused = Message(f"<@{BOT_ID}> salut")
    await mention(harness(), paused)
    assert paused.reactions == [QUOTA_EMOJI] and paused.channel.sent == []

    enabled(mentionReplies=True)
    spent = Message(f"<@{BOT_ID}> salut")
    await mention(harness(over_quota=True), spent)
    assert spent.reactions == [QUOTA_EMOJI]


@pytest.mark.parametrize("ext", [harness(models=()), harness(answer=None)])
async def test_no_model_or_no_answer_reacts_with_a_warning(enabled, ext):
    message = Message(f"<@{BOT_ID}> salut")
    await mention(ext, message)
    assert message.reactions == [FAILURE_EMOJI] and message.channel.sent == []


async def test_an_unexpected_failure_is_a_warning_reaction(enabled):
    ext = harness()

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    ext._gather_history = broken
    message = Message(f"<@{BOT_ID}> salut")
    await mention(ext, message)
    assert message.reactions == [FAILURE_EMOJI]
