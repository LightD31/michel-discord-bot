"""Tests for the shared Minecraft "Stats" embed.

Two tasks write this embed: the hourly stats job that regenerates the table
image, and the 30 s status poll that rebuilds the whole message. They have to
render it byte for byte from the same instant, or every poll looks like a
change and the message is rewritten on a 30 s loop forever.
"""

import re

from interactions import Timestamp

from extensions.minecraft._common import build_stats_embed


def _instant() -> Timestamp:
    return Timestamp.fromtimestamp(1_700_000_000, tz=Timestamp.utcnow().tzinfo)


def test_both_writers_render_the_same_embed():
    instant = _instant()
    assert build_stats_embed(instant).to_dict() == build_stats_embed(instant).to_dict()


def test_label_is_a_fixed_epoch_not_a_recomputed_clock():
    """Discord renders `<t:…:R>` client-side, so "il y a 5 minutes" stays current
    without the bot rewriting the message to advance it."""
    rendered = build_stats_embed(_instant()).to_dict()["description"]

    match = re.search(r"<t:(\d+):R>", rendered)
    assert match, rendered
    assert int(match.group(1)) == 1_700_000_000


def test_a_different_instant_is_a_real_change():
    earlier = build_stats_embed(_instant()).to_dict()
    later = build_stats_embed(
        Timestamp.fromtimestamp(1_700_003_600, tz=_instant().tzinfo)
    ).to_dict()

    assert earlier != later


def test_image_points_at_the_uploaded_table():
    assert build_stats_embed(_instant()).to_dict()["image"]["url"] == "attachment://stats.png"
