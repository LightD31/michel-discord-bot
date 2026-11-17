"""Instants are aware UTC datetimes; wall times go through src/core/clock.py (T07).

A naive ``datetime.now()`` is the host's wall clock: UTC in a bare container,
Paris time with the compose file's /etc/localtime. Mixed with the aware dates
MongoDB returns it raises ``TypeError``; stored, it is off by one or two hours.
"""

import ast
from datetime import UTC, date, datetime, timedelta

import pytest
from source_scan import BOT_SOURCES, Imports, is_none, keyword, python_files, rel

from src.core import clock
from src.core.text import format_discord_timestamp

# Read without a zone, each gives the host's wall clock (or a naive UTC one).
ALWAYS_NAIVE = {
    "datetime.datetime.utcnow",
    "datetime.datetime.utcfromtimestamp",
    "datetime.datetime.today",
    "datetime.date.today",
    "datetime.date.fromtimestamp",
}
# Naive unless a zone is passed: (positional index of the zone, keyword name).
ZONE_ARGUMENT = {
    "datetime.datetime.now": (0, "tz"),
    "datetime.datetime.fromtimestamp": (1, "tz"),
}


def _has_zone(call: ast.Call, index: int, name: str) -> bool:
    passed = keyword(call, name)
    if passed is not None:
        return not is_none(passed.value)
    return len(call.args) > index and not is_none(call.args[index])


def naive_clock_reads(source: str) -> list[int]:
    tree = ast.parse(source)
    imports = Imports(tree)
    lines = [line for line, module in imports.modules if module.split(".")[0] == "pytz"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = imports.resolve(node.func)
        if (
            name in ALWAYS_NAIVE
            or name in ZONE_ARGUMENT
            and not _has_zone(node, *ZONE_ARGUMENT[name])
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_no_naive_clock_reads():
    offenders = [
        f"{rel(file)}:{line}"
        for file in python_files(*BOT_SOURCES)
        for line in naive_clock_reads(file.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "use clock.now() (UTC) or clock.local_now() (wall clock) from src/core/clock.py, "
        "and zoneinfo rather than pytz"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\ndatetime.now()",
        "from datetime import datetime\ndatetime.utcnow()",
        "from datetime import date\ndate.today()",
        "from datetime import datetime\ndatetime.fromtimestamp(0)",
        "x = 1\nimport pytz",
        # Spelled some other way (T02).
        "import datetime\ndatetime.datetime.now()",
        "import datetime as dt\ndt.datetime.now()",
        "from datetime import datetime as dt\ndt.now()",
        "from datetime import datetime\ndatetime.now(tz=None)",
        "from datetime import datetime\ndatetime.now(None)",
        "from datetime import datetime\ndatetime.fromtimestamp(0, tz=None)",
        "from datetime import datetime\ndatetime.today()",
        "x = 1\nfrom pytz import timezone",
    ],
)
def test_the_scan_catches(source):
    assert naive_clock_reads(source) == [2]


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import UTC, datetime\ndatetime.now(UTC)",
        "from datetime import UTC, datetime\ndatetime.now(tz=UTC)",
        "import datetime as dt\ndt.datetime.fromtimestamp(0, dt.UTC)",
        "clock.now()",
        # Not the standard library's datetime.
        "from interactions import Timestamp\nTimestamp.now()",
    ],
)
def test_the_scan_lets_aware_reads_through(source):
    assert naive_clock_reads(source) == []


def test_now_is_utc_and_local_now_is_paris():
    assert clock.now().utcoffset() == timedelta(0)
    assert clock.local_now().tzinfo is clock.LOCAL_TZ


def test_at_local_reads_a_wall_time_in_paris():
    assert clock.at_local(date(2026, 1, 15), 20, 0) == datetime(2026, 1, 15, 19, 0, tzinfo=UTC)
    assert clock.at_local(date(2026, 7, 15), 20, 0) == datetime(2026, 7, 15, 18, 0, tzinfo=UTC)


def test_a_wall_time_the_spring_change_skips_lands_an_hour_later():
    # 29 March 2026, 02:00 → 03:00 in Paris: 02:30 does not exist.
    moment = clock.at_local(date(2026, 3, 29), 2, 30)
    assert clock.to_local(moment).hour == 3
    assert moment == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_a_wall_time_the_autumn_change_repeats_is_its_first_pass():
    moment = clock.at_local(date(2026, 10, 25), 2, 30)
    assert moment == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_a_local_step_keeps_the_wall_time():
    evening = clock.at_local(date(2026, 3, 28), 20, 0)
    next_day = clock.local_step(evening, timedelta(days=1))
    assert clock.to_local(next_day).hour == 20
    assert next_day - evening == timedelta(hours=23)


def test_a_naive_date_is_shown_as_a_paris_wall_time():
    # Midnight in Paris on 1 January 2026 is 23:00 UTC the day before.
    expected = int(datetime(2025, 12, 31, 23, 0, tzinfo=UTC).timestamp())
    assert format_discord_timestamp(datetime(2026, 1, 1), "D") == f"<t:{expected}:D>"


def test_a_monthly_tricount_expense_keeps_its_wall_time_across_the_change():
    from extensions.tricount.recurring import _next_occurrence

    march = clock.at_local(date(2026, 3, 15), 20, 0)  # winter time
    april = _next_occurrence(march, "monthly")  # summer time
    assert clock.to_local(april) == datetime(2026, 4, 15, 20, 0, tzinfo=clock.LOCAL_TZ)
    assert april.utcoffset() == timedelta(0)
