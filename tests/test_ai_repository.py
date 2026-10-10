"""``AiComparisonRepository`` against a small in-memory stand-in for a motor collection."""

import copy
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

import features.ai.repository as repository
from features.ai import AiComparisonRepository, Comparison, ComparisonAnswer

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def _matches(doc, query):
    for key, cond in query.items():
        value = doc
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$lte" and not (value is not None and value <= arg):
                    return False
                if op == "$gt" and not (value is not None and value != arg and len(value) > 0):
                    return False
                if op == "$in" and not (set(value or []) & set(arg)):
                    return False
        elif value != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, n):
        return _Cursor(self._docs[:n])

    def sort(self, key, direction):
        return _Cursor(sorted(self._docs, key=lambda d: d[key], reverse=direction < 0))

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return copy.deepcopy(next(self._iter))
        except StopIteration:
            raise StopAsyncIteration from None


class _Result:
    def __init__(self, inserted_id=None):
        self.inserted_id = inserted_id


class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    async def create_index(self, *args, **kwargs):
        return "idx"

    async def insert_one(self, doc):
        doc = copy.deepcopy(doc)
        doc["_id"] = ObjectId()
        self.docs.append(doc)
        return _Result(doc["_id"])

    async def find_one(self, query):
        found = next((d for d in self.docs if _matches(d, query)), None)
        return copy.deepcopy(found)

    def find(self, query, projection=None):
        return _Cursor([d for d in self.docs if _matches(d, query)])

    def _apply(self, doc, update):
        for path, value in update["$set"].items():
            target = doc
            *parents, leaf = path.split(".")
            for part in parents:
                target = target.setdefault(part, {})
            target[leaf] = value

    async def update_one(self, query, update):
        doc = next((d for d in self.docs if _matches(d, query)), None)
        if doc:
            self._apply(doc, update)

    async def find_one_and_update(self, query, update, return_document=None):
        doc = next((d for d in self.docs if _matches(d, query)), None)
        if doc is None:
            return None
        self._apply(doc, update)
        return copy.deepcopy(doc)


@pytest.fixture
def col(monkeypatch):
    collections: dict[tuple, _FakeCollection] = {}

    class _Manager:
        def get_guild_collection(self, guild_id, name):
            return collections.setdefault((guild_id, name), _FakeCollection())

        def get_global_collection(self, name):
            return collections.setdefault(("global", name), _FakeCollection())

    monkeypatch.setattr(repository, "mongo_manager", _Manager())
    return collections


def _comparison(closes_in=10):
    return Comparison(
        question="q",
        asker_id="1",
        channel_id="2",
        answers=[
            ComparisonAnswer("a", "openrouter", "a/a", "A", "réponse A"),
            ComparisonAnswer("b", "nanogpt", "b/b", "B", "réponse B"),
        ],
        closes_at=NOW + timedelta(minutes=closes_in),
        created_at=NOW,
    )


async def test_create_get_and_message_ids(col):
    repo = AiComparisonRepository("123")
    await repo.ensure_indexes()
    comparison_id = await repo.create(_comparison())
    await repo.set_message_ids(comparison_id, ["10", "11"])

    stored = await repo.get(comparison_id)
    assert stored.id == comparison_id
    assert [a.api for a in stored.answers] == ["openrouter", "nanogpt"]
    assert stored.message_ids == ["10", "11"]
    assert await repo.known_message_ids(["11", "12"]) == {"11"}
    assert await repo.known_message_ids([]) == set()
    assert ("123", "ai_comparisons") in col


async def test_bad_ids_are_simply_not_found(col):
    repo = AiComparisonRepository(None)
    assert await repo.get("not-an-object-id") is None
    assert await repo.set_vote("nope", "1", "a") is None
    assert await repo.close("nope") is None
    assert not col  # a malformed id never reaches the database

    await repo.create(_comparison())
    assert ("global", "ai_comparisons") in col  # no guild → shared global database


async def test_votes_change_and_stop_once_closed(col):
    repo = AiComparisonRepository("123")
    comparison_id = await repo.create(_comparison())
    assert (await repo.set_vote(comparison_id, "u1", "a")).votes == {"u1": "a"}
    updated = await repo.set_vote(comparison_id, "u1", "b")
    assert updated.votes == {"u1": "b"}

    closed = await repo.close(comparison_id)
    assert closed.closed
    assert await repo.close(comparison_id) is None  # only one closer wins
    assert await repo.set_vote(comparison_id, "u2", "a") is None


async def test_due_for_close_and_stats_listing(col):
    repo = AiComparisonRepository("123")
    due_id = await repo.create(_comparison(closes_in=-1))
    await repo.create(_comparison(closes_in=5))
    assert [c.id for c in await repo.due_for_close(NOW)] == [due_id]

    await repo.set_vote(due_id, "u1", "a")
    assert [c.id for c in await repo.list_for_stats()] == [due_id]
