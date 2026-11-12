"""On-disk format of a database backup, shared by the writer and the restore script.

A backup is a folder named after its timestamp::

    <backup_dir>/<YYYY-mm-dd_HH-MM-SS>/
        manifest.json                 # format version + document count per collection
        config.json                   # copy of config/config.json (owner-only)
        <database>/<collection>.jsonl.gz

Each collection is gzip-compressed JSON Lines: one document per line in
MongoDB Canonical Extended JSON (``bson.json_util``). Unlike plain JSON, it
keeps every BSON type as it was (ObjectId, dates, 64-bit integers, binary…),
so a restore writes back exactly what was read.

Writing and reading are blocking file I/O: callers on the event loop run them
through ``asyncio.to_thread``, a batch at a time.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from bson import json_util
from bson.json_util import CANONICAL_JSON_OPTIONS

from src.core.clock import LOCAL_TZ

FORMAT_VERSION = 1
# A backup folder's name, and its manifest's ``created_at``: local time.
BACKUP_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
MANIFEST_FILENAME = "manifest.json"
COLLECTION_SUFFIX = ".jsonl.gz"


def collection_path(backup: Path, db_name: str, col_name: str) -> Path:
    return backup / db_name / f"{col_name}{COLLECTION_SUFFIX}"


class CollectionWriter:
    """Appends documents to one collection file; use as a context manager."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.count = 0
        self._file: IO[str] = gzip.open(path, "wt", encoding="utf-8")  # noqa: SIM115 — closed in close()

    def write(self, docs: Iterable[Mapping[str, Any]]) -> None:
        for doc in docs:
            self._file.write(json_util.dumps(doc, json_options=CANONICAL_JSON_OPTIONS))
            self._file.write("\n")
            self.count += 1

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> CollectionWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_collection(path: Path) -> Iterator[dict[str, Any]]:
    """The documents stored in one collection file, with their BSON types restored."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                doc: dict[str, Any] = json_util.loads(line)
                yield doc


def write_manifest(backup: Path, created_at: str, counts: Mapping[str, Mapping[str, int]]) -> None:
    """``counts`` maps database → collection → number of documents written."""
    manifest = {"format": FORMAT_VERSION, "created_at": created_at, "databases": counts}
    (backup / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def read_manifest(backup: Path) -> dict[str, Any]:
    """The backup's manifest; ``ValueError`` when it is missing or not this format."""
    path = backup / MANIFEST_FILENAME
    if not path.is_file():
        raise ValueError(f"{backup} n'est pas une sauvegarde : {MANIFEST_FILENAME} manquant")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT_VERSION:
        raise ValueError(f"Format de sauvegarde inconnu : {manifest.get('format')!r}")
    return manifest


def latest_backup_time(root: Path) -> float | None:
    """When the newest complete backup under *root* was taken (epoch seconds), if any.

    A folder counts once its manifest is written, the last step of a backup,
    so one interrupted mid-way is skipped. ``created_at`` is the folder's
    timestamp (:data:`BACKUP_TIMESTAMP_FORMAT`) on the community's wall clock.
    """
    if not root.is_dir():
        return None
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        try:
            created_at = read_manifest(folder)["created_at"]
            stamp = datetime.strptime(created_at, BACKUP_TIMESTAMP_FORMAT)
            return stamp.replace(tzinfo=LOCAL_TZ).timestamp()
        except (ValueError, KeyError, OSError):
            continue
    return None


__all__ = [
    "BACKUP_TIMESTAMP_FORMAT",
    "COLLECTION_SUFFIX",
    "FORMAT_VERSION",
    "MANIFEST_FILENAME",
    "CollectionWriter",
    "collection_path",
    "latest_backup_time",
    "read_collection",
    "read_manifest",
    "write_manifest",
]
