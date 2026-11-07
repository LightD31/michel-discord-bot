"""A backup restores to exactly what was backed up, BSON types included."""

import asyncio
import gzip
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import Binary, Decimal128, Int64, ObjectId

import src.core.config as core_config
import src.core.db as db_module
from scripts.restore_backup import plan, restore
from src.core import backup_format
from src.core.db import MongoManager

# Documents as motor hands them over: dates are naive UTC and generic binary
# data is ``bytes`` (pymongo's defaults), which is also what a restore yields.
DOCS = {
    "global": {
        "olympics": [{"_id": 1, "medals": Int64(2**40)}],
    },
    "guild_1": {
        "xp": [
            {
                "_id": ObjectId("65f000000000000000000001"),
                "user_id": "42",
                "xp": 1500,
                "last_seen": datetime(2026, 11, 7, 4, 0),
                "ratio": 0.25,
                "price": Decimal128("12.34"),
                "blob": b"\x00\x01",
                "uuid": Binary(b"0123456789abcdef", 4),
                "nested": {"tags": ["a", "b"], "none": None},
            },
            {"_id": ObjectId("65f000000000000000000002"), "user_id": "43", "xp": 0},
            {"_id": ObjectId("65f000000000000000000003"), "user_id": "44", "xp": 7},
        ],
    },
}


class AsyncCursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        async def gen():
            for doc in self.docs:
                yield dict(doc)

        return gen()


class AsyncDb:
    def __init__(self, cols):
        self.cols = cols

    async def list_collection_names(self):
        return list(self.cols)

    def __getitem__(self, name):
        return SimpleNamespace(find=lambda: AsyncCursor(self.cols[name]))


class AsyncMotor:
    """Just enough of motor for ``backup_all``."""

    def __init__(self, dbs):
        self.dbs = dbs

    async def list_database_names(self):
        return list(self.dbs)

    def __getitem__(self, db_name):
        return AsyncDb(self.dbs[db_name])


class SyncCollection:
    """Just enough of pymongo for ``restore``."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserts = 0

    def estimated_document_count(self):
        return len(self.docs)

    def drop(self):
        self.docs = []

    def insert_many(self, docs):
        self.inserts += 1
        self.docs.extend(docs)


class SyncClient:
    def __init__(self, existing=None):
        self.cols: dict[tuple[str, str], SyncCollection] = {}
        for (db, col), docs in (existing or {}).items():
            self.cols[(db, col)] = SyncCollection(docs)

    def __getitem__(self, db_name):
        client = self

        class Db:
            def __getitem__(self, col_name):
                return client.cols.setdefault((db_name, col_name), SyncCollection())

        return Db()


@pytest.fixture
def backup(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(db_module, "BACKUP_BATCH_SIZE", 2)  # exercise several batches
    monkeypatch.setattr(MongoManager, "client", property(lambda self: AsyncMotor(DOCS)))
    return Path(asyncio.run(MongoManager().backup_all(str(tmp_path / "b"))))


def test_files_are_compressed_extended_json_lines(backup):
    path = backup_format.collection_path(backup, "guild_1", "xp")
    lines = gzip.decompress(path.read_bytes()).decode().splitlines()
    assert len(lines) == 3
    assert '"$oid": "65f000000000000000000001"' in lines[0]
    assert '"$date"' in lines[0] and '"$numberDecimal": "12.34"' in lines[0]


def test_round_trip_keeps_every_document_and_type(backup):
    client = SyncClient()
    todo = plan(backup, None)
    assert todo == {"global": {"olympics": 1}, "guild_1": {"xp": 3}}
    restored = restore(backup, client, todo, drop=False)
    assert restored == todo
    assert client.cols[("guild_1", "xp")].docs == DOCS["guild_1"]["xp"]
    medals = client.cols[("global", "olympics")].docs[0]["medals"]
    assert medals == 2**40 and isinstance(medals, Int64)


def test_a_non_empty_target_aborts_before_writing_anything(backup):
    client = SyncClient(existing={("guild_1", "xp"): [{"_id": "old"}]})
    with pytest.raises(ValueError, match="guild_1.xp"):
        restore(backup, client, plan(backup, None), drop=False)
    assert ("global", "olympics") not in client.cols or not client.cols[("global", "olympics")].docs
    assert client.cols[("guild_1", "xp")].docs == [{"_id": "old"}]


def test_drop_replaces_existing_documents(backup):
    client = SyncClient(existing={("guild_1", "xp"): [{"_id": "old"}]})
    restore(backup, client, plan(backup, ["guild_1"]), drop=True)
    assert [d["user_id"] for d in client.cols[("guild_1", "xp")].docs] == ["42", "43", "44"]
    assert ("global", "olympics") not in client.cols  # only the requested database


def test_plan_rejects_unknown_databases_and_non_backups(backup, tmp_path):
    with pytest.raises(ValueError, match="guild_9"):
        plan(backup, ["guild_9"])
    with pytest.raises(ValueError, match="manifest"):
        plan(tmp_path, None)
