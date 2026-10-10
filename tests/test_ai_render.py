"""Rendering of AI answers into Discord-sized messages (``features.ai.render``)."""

from features.ai import (
    attach_footer,
    render_comparison_body,
    render_comparison_footer,
    render_single,
    split_message,
    tally,
    winners,
)
from features.ai.render import DISCORD_MESSAGE_LIMIT, FOOTER_RESERVE

DISCORD_HARD_LIMIT = 2000


def test_split_keeps_short_messages_whole():
    assert split_message("court") == ["court"]


def test_split_respects_limit_and_loses_nothing():
    text = "\n\n".join(f"paragraphe {i} " + "mot " * 120 for i in range(10))
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= DISCORD_MESSAGE_LIMIT for c in chunks)
    assert "".join(chunks).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace(
        "\n", ""
    )


def test_tally_and_winners():
    counts = tally(["a", "b", "c"], {"1": "a", "2": "b", "3": "a", "4": "gone"})
    assert counts == [2, 1, 0]
    assert winners(counts) == {0}
    assert winners([1, 1, 0]) == {0, 1}
    assert winners([0, 0]) == set()


def test_comparison_fits_with_the_largest_footer():
    answers = ["réponse " * 300, "autre " * 400, "courte"]
    body = render_comparison_body("<@1>", "Une question ?", answers)
    footer = render_comparison_footer(
        ["Un nom de modèle vraiment très long " * 3] * 5, [99] * 5, closed=True
    )
    assert len(footer) + 2 <= FOOTER_RESERVE
    messages = attach_footer(body, footer)
    assert all(len(m) <= DISCORD_HARD_LIMIT for m in messages)
    assert messages[-1].endswith(footer)
    assert messages[0].startswith("**<@1> : Une question ?**")
    assert "**Réponse 3**\n> courte" in "\n".join(messages)


def test_open_footer_shows_deadline_and_count_only():
    footer = render_comparison_footer(["A", "B"], [2, 1], closed=False, closes_at_ts=1700000000)
    assert "<t:1700000000:R>" in footer
    assert "3 votes" in footer
    assert "A" not in footer  # models stay anonymous until the vote closes


def test_closed_footer_reveals_models_and_marks_ties():
    footer = render_comparison_footer(["GPT", "Claude", "Grok"], [2, 2, 0], closed=True)
    assert "Réponse 1 : **GPT** — 2 votes 🏆" in footer
    assert "Réponse 2 : **Claude** — 2 votes 🏆" in footer
    assert "Réponse 3 : **Grok** — 0 vote" in footer and "Grok** — 0 vote 🏆" not in footer


def test_closed_footer_without_votes_has_no_winner():
    assert "🏆" not in render_comparison_footer(["A", "B"], [0, 0], closed=True)


def test_long_question_is_truncated_in_header():
    body = render_comparison_body("<@1>", "q" * 1000, ["a", "b"])
    assert len(body[0].split("\n")[0]) < 400


def test_render_single_with_header_and_footer():
    chunks = render_single("Réponse", header="**Q**", footer="modèle : X")
    assert chunks == ["**Q**\n\nRéponse\n\n-# modèle : X"]
    assert render_single("Juste ça") == ["Juste ça"]
