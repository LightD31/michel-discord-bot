"""Instants are aware UTC datetimes; wall times go through src/core/clock.py (T07).

A naive ``datetime.now()`` is the host's wall clock: UTC in a bare container,
Paris time with the compose file's /etc/localtime. Mixed with the aware dates
MongoDB returns it raises ``TypeError``; stored, it is off by one or two hours.
"""

import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from src.core import clock
from src.discord_ext.embeds import format_discord_timestamp

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["extensions", "features", "src", "main.py"]


def naive_clock_reads(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            names += [alias.name for alias in node.names]
            if any(name.split(".")[0] == "pytz" for name in names):
                lines.append(node.lineno)
            continue
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id in ("datetime", "date")):
            continue
        method = node.func.attr
        no_zone = not node.keywords
        if (
            method in ("utcnow", "utcfromtimestamp", "today")
            or method == "now"
            and not node.args
            and no_zone
            or method == "fromtimestamp"
            and len(node.args) < 2
            and no_zone
        ):
            lines.append(node.lineno)
    return lines


def test_no_naive_clock_reads():
    offenders = []
    for name in SOURCES:
        path = ROOT / name
        for file in [path] if path.is_file() else sorted(path.rglob("*.py")):
            for line in naive_clock_reads(file.read_text(encoding="utf-8")):
                offenders.append(f"{file.relative_to(ROOT)}:{line}")
    assert offenders == [], (
        "use clock.now() (UTC) or clock.local_now() (wall clock) from src/core/clock.py, "
        "and zoneinfo rather than pytz"
    )


@pytest.mark.parametrize(
    "source",
    [
        "datetime.now()",
        "datetime.utcnow()",
        "date.today()",
        "datetime.fromtimestamp(0)",
        "import pytz",
    ],
)
def test_the_scan_catches(source):
    assert naive_clock_reads(source) == [1]


@pytest.mark.parametrize(
    "source", ["datetime.now(UTC)", "datetime.fromtimestamp(0, UTC)", "clock.now()"]
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
