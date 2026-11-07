"""Turn XP and Spotify statistics into Prometheus samples — no DB, no Discord.

The metrics extension fetches the raw documents on the bot loop and hands them
to the builders here; :class:`features.metrics.collector.SnapshotCollector`
serves the result. Every per-guild series carries ``guild_id`` and ``guild``
(the server's name); per-member series are capped at the top N and labelled
with the member's display name only, so no Discord id reaches the monitoring
system. Two members sharing a name are told apart with a " (2)" suffix.

All values are gauges: they describe the database as it is now, and
Prometheus builds the history (``delta(michel_xp_points[1d])`` is the XP
earned over a day).
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from features.spotify.stats import PlaylistStats
from features.xp.stats import level_distribution, summarize, top_members

GUILD_LABELS: tuple[str, ...] = ("guild_id", "guild")
MEMBER_LABELS: tuple[str, ...] = (*GUILD_LABELS, "member")


class _MemberNames:
    """Hands out one distinct ``member`` label per user id, in first-seen order."""

    def __init__(self, label_of: Callable[[str], str]) -> None:
        self._label_of = label_of
        self._by_id: dict[str, str] = {}
        self._taken: set[str] = set()

    def __call__(self, user_id: str) -> str:
        if user_id not in self._by_id:
            base = self._label_of(user_id)
            label, n = base, 1
            while label in self._taken:
                n += 1
                label = f"{base} ({n})"
            self._by_id[user_id] = label
            self._taken.add(label)
        return self._by_id[user_id]


# Windows (label value → days) reported by ``michel_xp_active_members``.
ACTIVE_WINDOWS: dict[str, int] = {"1d": 1, "7d": 7, "30d": 30}


@dataclass(frozen=True)
class MetricSpec:
    name: str
    documentation: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Sample:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float


def _spec(name: str, documentation: str, *extra: str, base=GUILD_LABELS) -> MetricSpec:
    return MetricSpec(name, documentation, (*base, *extra))


SNAPSHOT_METRICS: tuple[MetricSpec, ...] = (
    # XP
    _spec("michel_xp_members", "Members with an XP record."),
    _spec("michel_xp_points", "XP held by all members."),
    _spec("michel_xp_messages", "Messages counted toward XP."),
    _spec("michel_xp_level_members", "Members per level.", "level"),
    _spec("michel_xp_active_members", "Members who earned XP within the window.", "window"),
    _spec("michel_xp_source_points", "XP earned per source since the event log began.", "source"),
    _spec("michel_xp_source_events", "XP awards per source since the event log began.", "source"),
    _spec("michel_xp_member_points", "XP of a top member.", base=MEMBER_LABELS),
    _spec("michel_xp_member_level", "Level of a top member.", base=MEMBER_LABELS),
    _spec("michel_xp_member_messages", "Messages counted for a top member.", base=MEMBER_LABELS),
    _spec("michel_xp_member_rank", "Leaderboard rank of a top member.", base=MEMBER_LABELS),
    # Spotify
    _spec("michel_spotify_tracks", "Tracks in the playlist."),
    _spec("michel_spotify_duration_seconds", "Total playlist duration."),
    _spec("michel_spotify_artists", "Distinct artists in the playlist."),
    _spec("michel_spotify_contributors", "Distinct members who added a track."),
    _spec("michel_spotify_polls_closed", "Closed removal polls."),
    _spec("michel_spotify_polls_outcome", "Closed removal polls per outcome.", "outcome"),
    _spec("michel_spotify_poll_removal_ratio", "Share of closed polls that removed the track."),
    _spec("michel_spotify_poll_participation_avg", "Average voters per closed poll."),
    _spec("michel_spotify_poll_participation_max", "Most voters on a single closed poll."),
    _spec("michel_spotify_poll_open", "1 while a removal poll is open."),
    _spec("michel_spotify_poll_voters", "Voters on the open poll so far."),
    _spec(
        "michel_spotify_contributor_tracks",
        "Tracks added by a top contributor.",
        base=MEMBER_LABELS,
    ),
    _spec("michel_spotify_artist_tracks", "Tracks by a top artist.", "artist"),
    _spec(
        "michel_spotify_voter_votes",
        "Votes cast by a top voter, per choice.",
        "choice",
        base=MEMBER_LABELS,
    ),
)


class _Builder:
    """Accumulates samples that share a guild's label values."""

    def __init__(self, guild_id: str, guild_name: str) -> None:
        self._guild = (("guild_id", guild_id), ("guild", guild_name))
        self.samples: list[Sample] = []

    def add(self, name: str, value: float, **labels: str) -> None:
        self.samples.append(Sample(name, (*self._guild, *labels.items()), float(value)))


def xp_samples(
    guild_id: str,
    guild_name: str,
    docs: list[dict[str, Any]],
    names: Mapping[str, Mapping[str, str]],
    active_counts: Mapping[str, int],
    source_totals: Iterable[Mapping[str, Any]],
    top_n: int,
) -> list[Sample]:
    """Samples for one guild's XP.

    ``docs`` is ``XpRepository.list_all_sorted_by_xp()`` (XP descending),
    ``names`` the ``UserInfoRepository.get_names`` result for the top
    members, ``active_counts`` maps each :data:`ACTIVE_WINDOWS` key to
    ``count_active_users``, ``source_totals`` is ``totals_by_source()``.
    """
    out = _Builder(guild_id, guild_name)
    summary = summarize(docs)
    out.add("michel_xp_members", summary["members"])
    out.add("michel_xp_points", summary["total_xp"])
    out.add("michel_xp_messages", summary["total_messages"])
    for bucket in level_distribution(docs):
        out.add("michel_xp_level_members", bucket["users"], level=str(bucket["level"]))
    for window, count in active_counts.items():
        out.add("michel_xp_active_members", count, window=window)
    for row in source_totals:
        source = str(row.get("source") or "message")
        out.add("michel_xp_source_points", int(row.get("xp") or 0), source=source)
        out.add("michel_xp_source_events", int(row.get("events") or 0), source=source)
    ranked = top_members(docs, {k: dict(v) for k, v in names.items()}, limit=top_n)
    display = {m["user_id"]: m["display_name"] for m in ranked}
    member_name = _MemberNames(display.__getitem__)
    for member in ranked:
        who = {"member": member_name(member["user_id"])}
        out.add("michel_xp_member_points", member["xp"], **who)
        out.add("michel_xp_member_level", member["level"], **who)
        out.add("michel_xp_member_messages", member["messages"], **who)
        out.add("michel_xp_member_rank", member["rank"], **who)
    return out.samples


def spotify_samples(
    guild_id: str,
    guild_name: str,
    stats: PlaylistStats,
    label_of: Callable[[str], str],
) -> list[Sample]:
    """Samples for one guild's Spotify playlist and removal polls.

    ``stats`` comes from ``features.spotify.aggregate`` (its ``top`` and
    ``top_voters`` cap the per-member series); ``label_of`` names a member
    from their id. The open poll's tally is left out, like on the public page:
    only that a poll is open and how many have voted.
    """
    out = _Builder(guild_id, guild_name)
    summary = stats.summary
    out.add("michel_spotify_tracks", summary.track_count)
    out.add("michel_spotify_duration_seconds", summary.total_duration_ms / 1000)
    out.add("michel_spotify_artists", summary.artist_count)
    out.add("michel_spotify_contributors", summary.contributor_count)

    votes = stats.votes
    out.add("michel_spotify_polls_closed", votes.polls_closed)
    out.add("michel_spotify_polls_outcome", votes.kept, outcome="kept")
    out.add("michel_spotify_polls_outcome", votes.removed, outcome="removed")
    out.add("michel_spotify_poll_removal_ratio", votes.removal_rate)
    out.add("michel_spotify_poll_participation_avg", votes.avg_participation)
    out.add("michel_spotify_poll_participation_max", votes.max_participation)

    poll = stats.current_poll
    out.add("michel_spotify_poll_open", 1 if poll else 0)
    out.add("michel_spotify_poll_voters", len(poll.voters) if poll else 0)

    member_name = _MemberNames(label_of)
    for contributor in stats.top_contributors:
        who = {"member": member_name(contributor.key)}
        out.add("michel_spotify_contributor_tracks", contributor.count, **who)
    for artist in stats.top_artists:
        out.add("michel_spotify_artist_tracks", artist.count, artist=artist.key)
    for voter in stats.voters:
        who = {"member": member_name(voter.user_id)}
        out.add("michel_spotify_voter_votes", voter.conserver, **who, choice="conserver")
        out.add("michel_spotify_voter_votes", voter.menfou, **who, choice="menfou")
        out.add("michel_spotify_voter_votes", voter.supprimer, **who, choice="supprimer")
    return out.samples


__all__ = [
    "ACTIVE_WINDOWS",
    "GUILD_LABELS",
    "MEMBER_LABELS",
    "SNAPSHOT_METRICS",
    "MetricSpec",
    "Sample",
    "spotify_samples",
    "xp_samples",
]
