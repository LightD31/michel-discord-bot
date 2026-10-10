"""When Michel answers a mention (``features.ai.mentions``)."""

from features.ai import Cooldown, MentionFacts, RecentAnswers, mention_question

BOT = "42"


def facts(content, **kwargs):
    return MentionFacts(content=content, bot_id=BOT, **kwargs)


def test_explicit_mention_is_answered_without_the_mention():
    assert mention_question(facts("<@42> quelle heure ?")) == "quelle heure ?"
    assert mention_question(facts("dis <@!42>, ça va ?")) == "dis , ça va ?"


def test_no_mention_no_answer():
    assert mention_question(facts("salut tout le monde")) is None
    assert mention_question(facts("<@43> toi pas moi")) is None


def test_reply_to_an_answer_continues_without_mention():
    assert mention_question(facts("et pourquoi ?", replies_to_answer=True)) == "et pourquoi ?"


def test_bots_webhooks_and_mass_pings_are_ignored():
    assert mention_question(facts("<@42> hey", author_is_bot=True)) is None
    assert mention_question(facts("<@42> hey", is_webhook=True)) is None
    assert mention_question(facts("<@42> @everyone hey", mentions_everyone=True)) is None
    assert mention_question(facts("<@42> <@&7> hey")) is None


def test_bot_managed_role_counts_as_a_mention():
    assert mention_question(facts("<@&9> salut", bot_role_ids=frozenset({"9"}))) == "salut"
    assert mention_question(facts("<@&9> <@&7> salut", bot_role_ids=frozenset({"9"}))) is None


def test_mention_alone_is_not_a_question():
    assert mention_question(facts("<@42>")) is None


def test_cooldown_warns_once_per_window():
    cooldown = Cooldown()
    assert cooldown.check("u", 20, now=0) == (True, False)
    assert cooldown.check("u", 20, now=5) == (False, True)
    assert cooldown.check("u", 20, now=6) == (False, False)
    assert cooldown.check("v", 20, now=6) == (True, False)
    assert cooldown.check("u", 20, now=21) == (True, False)
    assert cooldown.check("u", 0, now=21) == (True, False)


def test_recent_answers_is_bounded():
    recent = RecentAnswers(cap=2)
    recent.add("1", "2")
    recent.add("3")
    assert "1" not in recent and "2" in recent and 3 in recent
