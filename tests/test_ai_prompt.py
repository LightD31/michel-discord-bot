"""Prompt building for the AI answers (``features.ai.prompt``) — pure, no Discord."""

from datetime import date

import pytest

from features.ai import (
    DEFAULT_PERSONA,
    HistoryMessage,
    build_history,
    build_system_prompt,
    extract_response,
    participants_from,
    question_turn,
    replace_user_mentions,
    strip_user_mention,
)
from features.ai.prompt import french_date


def test_history_skips_empty_and_marks_attachments():
    turns = build_history(
        [
            HistoryMessage("Alice", "salut"),
            HistoryMessage("Bob", "   "),
            HistoryMessage("Carol", "", has_attachments=True),
        ]
    )
    assert turns == [
        {"role": "user", "content": "Alice : salut"},
        {"role": "user", "content": "Carol : [pièce jointe]"},
    ]


def test_history_drops_comparisons_and_keeps_own_answers():
    turns = build_history(
        [
            HistoryMessage("Michel", "Réponse 1 … Réponse 2 …", is_self=True, is_comparison=True),
            HistoryMessage("Michel", "Bien sûr.\n-# modèle : GPT", is_self=True),
            HistoryMessage("Michel", "-# rien que du petit texte", is_self=True),
        ]
    )
    assert turns == [{"role": "assistant", "content": "Bien sûr."}]


def test_history_truncates_and_notes_replies():
    turns = build_history([HistoryMessage("Alice", "x" * 50, reply_to="Bob")], max_chars=10)
    assert turns == [{"role": "user", "content": "Alice : (en réponse à Bob) xxxxxxxxx…"}]


def test_participants_are_distinct_humans_asker_first():
    history = [
        HistoryMessage("Bob", "a"),
        HistoryMessage("Michel", "b", is_self=True),
        HistoryMessage("Alice", "c"),
        HistoryMessage("Bob", "d"),
    ]
    assert participants_from(history, "Alice") == ["Alice", "Bob"]


def test_system_prompt_injects_persona_date_and_rules():
    prompt = build_system_prompt(
        "Tu es un pirate.",
        server="Srv",
        channel="général",
        author="Alice",
        participants=["Alice", "Bob"],
        today=date(2026, 10, 10),
    )
    assert "Tu es un pirate." in prompt
    assert DEFAULT_PERSONA not in prompt
    assert "samedi 10 octobre 2026" in prompt
    assert "- Alice ← " in prompt and "- Bob\n" in prompt
    assert "<response>" in prompt  # the code-owned format rule survives any persona


def test_blank_persona_falls_back_to_default():
    prompt = build_system_prompt(
        "  ", server="S", channel="c", author="A", participants=["A"], today=date(2026, 1, 1)
    )
    assert DEFAULT_PERSONA in prompt


def test_french_date():
    assert french_date(date(2026, 8, 3)) == "lundi 3 août 2026"


def test_mentions_are_replaced_and_stripped():
    assert replace_user_mentions("yo <@1> et <@!2> et <@3>", {"1": "Al", "2": "Bo"}) == (
        "yo @Al et @Bo et <@3>"
    )
    assert strip_user_mention("<@99>  dis   bonjour", "99") == "dis bonjour"
    assert strip_user_mention("hey <@!99>", "99") == "hey"


def test_question_turn():
    assert question_turn("Alice", "  ça va ? ") == {"role": "user", "content": "Alice : ça va ?"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<response>Salut</response>", "Salut"),
        ('<Response lang="fr">\n Salut \n</RESPONSE>', "Salut"),
        ("&lt;response&gt;Échappé&lt;/response&gt;", "Échappé"),
        ("<response>Coupé par la limite de tok", "Coupé par la limite de tok"),
        ("<think>je réfléchis</think><response>Réponse</response>", "Réponse"),
        ("<think>je réfléchis</think>Pas de balises", "Pas de balises"),
        ("  Pas de balises  ", "Pas de balises"),
        ("", ""),
        (None, ""),
    ],
)
def test_extract_response(raw, expected):
    assert extract_response(raw) == expected
