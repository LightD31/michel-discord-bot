"""A real backup restores to exactly what was in MongoDB (T05).

``test_backup_restore.py`` checks the file format against a fake client; here
``backup_all`` reads a real server and ``scripts/restore_backup.py`` writes it
back, as the runbook has the operator do (``--drop``), so the procedure in
``docs/RUNBOOK.md`` is the one tested.
"""

import os
from datetime import datetime
from pathlib import Path

import pymongo
import pytest
from bson import Binary, Decimal128, Int64, ObjectId, Regex, Timestamp
from bson.codec_options import CodecOptions

from scripts import restore_backup
from src.core import backup_format, db

pytestmark = pytest.mark.mongo

MONGO_TEST_URL = os.environ.get("MONGO_TEST_URL", "")

# Every BSON type the bot stores, or could: what comes back must be the same type.
DOCUMENT = {
    "_id": ObjectId("65f000000000000000000001"),
    "int32": 7,
    "int64": Int64(2**40),
    "double": 0.25,
    "decimal": Decimal128("12.34"),
    "string": "héllo ⭐",
    "bool": True,
    "none": None,
    "date": datetime(2026, 11, 7, 4, 0, 0, 123000),
    "bytes": b"\x00\x01",
    "uuid": Binary(b"0123456789abcdef", 4),
    "regex": Regex("^a.*", "i"),
    "timestamp": Timestamp(1700000000, 1),
    "nested": {"tags": ["a", "b"], "deep": [{"x": Int64(1)}]},
}


def types_of(value):
    if isinstance(value, dict):
        return {k: types_of(v) for k, v in value.items()}
    if isinstance(value, list):
        return [types_of(v) for v in value]
    return type(value)


def read_back(url: str, database: str) -> dict[str, list[dict]]:
    client = pymongo.MongoClient(url)
    try:
        dbase = client.get_database(database, codec_options=CodecOptions())
        return {
            name: list(dbase[name].find().sort("_id"))
            for name in sorted(dbase.list_collection_names())
        }
    finally:
        client.close()


async def test_backup_all_then_restore_with_drop_gives_back_every_document(mongo, tmp_path):
    guild_db = f"{db.GUILD_DB_PREFIX}{mongo.guild_id}"
    await mongo.guild_collection("xp").insert_many(
        [DOCUMENT, {"_id": ObjectId("65f000000000000000000002"), "user_id": "43", "xp": 0}]
    )
    await db.mongo_manager.get_global_collection("olympics").insert_one({"_id": 1, "medals": 3})
    await db.mongo_manager.get_global_collection("webui_sessions").insert_one({"_id": "secret"})

    before = {name: read_back(MONGO_TEST_URL, name) for name in (guild_db, db.GLOBAL_DB_NAME)}
    folder = Path(await db.mongo_manager.backup_all(str(tmp_path / "backups"), max_backups=2))
    manifest = backup_format.read_manifest(folder)["databases"]
    assert manifest[guild_db] == {"xp": 2}
    assert "webui_sessions" not in manifest[db.GLOBAL_DB_NAME]

    # Lose the data, then follow the runbook.
    await mongo.guild_collection("xp").delete_many({})
    await mongo.guild_collection("xp").insert_one({"_id": "written after the backup"})
    await db.mongo_manager.client.drop_database(db.GLOBAL_DB_NAME)
    args = ["--mongo-url", MONGO_TEST_URL, "--drop"]
    restore_backup.main([str(folder), "--db", guild_db, "--db", db.GLOBAL_DB_NAME, *args])

    after = {name: read_back(MONGO_TEST_URL, name) for name in (guild_db, db.GLOBAL_DB_NAME)}
    before[db.GLOBAL_DB_NAME].pop("webui_sessions")  # never backed up
    assert after == before
    assert types_of(after[guild_db]["xp"]) == types_of(before[guild_db]["xp"])
    assert types_of(after[guild_db]["xp"][0]) == types_of(DOCUMENT)


async def test_a_restore_without_drop_refuses_to_overwrite(mongo, tmp_path):
    guild_db = f"{db.GUILD_DB_PREFIX}{mongo.guild_id}"
    await mongo.guild_collection("xp").insert_one({"_id": 1, "xp": 5})
    folder = await db.mongo_manager.backup_all(str(tmp_path / "backups"), max_backups=2)
    await mongo.guild_collection("xp").update_one({"_id": 1}, {"$set": {"xp": 9}})

    with pytest.raises(SystemExit, match="--drop"):
        restore_backup.main([folder, "--db", guild_db, "--mongo-url", MONGO_TEST_URL])
    assert read_back(MONGO_TEST_URL, guild_db)["xp"] == [{"_id": 1, "xp": 9}]
