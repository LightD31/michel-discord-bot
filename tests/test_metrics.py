"""Unit tests for the Prometheus sample builders and snapshot collector (no DB / Discord)."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, generate_latest

from features.metrics import (
    SNAPSHOT_METRICS,
    MetricSpec,
    Sample,
    SnapshotCollector,
    spotify_samples,
    xp_samples,
)
from features.spotify import aggregate
from features.userinfo import member_label_resolver
from features.xp import calculate_level


def _by_name(samples: list[Sample], name: str) -> dict[tuple[tuple[str, str], ...], float]:
    return {s.labels: s.value for s in samples if s.name == name}


def _value(samples: list[Sample], name: str, **labels: str) -> float:
    for sample in samples:
        if sample.name == name and labels.items() <= dict(sample.labels).items():
            return sample.value
    raise AssertionError(f"no sample {name} {labels}")


GUILD = (("guild_id", "42"), ("guild", "Le Serveur"))


def _xp_docs() -> list[dict]:
    # Sorted by XP descending, like XpRepository.list_all_sorted_by_xp().
    return [
        {"_id": "1", "xp": 5000, "msg": 300},
        {"_id": "2", "xp": 900, "msg": 50},
        {"_id": "3", "xp": 10, "msg": 1},
    ]


def test_xp_samples_totals_levels_windows_and_sources():
    samples = xp_samples(
        "42",
        "Le Serveur",
        _xp_docs(),
        {"1": {"username": "alice", "display_name": "Alice"}},
        {"1d": 1, "7d": 2, "30d": 3},
        [
            {"source": "message", "xp": 5500, "events": 340},
            {"source": "voice", "xp": 410, "events": 41},
        ],
        top_n=25,
    )

    assert _value(samples, "michel_xp_members") == 3
    assert _value(samples, "michel_xp_points") == 5910
    assert _value(samples, "michel_xp_messages") == 351
    assert _value(samples, "michel_xp_active_members", window="7d") == 2
    assert _value(samples, "michel_xp_source_points", source="voice") == 410
    assert _value(samples, "michel_xp_source_events", source="message") == 340

    levels = _by_name(samples, "michel_xp_level_members")
    assert sum(levels.values()) == 3
    top_level = calculate_level(5000)[0]
    assert levels[(*GUILD, ("level", str(top_level)))] >= 1

    # Every sample carries the guild labels first.
    assert all(s.labels[:2] == GUILD for s in samples)


def test_xp_samples_caps_member_series_and_names_members():
    samples = xp_samples(
        "42", "Le Serveur", _xp_docs(), {"1": {"display_name": "Alice"}}, {}, [], 2
    )

    points = _by_name(samples, "michel_xp_member_points")
    assert points == {
        (*GUILD, ("user_id", "1"), ("member", "Alice")): 5000,
        # No stored name: the id stands in, as on the admin page.
        (*GUILD, ("user_id", "2"), ("member", "2")): 900,
    }
    assert _value(samples, "michel_xp_member_rank", user_id="2") == 2
    assert _value(samples, "michel_xp_member_messages", user_id="1") == 300


def test_xp_samples_empty_guild():
    samples = xp_samples("42", "Le Serveur", [], {}, {}, [], 25)
    assert _value(samples, "michel_xp_members") == 0
    assert _value(samples, "michel_xp_points") == 0
    assert not _by_name(samples, "michel_xp_member_points")


def _spotify_stats():
    playlist = [
        {"_id": "t1", "added_by": "7", "duration_ms": 200_000, "artists": ["A"]},
        {"_id": "t2", "added_by": "7", "duration_ms": 100_000, "artists": ["A", "B"]},
        {"_id": "t3", "added_by": "8", "duration_ms": 300_000, "artists": ["C"]},
    ]
    votes = [
        {"_id": "t9", "state": "conservée", "votes": {"7": "conserver", "8": "menfou"}},
        {"_id": "t8", "state": "supprimée", "votes": {"7": "supprimer"}},
        {"_id": "t1", "votes": {"8": "supprimer", "9": "conserver"}},  # open poll
    ]
    return aggregate(playlist, votes, "t1", now=datetime(2026, 3, 1, tzinfo=UTC), top=1)


def test_spotify_samples_playlist_polls_and_members():
    label_of = member_label_resolver({"7": "Septime"}, {"8": {"display_name": "huit"}}, "?")
    samples = spotify_samples("42", "Le Serveur", _spotify_stats(), label_of)

    assert _value(samples, "michel_spotify_tracks") == 3
    assert _value(samples, "michel_spotify_duration_seconds") == 600
    assert _value(samples, "michel_spotify_artists") == 3
    assert _value(samples, "michel_spotify_contributors") == 2
    assert _value(samples, "michel_spotify_polls_closed") == 2
    assert _value(samples, "michel_spotify_polls_outcome", outcome="kept") == 1
    assert _value(samples, "michel_spotify_polls_outcome", outcome="removed") == 1
    assert _value(samples, "michel_spotify_poll_removal_ratio") == 0.5
    assert _value(samples, "michel_spotify_poll_participation_max") == 2

    # The open poll: only that it is open and its turnout.
    assert _value(samples, "michel_spotify_poll_open") == 1
    assert _value(samples, "michel_spotify_poll_voters") == 2

    # top=1 keeps one contributor and one artist.
    assert _by_name(samples, "michel_spotify_contributor_tracks") == {
        (*GUILD, ("user_id", "7"), ("member", "Septime")): 2
    }
    assert _by_name(samples, "michel_spotify_artist_tracks") == {(*GUILD, ("artist", "A")): 2}

    assert _value(samples, "michel_spotify_voter_votes", member="huit", choice="menfou") == 1
    assert _value(samples, "michel_spotify_voter_votes", member="Septime", choice="supprimer") == 1


def test_spotify_samples_no_open_poll():
    stats = aggregate([], [], None)
    samples = spotify_samples("42", "Le Serveur", stats, str)
    assert _value(samples, "michel_spotify_poll_open") == 0
    assert _value(samples, "michel_spotify_poll_voters") == 0


def test_member_label_resolver_precedence():
    label_of = member_label_resolver(
        {"1": "Prénom"},
        {
            "1": {"display_name": "Display", "username": "user"},
            "2": {"display_name": "", "username": "user2"},
            "3": {"display_name": "Trois", "username": "user3"},
        },
        "Membre inconnu",
    )
    assert label_of("1") == "Prénom"
    assert label_of("2") == "user2"
    assert label_of("3") == "Trois"
    assert label_of("4") == "Membre inconnu"
    # A malformed discord2name entry is ignored rather than crashing.
    assert member_label_resolver(["nope"], {}, "x")("1") == "x"


def test_every_builder_sample_has_a_spec_with_matching_labels():
    specs = {spec.name: spec for spec in SNAPSHOT_METRICS}
    label_of = member_label_resolver({}, {}, "?")
    samples = [
        *xp_samples("42", "g", _xp_docs(), {}, {"1d": 1}, [{"source": "voice"}], 25),
        *spotify_samples("42", "g", _spotify_stats(), label_of),
    ]
    for sample in samples:
        assert sample.name in specs, sample.name
        assert tuple(k for k, _ in sample.labels) == specs[sample.name].labels, sample.name


def _scrape(registry: CollectorRegistry) -> str:
    return generate_latest(registry).decode()


def test_collector_serves_snapshot_and_drops_stale_series():
    specs = (MetricSpec("demo_value", "Demo.", ("who",)), MetricSpec("demo_live", "Live.", ()))
    live_calls = []

    def live():
        live_calls.append(1)
        return [Sample("demo_live", (), 7)]

    collector = SnapshotCollector(specs, live)
    registry = CollectorRegistry()
    registry.register(collector)

    collector.publish(
        [Sample("demo_value", (("who", "a"),), 1), Sample("demo_value", (("who", "b"),), 2)]
    )
    text = _scrape(registry)
    assert 'demo_value{who="a"} 1.0' in text
    assert 'demo_value{who="b"} 2.0' in text
    assert "demo_live 7.0" in text

    collector.publish([Sample("demo_value", (("who", "a"),), 3), Sample("unknown", (), 1)])
    text = _scrape(registry)
    assert 'demo_value{who="a"} 3.0' in text
    assert 'who="b"' not in text
    assert "unknown" not in text
    assert len(live_calls) == 2


def test_collector_emits_every_family_even_without_samples():
    registry = CollectorRegistry()
    registry.register(SnapshotCollector(SNAPSHOT_METRICS))
    text = _scrape(registry)
    for spec in SNAPSHOT_METRICS:
        assert f"# TYPE {spec.name} gauge" in text
