"""« Quand ? » in the "⏰ Me le rappeler" form, read on the Paris clock (B1)."""

from datetime import UTC, datetime, timedelta

import pytest

from features.reminders.when import parse_when
from src.core import clock

# Wednesday 18 November 2026, 15:00 in Paris.
NOW = clock.at_local(datetime(2026, 11, 18).date(), 15, 0)


def local(text: str) -> str:
    return clock.to_local(parse_when(text, NOW)).strftime("%a %d/%m %H:%M")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("dans 2h", "Wed 18/11 17:00"),
        ("dans 2h30", "Wed 18/11 17:30"),
        ("Dans 45 min", "Wed 18/11 15:45"),
        ("30m", "Wed 18/11 15:30"),
        ("dans 3 jours", "Sat 21/11 15:00"),
        ("dans 1 semaine", "Wed 25/11 15:00"),
        ("18h30", "Wed 18/11 18:30"),
        ("à 20:15", "Wed 18/11 20:15"),
        ("8h", "Thu 19/11 08:00"),  # already past today: tomorrow
        ("2h", "Thu 19/11 02:00"),  # an hour of the day: a delay says "dans"
        ("demain", "Thu 19/11 09:00"),
        ("demain 18h30", "Thu 19/11 18:30"),
        ("demain à midi", "Thu 19/11 12:00"),
        ("après-demain 7h", "Fri 20/11 07:00"),
        ("ce soir", "Wed 18/11 20:00"),
        ("ce soir 22h", "Wed 18/11 22:00"),
        ("samedi 9h", "Sat 21/11 09:00"),
        ("mercredi 16h", "Wed 18/11 16:00"),  # today, still ahead
        ("mercredi 9h", "Wed 25/11 09:00"),  # today, gone: next week
        ("lundi prochain", "Mon 23/11 09:00"),
        ("24/12 20h", "Thu 24/12 20:00"),
        ("24 décembre à 18h", "Thu 24/12 18:00"),
        ("le 3/1/2027 10h", "Sun 03/01 10:00"),
    ],
)
def test_what_a_member_types(text, expected):
    assert local(text) == expected


def test_daylight_saving_keeps_the_wall_time():
    # 29 March 2027 is after the spring change: 9:00 is still 9:00 in Paris.
    assert clock.to_local(parse_when("29/03 9h", NOW)).hour == 9


@pytest.mark.parametrize("text", ["", "bientôt", "dans", "25h", "demain 18h75", "31/02"])
def test_what_it_does_not_understand_says_how(text):
    with pytest.raises(ValueError, match="Exemples : dans 2h"):
        parse_when(text, NOW)


def test_the_past_and_the_far_future_are_refused():
    with pytest.raises(ValueError, match="déjà passé"):
        parse_when("aujourd'hui 10h", NOW)
    with pytest.raises(ValueError, match="trop loin"):
        parse_when("dans 500 jours", NOW)


def test_the_result_is_a_utc_instant():
    when = parse_when("dans 2h", NOW)
    assert when.tzinfo is UTC or when.utcoffset() == timedelta(0)
