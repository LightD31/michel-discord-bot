"""The Minecraft player stats table (no pandas) and the hourly job that posts it."""

from types import SimpleNamespace

import pytest

import extensions.minecraft.stats as stats_mod
from extensions.minecraft import Minecraft
from features.minecraft.table import (
    COLUMNS,
    build_stats_table,
    format_large_number,
    format_playtime,
    stats_rows,
)

STEVE = {
    "Joueur": "Steve_the_long_name_player",
    "Niveau": "30",
    "Morts": 12,
    "Morts/h": 0.456,
    "Marche (km)": 12.345,
    "Temps de jeu": 93784.4,
    "Blocs minés": 123456,
    "Mobs tués": 980,
    "Animaux reproduits": 2_500_000,
}
ALEX = {**STEVE, "Joueur": "Alex", "Niveau": "5", "Morts": 0, "Temps de jeu": 3600.0}


def test_large_numbers_are_shortened():
    assert [format_large_number(n) for n in (980, 1500, 123456, 2_500_000)] == [
        "980",
        "1.5k",
        "123.5k",
        "2.5M",
    ]
    assert format_large_number(None) == "0"


def test_playtime_counts_hours_past_a_day():
    assert format_playtime(93784.4) == "26:03:04"
    assert format_playtime(59) == "0:00:59"


def test_rows_are_ranked_by_playtime_and_capped():
    rows = stats_rows([ALEX, STEVE], max_players=1, name_length=14)

    assert rows == [
        ["Steve_the_long", "30", "12", "0.5", "12.3", "26:03:04", "123.5k", "980", "2.5M"]
    ]


def test_missing_values_render_as_zero():
    rows = stats_rows([{"Joueur": "Bob"}], max_players=15, name_length=14)
    assert rows == [["Bob", "", "0", "0.0", "0.0", "0:00:00", "0", "0", "0"]]


def test_the_table_has_a_title_and_every_column():
    text = build_stats_table([STEVE, ALEX], max_players=15).get_string()

    assert "Stats Joueurs (Top 15)" in text
    assert all(column in text for column in COLUMNS)
    assert text.index("Steve_the_long") < text.index("Alex")


def test_no_player_means_no_table():
    assert build_stats_table([]) is None


# ── The hourly job ───────────────────────────────────────────────────────


class Message:
    def __init__(self) -> None:
        self.embeds: list = []


@pytest.fixture
def job(monkeypatch):
    edits: list[dict] = []
    renders: list[str] = []

    async def fake_edit(message, **kwargs):
        edits.append(kwargs)

    def fake_render(text):
        renders.append(text)
        return None, b"png"

    monkeypatch.setattr(stats_mod, "edit_message_if_changed", fake_edit)
    monkeypatch.setattr(stats_mod, "create_dynamic_image", fake_render)
    monkeypatch.setattr(stats_mod, "File", lambda data, name: (name, data))

    message = Message()
    players: list[dict] = [STEVE]

    async def messages_get(guild_id, config):
        return message

    async def get_player_stats(settings):
        return list(players)

    ext = object.__new__(Minecraft)
    ext.status_messages = SimpleNamespace(get=messages_get)
    ext._last_stats_tables = {}
    ext._get_player_stats = get_player_stats
    return SimpleNamespace(ext=ext, edits=edits, renders=renders, players=players)


async def test_a_new_table_is_rendered_once_and_attached(job):
    await job.ext._update_stats("1", {}, None)

    assert len(job.renders) == 1 and "Steve_the_long" in job.renders[0]
    assert job.edits[0]["file"] == ("stats.png", b"png")


async def test_an_unchanged_table_is_not_rendered_again(job):
    await job.ext._update_stats("1", {}, None)
    await job.ext._update_stats("1", {}, None)

    assert len(job.renders) == 1
    assert "file" not in job.edits[1]


async def test_no_player_edits_the_embeds_without_an_image(job):
    job.players.clear()

    await job.ext._update_stats("1", {}, None)

    assert job.renders == [] and "file" not in job.edits[0]
