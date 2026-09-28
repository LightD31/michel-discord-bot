"""Défi-forum listener: which messages become drafts."""

import pytest
from confrerie_fixtures import OEUVRES_SCHEMA
from test_confrerie_review import FakeRepo

from extensions.confrerie import forums
from extensions.confrerie.forums import ForumsMixin

FORUM_ID = 500
THREAD_ID = 600


class Author:
    def __init__(self, user_id: int, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot
        self.display_name = f"pseudo{user_id}"


class Thread:
    id = THREAD_ID
    parent_id = FORUM_ID
    name = "Défi 12 - Questionnaire personnage"


class Message:
    def __init__(self, msg_id, author, content="", attachments=()):
        self.id = msg_id
        self.author = author
        self.content = content
        self.attachments = list(attachments)
        self.channel = Thread()
        self.jump_url = f"https://discord.com/channels/9/{THREAD_ID}/{msg_id}"


class Event:
    def __init__(self, message):
        self.message = message


class Harness(ForumsMixin):
    def __init__(self) -> None:
        self.repo = FakeRepo()
        self.submitted = []

    def _drafts(self):
        return self.repo

    async def _oeuvres_schema(self):
        return OEUVRES_SCHEMA

    async def submit_draft(self, draft):
        self.submitted.append(draft)
        return await self.repo.insert_if_new(draft)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(
        forums,
        "module_config",
        {"confrerieDefisForumId": str(FORUM_ID), "confrerieAuthorMap": {"42": "Mahé"}},
    )
    monkeypatch.setattr(forums, "guild_id", "9")


async def _post(harness, message):
    await ForumsMixin.on_defi_answer.callback(harness, Event(message))


async def test_first_answer_becomes_a_draft():
    harness = Harness()
    await _post(harness, Message(700, Author(42), "# Grief\n\n" + "texte " * 60))
    [draft] = harness.submitted
    assert draft.id == f"defi-{THREAD_ID}-42"
    assert draft.defi == "Défi 12 : Questionnaire Personnage"
    assert draft.authors == ["Mahé"] and not draft.unmapped_author
    assert draft.title == "Grief"
    assert draft.types == ["Défi"]
    assert draft.message_id == 700


async def test_prompt_comments_bots_and_repeat_answers_are_skipped():
    harness = Harness()
    long_text = "texte " * 60
    await _post(harness, Message(THREAD_ID, Author(1), long_text))  # the défi prompt itself
    await _post(harness, Message(701, Author(43), "Bravo !"))  # a comment
    await _post(harness, Message(702, Author(44, bot=True), long_text))  # a bot
    assert harness.submitted == []

    await _post(harness, Message(703, Author(43), "", attachments=["file.pdf"]))
    await _post(harness, Message(704, Author(43), long_text))  # same member, same défi
    assert [d.id for d in harness.submitted] == [f"defi-{THREAD_ID}-43"]
    assert harness.submitted[0].unmapped_author
    assert harness.submitted[0].authors == ["pseudo43"]


async def test_other_channels_are_ignored():
    harness = Harness()
    message = Message(705, Author(42), "texte " * 60)
    message.channel.parent_id = 999
    await _post(harness, message)
    assert harness.submitted == []
