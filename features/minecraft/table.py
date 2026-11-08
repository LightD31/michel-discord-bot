"""The player stats table shown under a guild's Minecraft status, as text.

Built from the rows :func:`features.minecraft.stats_sftp.get_player_stats_optimized`
returns (one dict per player); the extension renders the text to an image.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import prettytable

COLUMNS = [
    "Joueur",
    "Niveau",
    "Morts",
    "Morts/h",
    "Marche (km)",
    "Temps de jeu",
    "Blocs minés",
    "Mobs tués",
    "Animaux reproduits",
]


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_large_number(value: Any) -> str:
    """``123456`` → ``"123.5k"``, ``2500000`` → ``"2.5M"``; small numbers stay whole."""
    num = _number(value)
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}k"
    return str(int(num))


def format_playtime(seconds: Any) -> str:
    """Seconds as ``h:mm:ss``, hours counted past a day (``"26:03:04"``)."""
    total = round(_number(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def stats_rows(
    players: Iterable[Mapping[str, Any]], *, max_players: int, name_length: int
) -> list[list[str]]:
    """The table's rows: most played first, *max_players* at most, every cell a string."""
    ranked = sorted(players, key=lambda p: _number(p.get("Temps de jeu")), reverse=True)
    return [
        [
            str(p.get("Joueur") or "?")[:name_length],
            str(p.get("Niveau", "")),
            str(int(_number(p.get("Morts")))),
            f"{_number(p.get('Morts/h')):.1f}",
            f"{_number(p.get('Marche (km)')):.1f}",
            format_playtime(p.get("Temps de jeu")),
            format_large_number(p.get("Blocs minés")),
            format_large_number(p.get("Mobs tués")),
            format_large_number(p.get("Animaux reproduits")),
        ]
        for p in ranked[:max_players]
    ]


def build_stats_table(
    players: Iterable[Mapping[str, Any]], *, max_players: int = 15, name_length: int = 14
) -> prettytable.PrettyTable | None:
    """The boxed table of the top players, ``None`` when there is no player."""
    rows = stats_rows(players, max_players=max_players, name_length=name_length)
    if not rows:
        return None
    table = prettytable.PrettyTable(COLUMNS)
    table.add_rows(rows)
    table.align = "r"
    table.align["Joueur"] = "l"
    table.set_style(prettytable.TableStyle.SINGLE_BORDER)
    table.padding_width = 1
    table.title = f"Stats Joueurs (Top {max_players})"
    table.hrules = prettytable.HRuleStyle.ALL
    return table


__all__ = ["COLUMNS", "build_stats_table", "format_large_number", "format_playtime", "stats_rows"]
