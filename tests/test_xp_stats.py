"""Unit tests for the pure XP dashboard-stats shaping (no DB / Discord)."""

from datetime import UTC, date, datetime

from features.xp import calculate_level
from features.xp.stats import (
    build_heatmap,
    fill_daily_series,
    level_distribution,
    stats_window,
    summarize,
    top_members,
)


def test_stats_window_covers_today_and_starts_at_local_midnight():
    # 23:30 UTC on 2026-07-14 is already the 15th in Paris (UTC+2 in summer).
    now = datetime(2026, 7, 14, 23, 30, tzinfo=UTC)
    first_day, last_day, since = stats_window(7, "Europe/Paris", now)
    assert last_day == date(2026, 7, 15)
    assert first_day == date(2026, 7, 9)
    assert since == datetime(2026, 7, 8, 22, 0, tzinfo=UTC)


def test_stats_window_winter_offset():
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    first_day, last_day, since = stats_window(1, "Europe/Paris", now)
    assert first_day == last_day == date(2026, 1, 10)
    assert since == datetime(2026, 1, 9, 23, 0, tzinfo=UTC)


def test_fill_daily_series_zero_fills_and_splits_sources():
    activity = [
        {"day": "2026-09-02", "source": "message", "xp": 40, "events": 2},
        {"day": "2026-09-02", "source": "voice", "xp": 15, "events": 2},
        {"day": "2026-09-03", "source": "voice", "xp": 7, "events": 1},
        {"day": "2026-08-31", "source": "message", "xp": 99, "events": 9},  # outside window
    ]
    active = [{"day": "2026-09-02", "users": 3}, {"day": "2026-09-03", "users": 1}]

    series = fill_daily_series(activity, active, date(2026, 9, 1), date(2026, 9, 3))

    assert [d["date"] for d in series] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert series[0] == {
        "date": "2026-09-01",
        "message_xp": 0,
        "voice_xp": 0,
        "messages": 0,
        "voice_ticks": 0,
        "active_users": 0,
    }
    assert series[1]["message_xp"] == 40
    assert series[1]["messages"] == 2
    assert series[1]["voice_xp"] == 15
    assert series[1]["voice_ticks"] == 2
    assert series[1]["active_users"] == 3
    assert series[2]["voice_xp"] == 7
    assert series[2]["message_xp"] == 0


def test_fill_daily_series_counts_unknown_source_as_message():
    activity = [{"day": "2026-09-01", "xp": 20, "events": 1}]
    series = fill_daily_series(activity, [], date(2026, 9, 1), date(2026, 9, 1))
    assert series[0]["message_xp"] == 20
    assert series[0]["messages"] == 1


def test_build_heatmap_is_monday_first():
    rows = [
        {"dow": 2, "hour": 9, "xp": 10},  # Monday
        {"dow": 1, "hour": 23, "xp": 5},  # Sunday
        {"dow": 7, "hour": 0, "xp": 3},  # Saturday
        {"dow": 2, "hour": 9, "xp": 1},  # merged into the Monday bucket
        {"dow": 9, "hour": 3, "xp": 100},  # out of range, ignored
    ]
    grid = build_heatmap(rows)
    assert len(grid) == 7
    assert all(len(row) == 24 for row in grid)
    assert grid[0][9] == 11
    assert grid[6][23] == 5
    assert grid[5][0] == 3
    assert sum(map(sum, grid)) == 19


def test_level_distribution_uses_computed_level():
    docs = [
        {"_id": "a", "xp": 0, "lvl": 0},
        {"_id": "b", "xp": 50, "lvl": 0},
        # 100 XP clears level 0; the stored lvl lags behind.
        {"_id": "c", "xp": 100, "lvl": 0},
        # 100 + 155 + 220 = 475 XP clears levels 0-2 → level 3.
        {"_id": "d", "xp": 475, "lvl": 1},
    ]
    assert calculate_level(475)[0] == 3
    assert level_distribution(docs) == [
        {"level": 0, "users": 2},
        {"level": 1, "users": 1},
        {"level": 2, "users": 0},
        {"level": 3, "users": 1},
    ]


def test_level_distribution_empty():
    assert level_distribution([]) == []


def test_top_members_resolves_names_and_progress():
    docs = [
        {"_id": "1", "xp": 150, "msg": 12},
        {"_id": "2", "xp": 30, "msg": 3},
        {"_id": "3", "xp": 10, "msg": 1},
    ]
    names = {
        "1": {"username": "alice", "display_name": "Alice"},
        "2": {"username": "bob", "display_name": ""},
    }
    top = top_members(docs, names, limit=2)
    assert len(top) == 2
    assert top[0] == {
        "rank": 1,
        "user_id": "1",
        "display_name": "Alice",
        "username": "alice",
        "xp": 150,
        "messages": 12,
        "level": 1,
        "xp_in_level": 50,
        "xp_to_next": 155,
    }
    # Missing display name falls back to the username, then to the id.
    assert top[1]["display_name"] == "bob"
    assert top_members(docs, {}, limit=3)[2]["display_name"] == "3"


def test_summarize():
    docs = [{"_id": "1", "xp": 150, "msg": 12}, {"_id": "2", "xp": 30}]
    assert summarize(docs) == {"members": 2, "total_xp": 180, "total_messages": 12}
    assert summarize([]) == {"members": 0, "total_xp": 0, "total_messages": 0}
