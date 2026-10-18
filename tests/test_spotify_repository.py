"""``SpotifyRepository`` playlist-mirror and add-with-vote writes against an in-memory collection."""

import copy

import pytest

import features.spotify.repository as repository
from features.spotify.repository import (
    ADDWITHVOTES_COLLECTION,
    PLAYLIST_COLLECTION,
    SpotifyRepository,
)

GUILD = "123"


class _UpdateResult:
    def __init__(self, upserted_id=None):
        self.upserted_id = upserted_id


class _FakeCollection:
    def __init__(self):
        self.docs: dict = {}

    async def find_one(self, query):
        return copy.deepcopy(self.docs.get(query["_id"]))

    async def update_one(self, query, update, upsert=False):
        doc_id = query["_id"]
        if doc_id in self.docs:
            self.docs[doc_id].update(update.get("$set", {}))
            return _UpdateResult()
        if not upsert:
            return _UpdateResult()
        doc = {"_id": doc_id, **update.get("$setOnInsert", {}), **update.get("$set", {})}
        self.docs[doc_id] = copy.deepcopy(doc)
        return _UpdateResult(doc_id)

    async def delete_one(self, query):
        self.docs.pop(query["_id"], None)

    def find(self):
        return _Cursor(list(self.docs.values()))


class _Cursor:
    def __init__(self, docs):
        self._iter = iter(copy.deepcopy(docs))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture
def cols(monkeypatch):
    collections: dict[str, _FakeCollection] = {}

    class _Manager:
        def get_guild_collection(self, guild_id, name):
            assert guild_id == GUILD
            return collections.setdefault(name, _FakeCollection())

    monkeypatch.setattr(repository, "mongo_manager", _Manager())
    return collections


def _song(track_id="t1", added_by="alice"):
    return {"_id": track_id, "name": "Song", "added_by": added_by}


async def test_add_playlist_item_inserts_a_new_track(cols):
    repo = SpotifyRepository(GUILD)

    assert await repo.add_playlist_item(_song()) is True
    assert cols[PLAYLIST_COLLECTION].docs["t1"] == _song()


async def test_add_playlist_item_keeps_the_existing_track(cols):
    repo = SpotifyRepository(GUILD)
    await repo.add_playlist_item(_song(added_by="alice"))

    # A second add (e.g. a won poll for a track /addsong already added) is a
    # no-op instead of a duplicate-key error, and doesn't rewrite who added it.
    assert await repo.add_playlist_item(_song(added_by="bob")) is False
    assert cols[PLAYLIST_COLLECTION].docs["t1"]["added_by"] == "alice"


async def test_closed_poll_is_gone_after_delete(cols):
    repo = SpotifyRepository(GUILD)
    await repo.save_addwithvote_data(
        {
            "t1": {"deadline": 1.0, "votes": {"1": "yes"}},
            "t2": {"deadline": 2.0, "votes": {}},
        }
    )

    await repo.delete_addwithvote("t1")

    assert await repo.get_addwithvote_doc("t1") is None
    assert list(await repo.load_addwithvote_data()) == ["t2"]
    assert set(cols[ADDWITHVOTES_COLLECTION].docs) == {"t2"}
