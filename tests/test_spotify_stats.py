"""Dashboard statistics aggregated from the Spotify playlist mirror and vote archive."""

from datetime import UTC, datetime

from features.spotify.stats import (
    CurrentPoll,
    DurationBucket,
    MonthCount,
    MonthParticipation,
    NamedCount,
    PlaylistStats,
    VoterTally,
    aggregate,
    parse_timestamp,
)

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def track(tid, *, by="1", at="2026-01-10T10:00:00Z", ms=200_000, artists=("A",)):
    return {
        "_id": tid,
        "added_by": by,
        "added_at": at,
        "duration_ms": ms,
        "name": f"Song {tid}",
        "artists": list(artists),
        "album": "Album",
    }


def poll(tid, *, date="2026-01-05", state="conservée", votes=None, by="1"):
    doc = {"_id": tid, "name": f"A - Song {tid}", "date": date, "added_by": by}
    if state is not None:
        doc["state"] = state
    if votes is not None:
        doc["votes"] = votes
    return doc


def test_parse_timestamp_accepts_strings_and_datetimes():
    assert parse_timestamp("2026-01-10T10:00:00Z") == datetime(2026, 1, 10, 10, tzinfo=UTC)
    # /addsong stores a BSON date, which Motor hands back naive (UTC).
    assert parse_timestamp(datetime(2026, 1, 10, 10)) == datetime(2026, 1, 10, 10, tzinfo=UTC)
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp(12) is None


def test_empty_inputs_give_empty_stats():
    stats = aggregate([], [], None, now=NOW)
    assert stats == PlaylistStats()
    assert stats.to_dict()["current_poll"] is None


def test_summary_counts_tracks_duration_artists_and_contributors():
    stats = aggregate(
        [
            track("t1", by="1", ms=180_000, artists=("A", "B")),
            track("t2", by="2", ms=240_000, artists=("B",)),
            track("t3", by="", ms=None),
        ],
        [],
        now=NOW,
    )
    s = stats.summary
    assert s.track_count == 3
    assert s.total_duration_ms == 420_000
    assert s.avg_duration_ms == 210_000  # over tracks with a duration only
    assert s.artist_count == 2
    assert s.contributor_count == 2  # the empty added_by is ignored


def test_additions_bucket_by_paris_month_and_fill_gaps_up_to_now():
    stats = aggregate(
        [
            # 23:30 UTC on Oct 31 is already November in Paris.
            track("t1", at="2025-10-31T23:30:00Z"),
            track("t2", at=datetime(2026, 1, 3, 9)),
            track("t3", at="2026-01-20T09:00:00+00:00"),
            track("t4", at="garbage"),
        ],
        [],
        now=NOW,
    )
    assert stats.additions_by_month == [
        MonthCount("2025-11", 1),
        MonthCount("2025-12", 0),
        MonthCount("2026-01", 2),
        MonthCount("2026-02", 0),
        MonthCount("2026-03", 0),
    ]


def test_top_lists_rank_by_count_then_name():
    stats = aggregate(
        [
            track("t1", by="2", artists=("beta", "Alpha")),
            track("t2", by="1", artists=("Alpha",)),
            track("t3", by="2", artists=("Gamma", "Gamma")),  # duplicate credit counts once
            track("t4", by="1", artists=("beta",)),
        ],
        [],
        now=NOW,
        top=2,
    )
    assert stats.top_contributors == [NamedCount("1", 2), NamedCount("2", 2)]
    assert stats.top_artists == [NamedCount("Alpha", 2), NamedCount("beta", 2)]
    assert stats.summary.artist_count == 3


def test_duration_buckets_trim_empty_edges_and_cap_overflow():
    stats = aggregate(
        [
            track("t1", ms=29_900),  # 0–30 s
            track("t2", ms=30_000),  # 30–60 s
            track("t3", ms=95_000),  # 90–120 s, leaves 60–90 s empty in between
            track("t4", ms=610_000),  # 10 min +
            track("t5", ms=3_600_000),  # 10 min +
        ],
        [],
        now=NOW,
    )
    buckets = stats.duration_buckets
    assert buckets[0] == DurationBucket(0, 30, 1)
    assert buckets[1] == DurationBucket(30, 60, 1)
    assert buckets[2] == DurationBucket(60, 90, 0)
    assert buckets[3] == DurationBucket(90, 120, 1)
    assert buckets[-1] == DurationBucket(600, None, 2)
    assert len(buckets) == 21


def test_duration_buckets_start_at_first_used_bucket():
    stats = aggregate([track("t1", ms=200_000), track("t2", ms=215_000)], [], now=NOW)
    assert stats.duration_buckets == [DurationBucket(180, 210, 1), DurationBucket(210, 240, 1)]


def test_vote_summary_counts_closed_polls_only():
    stats = aggregate(
        [],
        [
            poll("p1", votes={"1": "conserver", "2": "supprimer", "3": "menfou"}),
            poll("p2", state="supprimée", votes={"1": "supprimer", "2": "supprimer"}),
            poll("p3", state="supprimée"),  # imported without details
            poll("p4", state="conservée", votes={}),  # nobody voted
            poll("open", state=None, votes={"1": "conserver"}),
        ],
        now=NOW,
    )
    v = stats.votes
    assert (v.polls_closed, v.kept, v.removed) == (4, 2, 2)
    assert v.removal_rate == 0.5
    assert v.avg_participation == round((3 + 2 + 0) / 3, 1)
    assert v.max_participation == 3


def test_voters_ignore_unknown_choices_and_rank_by_total():
    stats = aggregate(
        [],
        [
            poll("p1", votes={"1": "conserver", "2": "supprimer", "3": "peut-être"}),
            poll("p2", votes={"1": "menfou", "2": "supprimer"}),
            poll("p3", votes={"2": "conserver"}),
            poll("open", state=None, votes={"3": "conserver"}),
        ],
        now=NOW,
    )
    assert stats.voters == [
        VoterTally("2", conserver=1, menfou=0, supprimer=2),
        VoterTally("1", conserver=1, menfou=1, supprimer=0),
    ]
    # The unknown choice doesn't count as participation either.
    assert stats.votes.max_participation == 2


def test_participation_by_month_leaves_gaps_empty():
    stats = aggregate(
        [],
        [
            poll("p1", date="2025-11-02", votes={"1": "conserver", "2": "menfou"}),
            poll("p2", date="2025-11-03", votes={"1": "conserver"}),
            poll("p3", date="2026-01-04", votes={"1": "supprimer"}),
            poll("p4", date="n/a", votes={"1": "supprimer"}),
        ],
        now=NOW,
    )
    assert stats.participation_by_month == [
        MonthParticipation("2025-11", polls=2, average=1.5),
        MonthParticipation("2025-12", polls=0, average=None),
        MonthParticipation("2026-01", polls=1, average=1.0),
    ]


def test_current_poll_is_the_open_vote_doc():
    stats = aggregate(
        [],
        [poll("open", state=None, votes={"2": "supprimer", "1": "conserver", "3": "?"}, by="9")],
        "open",
        now=NOW,
    )
    assert stats.current_poll == CurrentPoll(
        track_id="open",
        name="A - Song open",
        date="2026-01-05",
        added_by="9",
        conserver=1,
        menfou=0,
        supprimer=1,
        voters=["1", "2"],
    )


def test_current_poll_absent_once_closed_or_unknown():
    closed = [poll("p1", state="conservée", votes={"1": "conserver"})]
    assert aggregate([], closed, "p1", now=NOW).current_poll is None
    assert aggregate([], closed, "missing", now=NOW).current_poll is None
    assert aggregate([], [poll("p2", state=None)], None, now=NOW).current_poll is None


def test_to_dict_is_json_ready():
    stats = aggregate([track("t1")], [poll("open", state=None, votes={})], "open", now=NOW)
    data = stats.to_dict()
    assert data["summary"]["track_count"] == 1
    assert data["top_artists"] == [{"key": "A", "count": 1}]
    assert data["current_poll"]["voters"] == []
