"""Aggregate the playlist mirror and the vote archive into dashboard statistics.

Pure arithmetic over the raw documents :class:`SpotifyRepository` returns — the
Web UI route fetches, this shapes, the SPA draws. Shapes read here, as written
by ``extensions/spotify``:

- playlist items: ``{_id, added_by, added_at, duration_ms, name, artists, album}``.
  ``added_at`` is an ISO string when the playlist sync mirrored the track, but
  a ``datetime`` when ``/addsong`` inserted it; both are accepted.
- vote docs: ``{_id, name, date: "YYYY-MM-DD", added_by, votes: {user_id: choice},
  state}``. ``state`` is only set once the poll closes. Docs imported from the
  old bot have no ``votes`` at all ("pas de détails sur le vote"): they count
  toward the outcome, not toward participation.

Every ranked list has a deterministic tie-break so an unchanged database
serialises identically.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VOTE_CHOICES = ("conserver", "menfou", "supprimer")
STATE_KEPT = "conservée"
STATE_REMOVED = "supprimée"

BUCKET_SECONDS = 30
OVERFLOW_SECONDS = 600  # everything from 10 min up shares the last bucket


def _display_zone() -> tzinfo:
    """The audience's clock. Falls back to UTC if the tz database is absent."""
    try:
        return ZoneInfo("Europe/Paris")
    except (ZoneInfoNotFoundError, KeyError):  # pragma: no cover - image-dependent
        return UTC


DISPLAY_TZ = _display_zone()


@dataclass(frozen=True)
class PlaylistSummary:
    track_count: int = 0
    total_duration_ms: int = 0
    avg_duration_ms: int = 0
    artist_count: int = 0
    contributor_count: int = 0


@dataclass(frozen=True)
class VoteSummary:
    polls_closed: int = 0
    kept: int = 0
    removed: int = 0
    removal_rate: float = 0.0
    avg_participation: float = 0.0
    max_participation: int = 0


@dataclass(frozen=True)
class NamedCount:
    """A ranked entry — ``key`` is a Discord user id or an artist name."""

    key: str
    count: int


@dataclass(frozen=True)
class MonthCount:
    month: str  # "YYYY-MM"
    count: int


@dataclass(frozen=True)
class MonthParticipation:
    month: str  # "YYYY-MM"
    polls: int
    average: float | None  # mean voters per poll; None for a month with no poll


@dataclass(frozen=True)
class DurationBucket:
    start_s: int
    end_s: int | None  # None marks the open-ended overflow bucket
    count: int


@dataclass(frozen=True)
class VoterTally:
    user_id: str
    conserver: int
    menfou: int
    supprimer: int


@dataclass(frozen=True)
class CurrentPoll:
    track_id: str
    name: str
    date: str | None
    added_by: str | None
    conserver: int
    menfou: int
    supprimer: int
    voters: list[str] = field(default_factory=list)


@dataclass
class PlaylistStats:
    summary: PlaylistSummary = field(default_factory=PlaylistSummary)
    votes: VoteSummary = field(default_factory=VoteSummary)
    additions_by_month: list[MonthCount] = field(default_factory=list)
    top_contributors: list[NamedCount] = field(default_factory=list)
    top_artists: list[NamedCount] = field(default_factory=list)
    duration_buckets: list[DurationBucket] = field(default_factory=list)
    voters: list[VoterTally] = field(default_factory=list)
    participation_by_month: list[MonthParticipation] = field(default_factory=list)
    current_poll: CurrentPoll | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def user_ids(self) -> set[str]:
        """Every Discord user id the statistics mention."""
        ids = {c.key for c in self.top_contributors} | {v.user_id for v in self.voters}
        if self.current_poll:
            ids.update(self.current_poll.voters)
            if self.current_poll.added_by:
                ids.add(self.current_poll.added_by)
        return ids

    def with_user_labels(self, label_of: Callable[[str], str]) -> "PlaylistStats":
        """A copy with every Discord user id replaced by ``label_of(id)``.

        The public dashboard serves names, never ids. Artist names in
        ``top_artists`` share the ``NamedCount`` shape but are left untouched.
        """
        poll = self.current_poll
        if poll:
            poll = replace(
                poll,
                added_by=label_of(poll.added_by) if poll.added_by else None,
                voters=[label_of(v) for v in poll.voters],
            )
        return replace(
            self,
            top_contributors=[replace(c, key=label_of(c.key)) for c in self.top_contributors],
            voters=[replace(v, user_id=label_of(v.user_id)) for v in self.voters],
            current_poll=poll,
        )


def parse_timestamp(value: Any) -> datetime | None:
    """Read an ``added_at`` value (ISO string or datetime); naive means UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _month_key(moment: datetime) -> str:
    local = moment.astimezone(DISPLAY_TZ)
    return f"{local.year:04d}-{local.month:02d}"


def _months_between(first: str, last: str) -> list[str]:
    """Every "YYYY-MM" from ``first`` to ``last`` inclusive."""
    year, month = (int(part) for part in first.split("-"))
    end = tuple(int(part) for part in last.split("-"))
    months = []
    while (year, month) <= end:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _poll_month(date: Any) -> str | None:
    if not isinstance(date, str):
        return None
    try:
        parsed = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _ranked(counter: Counter[str], limit: int) -> list[NamedCount]:
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].casefold(), kv[0]))
    return [NamedCount(key, count) for key, count in ordered[:limit]]


def _tally(votes: Mapping[str, Any]) -> Counter[str]:
    return Counter(choice for choice in votes.values() if choice in VOTE_CHOICES)


def _duration_buckets(durations: list[int]) -> list[DurationBucket]:
    last_index = OVERFLOW_SECONDS // BUCKET_SECONDS
    counts = [0] * (last_index + 1)
    for ms in durations:
        counts[min(ms // 1000 // BUCKET_SECONDS, last_index)] += 1
    used = [i for i, count in enumerate(counts) if count]
    if not used:
        return []
    return [
        DurationBucket(
            start_s=i * BUCKET_SECONDS,
            end_s=None if i == last_index else (i + 1) * BUCKET_SECONDS,
            count=counts[i],
        )
        for i in range(used[0], used[-1] + 1)
    ]


def _playlist_stats(
    playlist: Iterable[Mapping[str, Any]], now: datetime, top: int
) -> tuple[PlaylistSummary, list[MonthCount], list[NamedCount], list[NamedCount], list[int]]:
    contributors: Counter[str] = Counter()
    artists: Counter[str] = Counter()
    months: Counter[str] = Counter()
    durations: list[int] = []
    track_count = 0

    for item in playlist:
        track_count += 1
        added_by = item.get("added_by")
        if added_by:
            contributors[str(added_by)] += 1
        for artist in {a for a in item.get("artists") or [] if isinstance(a, str) and a}:
            artists[artist] += 1
        duration = item.get("duration_ms")
        if isinstance(duration, int | float) and not isinstance(duration, bool) and duration >= 0:
            durations.append(int(duration))
        added_at = parse_timestamp(item.get("added_at"))
        if added_at is not None:
            months[_month_key(added_at)] += 1

    additions: list[MonthCount] = []
    if months:
        last = max(max(months), _month_key(now))
        additions = [MonthCount(m, months[m]) for m in _months_between(min(months), last)]

    total = sum(durations)
    summary = PlaylistSummary(
        track_count=track_count,
        total_duration_ms=total,
        avg_duration_ms=total // len(durations) if durations else 0,
        artist_count=len(artists),
        contributor_count=len(contributors),
    )
    return summary, additions, _ranked(contributors, top), _ranked(artists, top), durations


def _vote_stats(
    vote_docs: list[Mapping[str, Any]], top_voters: int
) -> tuple[VoteSummary, list[VoterTally], list[MonthParticipation]]:
    kept = removed = 0
    per_voter: dict[str, Counter[str]] = {}
    participation: list[int] = []
    month_polls: dict[str, list[int]] = {}

    for doc in vote_docs:
        state = doc.get("state")
        if state not in (STATE_KEPT, STATE_REMOVED):
            continue
        if state == STATE_KEPT:
            kept += 1
        else:
            removed += 1
        votes = doc.get("votes")
        if not isinstance(votes, Mapping):
            continue  # imported poll without details
        voters = [user for user, choice in votes.items() if choice in VOTE_CHOICES]
        participation.append(len(voters))
        for user in voters:
            per_voter.setdefault(str(user), Counter())[votes[user]] += 1
        month = _poll_month(doc.get("date"))
        if month:
            month_polls.setdefault(month, []).append(len(voters))

    closed = kept + removed
    summary = VoteSummary(
        polls_closed=closed,
        kept=kept,
        removed=removed,
        removal_rate=round(removed / closed, 3) if closed else 0.0,
        avg_participation=(
            round(sum(participation) / len(participation), 1) if participation else 0.0
        ),
        max_participation=max(participation, default=0),
    )

    ranked_voters = sorted(per_voter.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    voters = [
        VoterTally(user, tally["conserver"], tally["menfou"], tally["supprimer"])
        for user, tally in ranked_voters[:top_voters]
    ]

    by_month: list[MonthParticipation] = []
    if month_polls:
        for month in _months_between(min(month_polls), max(month_polls)):
            counts = month_polls.get(month, [])
            by_month.append(
                MonthParticipation(
                    month=month,
                    polls=len(counts),
                    average=round(sum(counts) / len(counts), 1) if counts else None,
                )
            )
    return summary, voters, by_month


def _current_poll(vote_docs: list[Mapping[str, Any]], track_id: str | None) -> CurrentPoll | None:
    if not track_id:
        return None
    doc = next((d for d in vote_docs if d.get("_id") == track_id), None)
    if doc is None or doc.get("state"):
        return None  # closed, and the next poll hasn't opened yet
    raw_votes = doc.get("votes")
    votes: Mapping[str, Any] = raw_votes if isinstance(raw_votes, Mapping) else {}
    tally = _tally(votes)
    return CurrentPoll(
        track_id=str(track_id),
        name=str(doc.get("name") or track_id),
        date=doc.get("date") if isinstance(doc.get("date"), str) else None,
        added_by=str(doc["added_by"]) if doc.get("added_by") else None,
        conserver=tally["conserver"],
        menfou=tally["menfou"],
        supprimer=tally["supprimer"],
        voters=sorted(str(user) for user, choice in votes.items() if choice in VOTE_CHOICES),
    )


def aggregate(
    playlist: Iterable[Mapping[str, Any]],
    votes: Iterable[Mapping[str, Any]],
    current_track_id: str | None = None,
    *,
    now: datetime | None = None,
    top: int = 10,
    top_voters: int = 20,
) -> PlaylistStats:
    """Compute the dashboard statistics for one guild.

    ``current_track_id`` is ``vote_infos.current.track_id``; its vote doc is
    reported as the open poll while it has no ``state``. ``now`` pins the end
    of the monthly additions series (tests), defaulting to the current time.
    """
    now = now or datetime.now(UTC)
    vote_docs = list(votes)
    summary, additions, contributors, artists, durations = _playlist_stats(playlist, now, top)
    vote_summary, voters, participation = _vote_stats(vote_docs, top_voters)
    return PlaylistStats(
        summary=summary,
        votes=vote_summary,
        additions_by_month=additions,
        top_contributors=contributors,
        top_artists=artists,
        duration_buckets=_duration_buckets(durations),
        voters=voters,
        participation_by_month=participation,
        current_poll=_current_poll(vote_docs, current_track_id),
    )
