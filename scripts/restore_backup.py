"""Restore a backup written by ``/backup`` or the daily backup task.

Stop the bot first, so nothing writes to the databases while they are being
restored. With Docker Compose::

    docker compose stop discord_bot
    docker compose run --rm discord_bot python scripts/restore_backup.py \\
        data/backups/2026-11-07_04-00-00 --dry-run
    docker compose run --rm discord_bot python scripts/restore_backup.py \\
        data/backups/2026-11-07_04-00-00 --drop
    docker compose start discord_bot

Options:

- ``--db NAME`` (repeatable): restore only these databases (``global``,
  ``guild_<id>``). Default: every database in the backup.
- ``--drop``: replace collections that already hold documents. Without it the
  restore refuses to touch a non-empty collection, and writes nothing at all.
- ``--dry-run``: list what would be restored and stop.
- ``--mongo-url URL``: target server. Default: ``mongodb.url`` from
  ``config/config.json``.

``config.json`` in the backup folder is the configuration as it was; it is not
restored automatically. Copy it over ``config/config.json`` by hand if needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core import backup_format  # noqa: E402

BATCH_SIZE = 1000


def plan(backup: Path, databases: list[str] | None) -> dict[str, dict[str, int]]:
    """Database → collection → documents to restore, from the backup's manifest."""
    manifest = backup_format.read_manifest(backup)
    available: dict[str, dict[str, int]] = manifest["databases"]
    if not databases:
        return available
    missing = [name for name in databases if name not in available]
    if missing:
        raise ValueError(f"Absent de la sauvegarde : {', '.join(missing)}")
    return {name: available[name] for name in databases}


def restore(
    backup: Path, client: Any, todo: dict[str, dict[str, int]], *, drop: bool
) -> dict[str, dict[str, int]]:
    """Write every collection of *todo* back; return what was inserted.

    Checks every target first: without *drop*, a single non-empty collection
    aborts the restore before anything is written.
    """
    if not drop:
        busy = [
            f"{db}.{col}"
            for db, cols in todo.items()
            for col in cols
            if client[db][col].estimated_document_count() > 0
        ]
        if busy:
            raise ValueError(
                "Collections non vides (relance avec --drop pour les remplacer) : "
                + ", ".join(busy)
            )

    restored: dict[str, dict[str, int]] = {}
    for db_name, cols in todo.items():
        restored[db_name] = {}
        for col_name in cols:
            target = client[db_name][col_name]
            if drop:
                target.drop()
            inserted, batch = 0, []
            path = backup_format.collection_path(backup, db_name, col_name)
            for doc in backup_format.read_collection(path):
                batch.append(doc)
                if len(batch) >= BATCH_SIZE:
                    target.insert_many(batch)
                    inserted, batch = inserted + len(batch), []
            if batch:
                target.insert_many(batch)
                inserted += len(batch)
            restored[db_name][col_name] = inserted
    return restored


def _mongo_url(args: argparse.Namespace) -> str:
    if args.mongo_url:
        return str(args.mongo_url)
    config = json.loads(Path("config/config.json").read_text(encoding="utf-8"))
    url = config.get("config", {}).get("mongodb", {}).get("url")
    if not url:
        raise SystemExit("Pas d'URL MongoDB : passe --mongo-url ou renseigne mongodb.url")
    return str(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restaure une sauvegarde MongoDB du bot.")
    parser.add_argument("backup", type=Path, help="dossier de la sauvegarde")
    parser.add_argument("--db", action="append", dest="databases", help="base à restaurer")
    parser.add_argument("--drop", action="store_true", help="remplacer les collections non vides")
    parser.add_argument("--dry-run", action="store_true", help="afficher sans rien écrire")
    parser.add_argument("--mongo-url", help="URL MongoDB (défaut : config/config.json)")
    args = parser.parse_args()

    try:
        todo = plan(args.backup, args.databases)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    for db_name, cols in sorted(todo.items()):
        print(f"{db_name}: {len(cols)} collections, {sum(cols.values())} documents")
    if args.dry_run:
        return

    import pymongo

    client: Any = pymongo.MongoClient(_mongo_url(args))
    try:
        restored = restore(args.backup, client, todo, drop=args.drop)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    finally:
        client.close()

    mismatches = [
        f"{db}.{col}: {restored[db][col]} restaurés, {expected} attendus"
        for db, cols in todo.items()
        for col, expected in cols.items()
        if restored[db][col] != expected
    ]
    for line in mismatches:
        print(line, file=sys.stderr)
    if mismatches:
        raise SystemExit("Restauration incomplète")
    print("Restauration terminée. config.json de la sauvegarde n'a pas été restauré.")


if __name__ == "__main__":
    main()
