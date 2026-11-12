"""Autocomplete choices: matched on the label or the value, within Discord's limits."""

from src.discord_ext.autocomplete import CHOICE_NAME_LIMIT, MAX_CHOICES, matching_choices


def test_typed_text_matches_the_label_or_the_value():
    items = [("Un jeu · fin le 12/11", "500"), ("Une carte cadeau", "501")]
    assert [c["value"] for c in matching_choices(items, "JEU")] == ["500"]
    assert [c["value"] for c in matching_choices(items, " 501 ")] == ["501"]
    assert len(matching_choices(items, "")) == 2


def test_choices_stay_within_discords_limits():
    items = [("x" * 300, str(i)) for i in range(40)]
    choices = matching_choices(items, "")
    assert len(choices) == MAX_CHOICES
    assert all(len(c["name"]) == CHOICE_NAME_LIMIT for c in choices)
    assert choices[0]["name"].endswith("…")
