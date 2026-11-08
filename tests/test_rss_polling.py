"""The RSS poll cycle: due feeds only, one fetch per URL, bounded concurrency, backoff.

The repository is faked here; its MongoDB updates are covered by
tests/test_mongo_rss.py.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import extensions.rss as rss_ext
from extensions.rss import RssExtension
from features.rss.models import RssEntry, RssFeedState
from features.rss.scheduling import BACKOFF_CAP, is_due, next_poll_at, poll_delay
from src.core.errors import DatabaseError, IntegrationError

# ── Scheduling ───────────────────────────────────────────────────────────

MIN15 = timedelta(minutes=15)


def test_a_healthy_feed_waits_its_interval():
    assert poll_delay(MIN15, 0) == MIN15


def test_each_failure_doubles_the_wait_up_to_the_cap():
    assert [poll_delay(MIN15, n) for n in (1, 2, 3)] == [MIN15 * 2, MIN15 * 4, MIN15 * 8]
    assert poll_delay(MIN15, 50) == BACKOFF_CAP


def test_the_backoff_never_polls_more_often_than_configured():
    twelve_hours = timedelta(hours=12)
    assert poll_delay(twelve_hours, 3) == twelve_hours


def test_a_feed_never_polled_is_due():
    now = datetime(2026, 1, 1, 12)
    assert is_due(None, MIN15, now)
    assert is_due(RssFeedState(_id="f"), MIN15, now)
    assert next_poll_at(None, MIN15) is None


def test_a_failing_feed_is_due_later():
    now = datetime(2026, 1, 1, 12)
    polled = now - timedelta(minutes=20)
    healthy = RssFeedState(_id="f", last_poll_at=polled)
    failing = RssFeedState(_id="f", last_poll_at=polled, failures=2)

    assert is_due(healthy, MIN15, now)
    assert not is_due(failing, MIN15, now)
    assert next_poll_at(failing, MIN15) == polled + timedelta(hours=1)


# ── Poll cycle ───────────────────────────────────────────────────────────


class FakeRepo:
    """Every guild's feed states, plus a log of what the cycle recorded."""

    docs: dict[tuple[str, str], RssFeedState] = {}
    calls: list[tuple] = []
    failing_guilds: set[str] = set()

    def __init__(self, guild_id):
        self.guild_id = str(guild_id)

    async def states(self):
        if self.guild_id in FakeRepo.failing_guilds:
            raise DatabaseError("mongo down")
        return {fid: st for (gid, fid), st in FakeRepo.docs.items() if gid == self.guild_id}

    async def initialize(self, feed_id, seen_ids):
        FakeRepo.calls.append(("initialize", self.guild_id, feed_id, list(seen_ids)))

    async def record_poll(self, feed_id, new_ids):
        FakeRepo.calls.append(("poll", self.guild_id, feed_id, list(new_ids)))

    async def record_error(self, feed_id, message):
        FakeRepo.calls.append(("error", self.guild_id, feed_id, message))


class FakeModule:
    def __init__(self, guilds: dict[str, dict]) -> None:
        self.guilds = guilds

    def items(self):
        return list(self.guilds.items())


def entry(n: int) -> RssEntry:
    return RssEntry(entry_id=f"e{n}", title=f"Titre {n}", link=f"https://example.com/{n}")


class Network:
    """Stands in for fetch_feed: counts fetches and the peak of concurrent ones."""

    def __init__(self) -> None:
        self.feeds: dict[str, list[RssEntry] | Exception] = {}
        self.fetched: list[str] = []
        self.active = 0
        self.peak = 0

    async def __call__(self, url):
        self.fetched.append(url)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            result = self.feeds.get(url, [entry(1)])
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self.active -= 1


class Channel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


@pytest.fixture
def cycle(monkeypatch):
    FakeRepo.docs = {}
    FakeRepo.calls = []
    FakeRepo.failing_guilds = set()
    network = Network()
    channel = Channel()

    async def fetch_channel(cid):
        return channel

    async def no_shortening(e, feed_id):
        return e

    monkeypatch.setattr(rss_ext, "RssRepository", FakeRepo)
    monkeypatch.setattr(rss_ext, "fetch_feed", network)
    monkeypatch.setattr(rss_ext, "_with_short_link", no_shortening)
    ext = object.__new__(RssExtension)
    ext.bot = SimpleNamespace(fetch_channel=fetch_channel)
    ext._repos = {}
    ext._poll_lock = asyncio.Lock()

    def configure(guilds: dict[str, dict]) -> None:
        monkeypatch.setattr(rss_ext, "RSS", FakeModule(guilds))

    async def run() -> None:
        await RssExtension.check_feeds.callback(ext)

    return SimpleNamespace(ext=ext, network=network, channel=channel, configure=configure, run=run)


def seen(guild, feed, *ids, **fields):
    FakeRepo.docs[(guild, feed)] = RssFeedState(
        _id=feed, initialized=True, seen_ids=list(ids), **fields
    )


def guild(*feeds: tuple[str, str], minutes: int = 15) -> dict:
    return {
        "ChannelId": "10",
        "rssPollMinutes": minutes,
        "rssFeeds": {fid: {"url": url} for fid, url in feeds},
    }


async def test_a_url_followed_by_several_guilds_is_fetched_once(cycle):
    url = "https://example.com/feed"
    cycle.configure({"1": guild(("news", url)), "2": guild(("actus", url))})
    seen("1", "news", "e0")
    seen("2", "actus", "e0")

    await cycle.run()

    assert cycle.network.fetched == [url]
    assert sorted(FakeRepo.calls) == [
        ("poll", "1", "news", ["e1"]),
        ("poll", "2", "actus", ["e1"]),
    ]
    assert len(cycle.channel.sent) == 2


async def test_fetches_run_concurrently_within_the_bound(cycle, monkeypatch):
    monkeypatch.setattr(rss_ext, "MAX_CONCURRENT_FETCHES", 3)
    feeds = [(f"f{n}", f"https://example.com/{n}") for n in range(10)]
    cycle.configure({"1": guild(*feeds)})

    await cycle.run()

    assert len(cycle.network.fetched) == 10
    assert cycle.network.peak == 3


async def test_a_feed_polled_recently_is_skipped(cycle):
    cycle.configure({"1": guild(("news", "https://example.com/feed"))})
    seen("1", "news", "e1", last_poll_at=datetime.now() - timedelta(minutes=5))

    await cycle.run()

    assert cycle.network.fetched == []


async def test_a_failing_feed_backs_off(cycle):
    url = "https://example.com/down"
    cycle.configure({"1": guild(("news", url))})
    cycle.network.feeds[url] = IntegrationError("HTTP 503")
    # Polled 20 minutes ago, after two failures: next try at 1 h, not 15 min.
    seen("1", "news", "e0", last_poll_at=datetime.now() - timedelta(minutes=20), failures=2)

    await cycle.run()
    assert cycle.network.fetched == []

    seen("1", "news", "e0", last_poll_at=datetime.now() - timedelta(hours=2), failures=2)
    await cycle.run()
    assert cycle.network.fetched == [url]
    assert FakeRepo.calls == [("error", "1", "news", "HTTP 503")]


async def test_a_feed_with_nothing_new_still_records_its_poll(cycle):
    """Without the stamp, the interval was ignored and the feed fetched every 5 minutes."""
    cycle.configure({"1": guild(("news", "https://example.com/feed"))})
    seen("1", "news", "e1")

    await cycle.run()

    assert FakeRepo.calls == [("poll", "1", "news", [])]
    assert cycle.channel.sent == []


async def test_the_first_poll_seeds_the_feed_without_posting(cycle):
    url = "https://example.com/feed"
    cycle.configure({"1": guild(("news", url))})
    cycle.network.feeds[url] = [entry(2), entry(1)]

    await cycle.run()

    assert FakeRepo.calls == [("initialize", "1", "news", ["e2", "e1"])]
    assert cycle.channel.sent == []


async def test_new_entries_are_posted_oldest_first(cycle):
    url = "https://example.com/feed"
    cycle.configure({"1": guild(("news", url))})
    cycle.network.feeds[url] = [entry(3), entry(2), entry(1)]
    seen("1", "news", "e1")

    await cycle.run()

    assert [m.split("[")[1].split("]")[0] for m in cycle.channel.sent] == ["Titre 2", "Titre 3"]
    assert FakeRepo.calls == [("poll", "1", "news", ["e3", "e2"])]


async def test_an_empty_feed_counts_as_a_failure(cycle):
    url = "https://example.com/empty"
    cycle.configure({"1": guild(("news", url))})
    cycle.network.feeds[url] = []
    seen("1", "news", "e1")

    await cycle.run()

    assert FakeRepo.calls == [("error", "1", "news", "Feed returned no entries")]


async def test_an_unexpected_error_on_one_url_spares_the_others(cycle):
    broken, fine = "https://example.com/broken", "https://example.com/fine"
    cycle.configure({"1": guild(("a", broken), ("b", fine))})
    cycle.network.feeds[broken] = ValueError("parser bug")
    seen("1", "a", "e0")
    seen("1", "b", "e0")

    await cycle.run()

    assert sorted(c[0] for c in FakeRepo.calls) == ["error", "poll"]


async def test_a_guild_whose_state_cannot_be_read_is_skipped(cycle):
    cycle.configure(
        {"1": guild(("a", "https://example.com/1")), "2": guild(("b", "https://example.com/2"))}
    )
    FakeRepo.failing_guilds = {"1"}

    await cycle.run()

    assert cycle.network.fetched == ["https://example.com/2"]


async def test_a_cycle_still_running_makes_the_next_one_a_no_op(cycle):
    cycle.configure({"1": guild(("news", "https://example.com/feed"))})

    async with cycle.ext._poll_lock:
        await cycle.run()

    assert cycle.network.fetched == []
