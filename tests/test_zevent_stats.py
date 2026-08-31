"""Unit tests for ``features.zevent.stats``.

Payload shapes are copied from the live EvenMoreStats API (2026 edition).
"""

from datetime import UTC, datetime

from features.zevent.stats import (
    LAN,
    ONLINE,
    build_location_index,
    event_schedule,
    goal_score,
    is_live,
    location_bucket,
    parse_datetime,
    parse_participants,
    parse_shows,
    resolve_location,
    select_event,
    upcoming_goals,
    upcoming_shows,
)

OVERVIEW_SAMPLE = [
    {
        "id": "019fc7a3-8347-7f70-9f9a-ae2ca8281d20",
        "name": "Alderiate",
        "location": "lan",
        "live": False,
        "amount_raised": 125050,
        "donation_goals_count": 14,
        "next_donation_goal": {"name": "Je compte jusqu'à 1 000", "amount": 200000},
        "socials": {"twitch": {"id": "77452537", "login": "alderiate"}},
    },
    {
        "id": "019fc7a3-8215-70b5-98e4-359831fa7f44",
        "name": "Aducine",
        "location": "remote",
        "live": True,
        "amount_raised": 5000,
        "next_donation_goal": {"name": "Apéro handcam", "amount": 800000},
        "socials": {"twitch": {"id": "44842076", "login": "aducine"}},
    },
    {
        "id": "019fc7a3-967c-7159-85bd-4720c7cc7e3b",
        "name": "Flonflon",
        "location": "remote_villa",
        "live": True,
        "amount_raised": 700,
        "socials": {"twitch": {"id": "123456", "login": "flonflon"}},
    },
    {
        # No Twitch social: unusable as a key, must be skipped.
        "name": "Orchestre Curieux",
        "location": "lan",
        "live": False,
        "amount_raised": 0,
        "socials": {},
    },
]

SHOWS_SAMPLE = [
    {
        "id": "01a00b12-eee6-73ae-abb9-2a006ebdc126",
        "name": "Concert",
        "description": "",
        "schedule": {"start": "2026-09-03T18:00:00Z", "end": "2026-09-03T21:30:00Z"},
        "all_day": False,
        "participants": [
            {
                "streamer_name": "ZEVENT",
                "role": "host",
                "socials": {"twitch": {"id": "77870741", "login": "zevent"}},
            },
            {"streamer_name": "Bigflo et Oli", "role": "guest", "socials": {}},
            {"streamer_name": "GIMS", "role": "guest", "socials": {}},
        ],
    },
    {
        "id": "01a00b2a-0000-7dfc-9d19-10edb89269b2",
        "name": "Rush final",
        "description": "",
        "schedule": {"start": "2026-09-06T18:00:00Z", "end": "2026-09-06T23:00:00Z"},
        "all_day": False,
        "participants": [],
    },
    {
        "id": "01a00b2a-1111-7dfc-9d19-10edb89269b2",
        "name": "Lancement ZEVENT",
        "description": "",
        "schedule": {"start": "2026-09-04T16:00:00Z", "end": "2026-09-04T16:10:00Z"},
        "all_day": False,
        "participants": [{"streamer_name": "Dach", "role": "host", "socials": {}}],
    },
]

EVENTS_SAMPLE = [
    {
        "id": "019d3f95-bd24-7e5d-861b-1de6243e3169",
        "name": "ZEvent 2025",
        "schedule": {"start": "2025-09-04T10:00:00Z", "end": "2025-09-08T02:00:00Z"},
    },
    {
        "id": "019f5bd1-fe07-7d78-a326-a02198a9d50f",
        "name": "ZEvent 2026",
        "schedule": {"start": "2026-09-03T18:00:00Z", "end": "2026-09-07T00:00:00Z"},
    },
    {
        "id": "019d20ef-fb85-7c13-b0dd-21ee8b6b9000",
        "name": "Birds Of Prey #6",
        "schedule": {"start": "2025-11-08T13:00:00Z", "end": "2025-11-09T16:00:00Z"},
    },
]


# ── locations ────────────────────────────────────────────────────────


def test_only_the_venue_counts_as_lan() -> None:
    assert location_bucket("lan") == LAN
    assert location_bucket("remote") == ONLINE
    # Satellite setups are remote, not the LAN.
    assert location_bucket("remote_zbase") == ONLINE
    assert location_bucket("remote_villa") == ONLINE
    assert location_bucket("remote_ankama") == ONLINE
    assert location_bucket(None) == ONLINE
    assert location_bucket("") == ONLINE


def test_parse_participants_buckets_and_converts_centimes() -> None:
    participants = parse_participants(OVERVIEW_SAMPLE)

    # The entry without a Twitch login is dropped.
    assert [p.twitch_login for p in participants] == ["alderiate", "aducine", "flonflon"]

    alderiate = participants[0]
    assert alderiate.location == LAN
    assert alderiate.amount_raised == 1250.50
    assert alderiate.live is False

    assert participants[1].location == ONLINE
    assert participants[2].location == ONLINE
    assert participants[2].amount_raised == 7.0


def test_parse_participants_tolerates_junk() -> None:
    assert parse_participants(None) == []
    assert parse_participants({"data": []}) == []
    assert parse_participants(["nope", 42, {"name": "x"}]) == []


def test_location_index_keys_on_login_and_twitch_id() -> None:
    index = build_location_index(parse_participants(OVERVIEW_SAMPLE))
    assert index["alderiate"] == LAN
    assert index["77452537"] == LAN
    assert index["aducine"] == ONLINE
    assert index["44842076"] == ONLINE


def test_resolve_location_uses_the_index_when_zevent_omits_it() -> None:
    """zevent.fr dropped ``location`` in 2026 — the index has to fill in."""
    index = build_location_index(parse_participants(OVERVIEW_SAMPLE))

    assert resolve_location({"twitch": "Alderiate", "twitch_id": "77452537"}, index) == LAN
    assert resolve_location({"twitch": "aducine", "twitch_id": "44842076"}, index) == ONLINE
    # Login missing on the zevent.fr side: the Twitch id still resolves it.
    assert resolve_location({"twitch": "", "twitch_id": "77452537"}, index) == LAN
    # Unknown streamer falls back to the far larger remote group.
    assert resolve_location({"twitch": "someoneelse"}, index) == ONLINE


def test_resolve_location_prefers_a_location_zevent_does_send() -> None:
    """If zevent.fr brings the field back, it wins over the index."""
    index = {"alderiate": ONLINE}
    assert resolve_location({"twitch": "alderiate", "location": "LAN"}, index) == LAN
    assert resolve_location({"twitch": "alderiate", "location": "lan"}, index) == LAN
    assert resolve_location({"twitch": "alderiate", "location": "Online"}, index) == ONLINE


# ── shows ────────────────────────────────────────────────────────────


def test_parse_shows_sorts_by_start_and_splits_roles() -> None:
    shows = parse_shows(SHOWS_SAMPLE)

    assert [s.name for s in shows] == ["Concert", "Lancement ZEVENT", "Rush final"]

    concert = shows[0]
    assert concert.start == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert concert.end == datetime(2026, 9, 3, 21, 30, tzinfo=UTC)
    assert concert.hosts == ["ZEVENT"]
    assert concert.guests == ["Bigflo et Oli", "GIMS"]
    assert shows[2].hosts == [] and shows[2].guests == []


def test_parse_shows_tolerates_junk() -> None:
    assert parse_shows(None) == []
    assert parse_shows({"data": []}) == []
    unnamed = parse_shows([{"schedule": {"start": "nonsense"}}])
    assert len(unnamed) == 1
    assert unnamed[0].name == "Événement"
    assert unnamed[0].start is None


def test_upcoming_shows_drops_finished_entries() -> None:
    shows = parse_shows(SHOWS_SAMPLE)
    during = datetime(2026, 9, 4, 17, 0, tzinfo=UTC)

    pending = upcoming_shows(shows, during)
    assert [s.name for s in pending] == ["Rush final"]

    assert len(upcoming_shows(shows, datetime(2026, 9, 1, tzinfo=UTC), limit=2)) == 2
    assert upcoming_shows(shows, datetime(2027, 1, 1, tzinfo=UTC)) == []


# ── event selection ──────────────────────────────────────────────────


def test_select_event_prefers_the_running_edition() -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now)
    assert event is not None
    assert event["name"] == "ZEvent 2026"


def test_select_event_falls_back_to_the_next_one_to_start() -> None:
    now = datetime(2025, 9, 20, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now)
    assert event is not None
    # Birds Of Prey (November) starts before ZEvent 2026.
    assert event["name"] == "Birds Of Prey #6"


def test_select_event_falls_back_to_the_most_recent_past_edition() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now)
    assert event is not None
    assert event["name"] == "ZEvent 2026"


def test_select_event_honours_an_explicit_id() -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now, "019d3f95-bd24-7e5d-861b-1de6243e3169")
    assert event is not None
    assert event["name"] == "ZEvent 2025"

    assert select_event(EVENTS_SAMPLE, now, "does-not-exist") is None


def test_select_event_tolerates_junk() -> None:
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert select_event(None, now) is None
    assert select_event([], now) is None
    assert select_event(["nope"], now) is None


# ── timestamps ───────────────────────────────────────────────────────


def test_parse_datetime_handles_z_suffix_and_naive_values() -> None:
    assert parse_datetime("2026-09-03T18:00:00Z") == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert parse_datetime("2026-09-03T18:00:00+02:00").utcoffset().total_seconds() == 7200
    # A naive timestamp is assumed UTC rather than crashing the comparison.
    assert parse_datetime("2026-09-03T18:00:00") == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert parse_datetime("") is None
    assert parse_datetime(None) is None
    assert parse_datetime("not a date") is None


# ── donation goals ───────────────────────────────────────────────────


def test_parse_participants_reads_the_next_goal() -> None:
    by_login = {p.twitch_login: p for p in parse_participants(OVERVIEW_SAMPLE)}

    alderiate = by_login["alderiate"]
    assert alderiate.next_goal is not None
    assert alderiate.next_goal.name == "Je compte jusqu'à 1 000"
    assert alderiate.next_goal.amount == 2000.0  # centimes -> euros

    # Flonflon's fixture entry has no goal at all.
    assert by_login["flonflon"].next_goal is None


def test_parse_goal_rejects_unusable_entries() -> None:
    def one(goal: object) -> object:
        payload = [
            {
                "name": "X",
                "location": "lan",
                "amount_raised": 0,
                "next_donation_goal": goal,
                "socials": {"twitch": {"id": "1", "login": "x"}},
            }
        ]
        return parse_participants(payload)[0].next_goal

    assert one(None) is None
    assert one("nope") is None
    assert one({"name": "no amount"}) is None
    assert one({"amount": 100}) is None  # no name
    assert one({"name": "   ", "amount": 100}) is None


def test_upcoming_goals_ranks_on_score_not_live_status() -> None:
    """Being live is a display marker, not a ranking key.

    Aducine is live but has raised 50 € against an 8 000 € goal (0.6%);
    Alderiate is offline at 1 250 € of 2 000 € (63%), so it leads.
    """
    participants = parse_participants(OVERVIEW_SAMPLE)
    ranked = upcoming_goals(participants)

    assert [p.twitch_login for p in ranked] == ["alderiate", "aducine"]
    assert upcoming_goals(participants, limit=1)[0].twitch_login == "alderiate"


def test_upcoming_goals_is_empty_without_goals() -> None:
    assert upcoming_goals([]) == []
    no_goals = [p for p in parse_participants(OVERVIEW_SAMPLE) if p.next_goal is None]
    assert upcoming_goals(no_goals) == []


# ── event schedule ───────────────────────────────────────────────────


def test_event_schedule_splits_open_and_fundraising_starts() -> None:
    event = {
        "schedule": {"start": "2026-09-03T18:00:00Z", "end": "2026-09-07T00:00:00Z"},
        "schedule_raising": {"start": "2026-09-04T16:00:00Z", "end": "2026-09-07T00:00:00Z"},
    }
    start, raising = event_schedule(event)
    assert start == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert raising == datetime(2026, 9, 4, 16, 0, tzinfo=UTC)


def test_event_schedule_tolerates_missing_blocks() -> None:
    assert event_schedule(None) == (None, None)
    assert event_schedule({}) == (None, None)
    assert event_schedule({"schedule": "nope"}) == (None, None)
    start, raising = event_schedule({"schedule": {"start": "2026-09-03T18:00:00Z"}})
    assert start == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert raising is None


def test_upcoming_goals_ranking_is_deterministic_on_ties() -> None:
    """Equal gaps must not let payload order churn the rendered embed."""

    def entry(name: str, login: str) -> dict:
        return {
            "name": name,
            "location": "remote",
            "live": False,
            "amount_raised": 0,
            "next_donation_goal": {"name": "same goal", "amount": 100},
            "socials": {"twitch": {"id": login, "login": login}},
        }

    forward = parse_participants([entry("Zoe", "zoe"), entry("alice", "alice")])
    backward = parse_participants([entry("alice", "alice"), entry("Zoe", "zoe")])

    assert [p.display_name for p in upcoming_goals(forward)] == ["alice", "Zoe"]
    assert [p.display_name for p in upcoming_goals(backward)] == ["alice", "Zoe"]


# ── goal ranking ─────────────────────────────────────────────────────
#
# Rows below are real entries from the ZEvent 2025 payload
# (/events/{2025}/donation_goals/overview), trimmed to the fields the parser
# reads. They cover the case the first implementation got wrong: ranking by
# absolute remaining gap put Kalaxee (1 € raised, 5 € goal) above every
# headline streamer, because a small gap and a small streamer are the same
# thing under that metric.


def _entry(name, raised, goal, live=False, loc="lan"):  # noqa: FBT002
    return {
        "name": name,
        "location": loc,
        "live": live,
        "amount_raised": raised,  # centimes, as the API reports
        "next_donation_goal": {"name": f"objectif {name}", "amount": goal},
        "socials": {"twitch": {"id": name.lower(), "login": name.lower()}},
    }


ZEVENT_2025_SAMPLE = [
    _entry("Kalaxee", 100, 500, loc="remote"),  # 1 € raised, 5 € goal, 20%
    _entry("lastbaroudeur", 100, 10_000, loc="remote"),  # 1 € raised, 100 € goal, 1%
    _entry("Clemovitch", 42_085_200, 45_000_000),  # 420 852 € raised, 94%
    _entry("AntoineDaniel", 121_883_800, 200_000_000),  # 1 218 838 € raised, 61%
    _entry("ZeratoR", 115_421_200, 1_000_000_000),  # 1 154 212 € raised, 12%
    _entry("MoMaN", 4_798_100, 5_000_000),  # 47 981 € raised, 96%
]


def test_tiny_streamers_no_longer_lead_the_ranking() -> None:
    """The regression: a 1 €-raised channel outranking every headline name."""
    ranked = upcoming_goals(parse_participants(ZEVENT_2025_SAMPLE), limit=3)
    names = [p.display_name for p in ranked]

    assert "Kalaxee" not in names
    assert "lastbaroudeur" not in names
    assert names[0] == "Clemovitch"  # 94% of a 450 000 € goal


def test_progress_weight_slides_between_prominence_and_imminence() -> None:
    participants = parse_participants(ZEVENT_2025_SAMPLE)

    # weight 0 drops the progress term entirely: biggest fundraiser wins even
    # though it is only 61% of the way there.
    assert upcoming_goals(participants, progress_weight=0.0)[0].display_name == "AntoineDaniel"

    # weight 1 balances the two: Clemovitch (94% of 450 000 €) leads.
    assert upcoming_goals(participants, progress_weight=1.0)[0].display_name == "Clemovitch"

    # A high weight favours whoever is nearest their goal — MoMaN at 96%,
    # despite raising a tenth of what Clemovitch did.
    assert upcoming_goals(participants, progress_weight=8.0)[0].display_name == "MoMaN"


def test_a_huge_but_distant_goal_does_not_lead() -> None:
    """ZeratoR raised the second-most but sits at 12% of a 10 M € goal."""
    ranked = upcoming_goals(parse_participants(ZEVENT_2025_SAMPLE))
    assert ranked[0].display_name != "ZeratoR"


def test_negative_weight_is_clamped_not_inverted() -> None:
    participants = parse_participants(ZEVENT_2025_SAMPLE)
    assert [p.display_name for p in upcoming_goals(participants, progress_weight=-5.0)] == [
        p.display_name for p in upcoming_goals(participants, progress_weight=0.0)
    ]


def test_before_the_event_the_biggest_goal_leads() -> None:
    """Every amount is zero pre-event, so scores tie and goal size decides.

    Without that tiebreak the ordering fell back to alphabetical, which is how
    a list of 1 € joke openers ended up in the embed.
    """
    pre_event = [
        _entry("ZZZ_big", 0, 10_000_000),  # 100 000 € pledge
        _entry("aaa_small", 0, 100),  # 1 € opener, alphabetically first
        _entry("mmm_mid", 0, 100_000),  # 1 000 €
    ]
    participants = parse_participants(pre_event)
    assert all(p.amount_raised == 0 for p in participants)
    assert all(goal_score(p) == 0.0 for p in participants)

    assert [p.display_name for p in upcoming_goals(participants)] == [
        "ZZZ_big",
        "mmm_mid",
        "aaa_small",
    ]


def test_goal_score_is_zero_without_a_goal_amount() -> None:
    (p,) = parse_participants([_entry("x", 5_000, 0)])
    assert goal_score(p) == 0.0


# ── online status ────────────────────────────────────────────────────


def test_being_live_breaks_a_tie_between_equal_goals() -> None:
    """Presence is a factor, not an override."""
    pair = [
        _entry("offline_one", 10_000, 20_000, live=False),
        _entry("live_one", 10_000, 20_000, live=True),
    ]
    ranked = upcoming_goals(parse_participants(pair))
    assert [p.display_name for p in ranked] == ["live_one", "offline_one"]


def test_presence_does_not_override_a_far_better_goal() -> None:
    """The bug the first version had: live-first put a 0.6% goal on top."""
    mixed = [
        # live, but barely started: 50 € of an 8 000 € goal
        _entry("live_barely_started", 5_000, 800_000, live=True),
        # offline, but nearly there: 420 852 € of a 450 000 € goal
        _entry("offline_nearly_there", 42_085_200, 45_000_000, live=False),
    ]
    ranked = upcoming_goals(parse_participants(mixed))
    assert ranked[0].display_name == "offline_nearly_there"


def test_offline_factor_bounds() -> None:
    mixed = parse_participants(
        [
            _entry("offline_big", 42_085_200, 45_000_000, live=False),
            _entry("live_small", 1_000, 2_000, live=True),
        ]
    )
    # 1.0 ignores the stream status: the big offline goal still wins.
    assert upcoming_goals(mixed, offline_factor=1.0)[0].display_name == "offline_big"
    # 0.0 zeroes every offline score, so any live streamer comes first.
    assert upcoming_goals(mixed, offline_factor=0.0)[0].display_name == "live_small"
    # Out-of-range values are clamped rather than inverting the ranking.
    assert [p.display_name for p in upcoming_goals(mixed, offline_factor=5.0)] == [
        p.display_name for p in upcoming_goals(mixed, offline_factor=1.0)
    ]
    assert [p.display_name for p in upcoming_goals(mixed, offline_factor=-5.0)] == [
        p.display_name for p in upcoming_goals(mixed, offline_factor=0.0)
    ]


def test_offline_factor_is_neutral_when_nobody_is_live() -> None:
    """A uniform factor must not reshuffle an all-offline field."""
    participants = parse_participants(ZEVENT_2025_SAMPLE)
    assert not any(p.live for p in participants)
    baseline = [p.display_name for p in upcoming_goals(participants, offline_factor=1.0)]
    assert [p.display_name for p in upcoming_goals(participants, offline_factor=0.3)] == baseline


# ── presence comes from Twitch, not the cached API flag ──────────────


def test_live_logins_from_twitch_override_the_cached_flag() -> None:
    """The stats API is cached for minutes; Twitch is polled every refresh."""
    entries = [
        # Cached as offline, but Twitch says they just went live.
        _entry("JustWentLive", 10_000, 20_000, live=False),
        # Cached as live, but Twitch says the stream already ended.
        _entry("JustWentOffline", 10_000, 20_000, live=True),
    ]
    participants = parse_participants(entries)
    fresh = {"justwentlive"}

    assert is_live(participants[0], fresh) is True
    assert is_live(participants[1], fresh) is False

    ranked = upcoming_goals(participants, live_logins=fresh)
    assert ranked[0].display_name == "JustWentLive"


def test_without_twitch_data_the_cached_flag_still_applies() -> None:
    """Twitch may be unavailable; the API flag is the fallback, not a hard fail."""
    participants = parse_participants(
        [
            _entry("cached_offline", 10_000, 20_000, live=False),
            _entry("cached_live", 10_000, 20_000, live=True),
        ]
    )
    assert is_live(participants[1], None) is True
    assert upcoming_goals(participants, live_logins=None)[0].display_name == "cached_live"


def test_an_empty_live_set_means_nobody_is_live() -> None:
    """An empty set is 'Twitch says nobody', distinct from None ('no data')."""
    participants = parse_participants([_entry("cached_live", 10_000, 20_000, live=True)])
    assert is_live(participants[0], set()) is False
    assert is_live(participants[0], None) is True
