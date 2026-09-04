"""The remote-participants embed fills the message budget rather than a fixed cap.

It used to stop at 100 names regardless of how much room the message had. The
real constraint is Discord's 6000-character ceiling shared across every embed,
so the roster now takes whatever the other embeds leave.
"""

from extensions.zevent import embeds as module
from extensions.zevent._common import StreamerInfo
from extensions.zevent.embeds import EMBED_TOTAL_BUDGET, EmbedsMixin


class _Embeds(EmbedsMixin):
    """EmbedsMixin with the two phase predicates its builders consult."""

    _event_title = "ZEvent test"

    def _is_event_started(self) -> bool:
        return True

    def _is_main_event_started(self) -> bool:
        return True


def _roster(count: int, *, live: int = 0, prefix: str = "streamer") -> dict[str, StreamerInfo]:
    out: dict[str, StreamerInfo] = {}
    for i in range(count):
        name = f"{prefix}{i:03d}"
        out[name] = StreamerInfo(name, name.lower(), i < live, "Online", 0.0)
    return out


def _names_in(embed) -> list[str]:
    names: list[str] = []
    for field in embed.fields:
        names.extend(field.value.split(", "))
    return names


def test_far_more_than_a_hundred_names_are_shown_when_they_fit() -> None:
    builder = _Embeds()
    streams = _roster(250)
    embed = builder.create_location_embed(
        "participants à distance", streams, withlink=False, total_count=250, max_chars=5000
    )
    assert len(_names_in(embed)) == 250
    assert embed.title == "Les 250 participants à distance"


def test_a_tight_budget_truncates_and_the_title_says_so() -> None:
    builder = _Embeds()
    embed = builder.create_location_embed(
        "participants à distance", _roster(250), withlink=False, total_count=250, max_chars=300
    )
    shown = _names_in(embed)
    assert 0 < len(shown) < 250
    assert embed.title == f"Top {len(shown)}/250 participants à distance"


def test_the_rendered_embed_never_exceeds_the_budget_it_was_given() -> None:
    builder = _Embeds()
    for budget in (200, 500, 1500, 4000):
        embed = builder.create_location_embed(
            "participants à distance",
            _roster(250),
            withlink=False,
            total_count=250,
            max_chars=budget,
        )
        values = sum(len(f.value) for f in embed.fields)
        assert values <= budget, f"{values} > {budget}"


def test_live_streamers_survive_a_squeeze_before_offline_ones() -> None:
    """Budget is spent on the live group first — those are the watchable ones."""
    builder = _Embeds()
    live_count = 5
    streams = _roster(200, live=live_count, prefix="chan")
    embed = builder.create_location_embed(
        "participants à distance", streams, withlink=False, total_count=200, max_chars=200
    )

    by_group = {f.name.split(" ")[0]: f.value.split(", ") for f in embed.fields}
    shown = _names_in(embed)
    # Every live channel is present even though the roster is heavily cut...
    assert all(f"chan{i:03d}" in shown for i in range(live_count))
    # ...and the offline group is what absorbed the truncation.
    assert len(shown) < 200
    assert len(by_group.get("Streamers", [])) == live_count


def test_a_budget_that_only_covers_the_live_group_drops_offline_entirely() -> None:
    builder = _Embeds()
    streams = _roster(200, live=5, prefix="chan")
    embed = builder.create_location_embed(
        "participants à distance", streams, withlink=False, total_count=200, max_chars=45
    )
    assert [f.name for f in embed.fields] == ["Streamers en ligne"]
    assert len(_names_in(embed)) == 5


def test_no_budget_given_means_no_truncation() -> None:
    builder = _Embeds()
    embed = builder.create_location_embed(
        "participants à distance", _roster(250), withlink=False, total_count=250
    )
    assert len(_names_in(embed)) == 250


def test_remaining_budget_shrinks_as_embeds_are_added() -> None:
    builder = _Embeds()
    main = builder.create_main_embed("1 000 €", "42")
    assert builder.remaining_embed_budget([]) == EMBED_TOTAL_BUDGET
    with_main = builder.remaining_embed_budget([main])
    assert 0 < with_main < EMBED_TOTAL_BUDGET
    assert builder.remaining_embed_budget([main, main]) < with_main


def test_remaining_budget_never_goes_negative() -> None:
    builder = _Embeds()
    huge = builder.create_location_embed(
        "participants à distance", _roster(400), withlink=False, total_count=400
    )
    assert builder.remaining_embed_budget([huge] * 5) == 0


# ── the donation-goals count is a display limit, not a hard-coded 5 ──


def test_goals_count_is_clamped_and_survives_junk() -> None:
    """The parser guards the config value the dashboard writes."""
    from extensions.zevent._common import _parse_count

    assert _parse_count(8, 5, "n", 25) == 8
    assert _parse_count("8", 5, "n", 25) == 8  # the UI writes numbers as JSON
    assert _parse_count(8.0, 5, "n", 25) == 8
    # Out of range is clamped rather than inverted or unbounded.
    assert _parse_count(-3, 5, "n", 25) == 0
    assert _parse_count(999, 5, "n", 25) == 25
    # Unusable values fall back to the default rather than crashing startup.
    assert _parse_count(None, 5, "n", 25) == 5
    assert _parse_count("huit", 5, "n", 25) == 5
    # Zero is meaningful: it hides the embed.
    assert _parse_count(0, 5, "n", 25) == 0


# ─── Hiding offline streamers ─────────────────────────────────────────


def _headers(embed) -> list[str]:
    return [field.name for field in embed.fields]


def test_offline_streamers_are_listed_by_default(monkeypatch) -> None:
    monkeypatch.setattr(module, "SHOW_OFFLINE_STREAMERS", True)
    embed = _Embeds().create_location_embed(
        "streamers présents sur place", _roster(10, live=3), withlink=False, total_count=10
    )
    assert "Hors-ligne" in " ".join(_headers(embed))
    assert len(_names_in(embed)) == 10


def test_the_toggle_hides_them_on_site(monkeypatch) -> None:
    monkeypatch.setattr(module, "SHOW_OFFLINE_STREAMERS", False)
    embed = _Embeds().create_location_embed(
        "streamers présents sur place", _roster(10, live=3), withlink=False, total_count=10
    )
    assert "Hors-ligne" not in " ".join(_headers(embed))
    assert len(_names_in(embed)) == 3


def test_the_toggle_hides_them_remotely_too(monkeypatch) -> None:
    monkeypatch.setattr(module, "SHOW_OFFLINE_STREAMERS", False)
    embed = _Embeds().create_location_embed(
        "participants à distance", _roster(50, live=4), withlink=False, total_count=50
    )
    assert "Hors-ligne" not in " ".join(_headers(embed))
    assert len(_names_in(embed)) == 4


def test_hiding_offline_is_not_reported_as_a_truncation(monkeypatch) -> None:
    """ "Top x/y" means the message ran out of room, not that a filter applied."""
    monkeypatch.setattr(module, "SHOW_OFFLINE_STREAMERS", False)
    embed = _Embeds().create_location_embed(
        "participants à distance",
        _roster(50, live=4),
        withlink=False,
        total_count=50,
        max_chars=5000,
    )
    assert not embed.title.startswith("Top")


def test_a_budget_shortfall_is_still_reported_as_a_truncation(monkeypatch) -> None:
    monkeypatch.setattr(module, "SHOW_OFFLINE_STREAMERS", True)
    embed = _Embeds().create_location_embed(
        "participants à distance", _roster(300), withlink=False, total_count=300, max_chars=200
    )
    assert embed.title.startswith("Top")


def test_the_final_recap_still_lists_everyone(monkeypatch) -> None:
    """After the event nobody is live; the recap must not come out empty."""
    monkeypatch.setattr(module, "SHOW_OFFLINE_STREAMERS", False)
    embed = _Embeds().create_location_embed(
        "streamers présents sur place",
        _roster(10),
        withlink=False,
        finished=True,
        total_count=10,
    )
    assert len(_names_in(embed)) == 10
