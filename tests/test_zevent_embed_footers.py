"""Every embed footer credits exactly the sources that fed it.

Both optional data sources are configured per guild, so the credit has to
follow the configuration: naming a source the tracker never reached would be
a lie, and omitting one it leans on is what this file exists to prevent.
"""

import pytest

from extensions.zevent import embeds as module
from extensions.zevent._common import StreamerInfo
from extensions.zevent.embeds import (
    SOURCE_STATS,
    SOURCE_STREAMLABS,
    SOURCE_TWITCH,
    SOURCE_ZEVENT,
    EmbedsMixin,
    source_footer,
)
from features.zevent.models import DonationGoal, Participant


class _Embeds(EmbedsMixin):
    """EmbedsMixin with the two phase predicates its builders consult."""

    _event_title = "ZEvent test"

    def _is_event_started(self) -> bool:
        return True

    def _is_main_event_started(self) -> bool:
        return True

    def goal_etas(self) -> dict[str, float]:
        return {}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Both optional sources configured; individual tests switch them off."""
    monkeypatch.setattr(module, "STREAMLABS_API_URL", "https://streamlabscharity.test/team")
    monkeypatch.setattr(module, "STATS_API_URL", "https://stats.test")


def _roster() -> dict[str, StreamerInfo]:
    return {"Alice": StreamerInfo("Alice", "alice", True, "LAN", 0.0)}


def _location_embed(builder: _Embeds):
    return builder.create_location_embed(
        "streamers présents sur place", _roster(), withlink=False, total_count=1
    )


# ─── The helper ───────────────────────────────────────────────────────


def test_the_footer_lists_only_the_sources_given() -> None:
    assert source_footer("a", None, "b") == "Source: a / b ❤️"


def test_the_footer_is_empty_when_nothing_fed_the_embed() -> None:
    assert source_footer(None, None) == ""


# ─── Main embed ───────────────────────────────────────────────────────


def test_the_main_embed_credits_streamlabs_when_it_feeds_the_total() -> None:
    footer = _Embeds().create_main_embed("1 000 €").footer.text
    assert footer == f"Source: {SOURCE_ZEVENT} / {SOURCE_STREAMLABS} / {SOURCE_TWITCH} ❤️"


def test_the_main_embed_drops_streamlabs_when_no_team_is_configured(monkeypatch) -> None:
    """Without a team URL the total is purely zevent.fr."""
    monkeypatch.setattr(module, "STREAMLABS_API_URL", "")
    footer = _Embeds().create_main_embed("1 000 €").footer.text
    assert SOURCE_STREAMLABS not in footer
    assert footer == f"Source: {SOURCE_ZEVENT} / {SOURCE_TWITCH} ❤️"


# ─── Location embeds ──────────────────────────────────────────────────


def test_the_location_embed_credits_the_stats_site_for_the_split() -> None:
    """The LAN/remote split is the stats API's contribution, not zevent.fr's."""
    footer = _location_embed(_Embeds()).footer.text
    assert footer == f"Source: {SOURCE_ZEVENT} / {SOURCE_STATS} / {SOURCE_TWITCH} ❤️"


def test_the_location_embed_drops_the_stats_site_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(module, "STATS_API_URL", "")
    footer = _location_embed(_Embeds()).footer.text
    assert SOURCE_STATS not in footer


# ─── Goals embed ──────────────────────────────────────────────────────


def test_the_goals_embed_credits_both_the_goals_and_the_pace(monkeypatch) -> None:
    """Goals come from the stats API; the pace ranking them is zevent.fr's."""
    monkeypatch.setattr(module, "GOALS_COUNT", 5)
    participants = [
        Participant(
            display_name="Alice",
            twitch_login="alice",
            twitch_id="1",
            location="LAN",
            raw_location="lan",
            live=True,
            amount_raised=500.0,
            next_goal=DonationGoal(name="Barbe", amount=1000.0),
        )
    ]
    embed = _Embeds().create_donation_goals_embed(participants)
    assert embed is not None
    assert embed.footer.text == f"Source: {SOURCE_STATS} / {SOURCE_ZEVENT} ❤️"
