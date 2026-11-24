"""The Gazette's AI intro (features/gazette/intro.py): opt-in twice, one call, never in the way."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from features.ai import ModelAnswer
from features.gazette import Edition
from features.gazette import intro as intro_mod
from features.gazette.intro import intro_messages, write_intro
from src.core import clock
from src.core.errors import DatabaseError

NOW = clock.now()
MODELS = [{"api": "openrouter", "model_id": "v/a", "key": "a", "display_name": "A"}]


def edition():
    return Edition("Les Copains", NOW - timedelta(days=7), NOW, top_members=[("Camille", 9)])


class Client:
    def __init__(self, content="Bonjour @everyone,\n une   belle semaine !", error=None):
        self.content, self.error, self.calls = content, error, []

    async def complete(self, model, messages, max_tokens):
        self.calls.append(messages)
        return ModelAnswer(model=model, content=self.content, error=self.error)


@pytest.fixture
def setup(store_config, monkeypatch):
    def configure(gazette=True, ai=True, paused=False):
        store_config(
            {
                "config": {"AI": {"models": MODELS, "paused": paused}},
                "servers": {
                    "1": {
                        "moduleGazette": {"enabled": True, "gazetteAiIntro": gazette},
                        "moduleIA": {"enabled": ai},
                    }
                },
            }
        )

    configure()
    monkeypatch.setattr(intro_mod.llm, "configured_apis", lambda: ["openrouter"])
    reserved = []

    async def reserve(key, calls, limits, now):
        reserved.append((key, calls))
        return None

    return SimpleNamespace(configure=configure, reserve=reserve, reserved=reserved)


async def test_the_intro_is_one_call_cleaned_of_pings(setup):
    client = Client()
    text = await write_intro("1", edition(), client=client, reserve=setup.reserve)
    assert text == "Bonjour everyone, une belle semaine !"
    assert setup.reserved == [("gazette:1", 1)]
    (messages,) = client.calls
    assert "Camille" in messages[1]["content"] and "Les Copains" in messages[0]["content"]


@pytest.mark.parametrize("change", [{"gazette": False}, {"ai": False}, {"paused": True}])
async def test_no_intro_unless_both_switches_are_on(setup, change):
    setup.configure(**change)
    client = Client()
    assert await write_intro("1", edition(), client=client, reserve=setup.reserve) == ""
    assert client.calls == [] and setup.reserved == []


async def test_a_spent_or_unreadable_quota_means_no_intro(setup):
    client = Client()

    async def spent(*_):
        return "global"

    async def down(*_):
        raise DatabaseError("down")

    for reserve in (spent, down):
        assert await write_intro("1", edition(), client=client, reserve=reserve) == ""
    assert client.calls == []


async def test_a_failed_answer_means_no_intro(setup):
    client = Client(content=None, error="HTTP 500")
    assert await write_intro("1", edition(), client=client, reserve=setup.reserve) == ""


def test_the_prompt_says_not_to_invent():
    system, user = intro_messages(edition(), "Tu es Michel.")
    assert system["content"].startswith("Tu es Michel.")
    assert "n'invente aucun fait" in system["content"]
    assert user["content"].startswith("📰 La Gazette de Les Copains")
