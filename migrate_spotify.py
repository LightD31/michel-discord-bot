"""
Migration script for the Spotify module.

Migrates data from the old single-server format to the new per-server format:
- MongoDB: Playlist -> Playlist_{guild_id}
- JSON files: {name}.json -> {name}_{guild_id}.json

Usage:
    python migrate_spotify.py [--dry-run]

The script auto-detects the primary guild ID from config.
Use --dry-run to preview what would be migrated without making changes.
"""

import json
import os
import shutil
import sys

import pymongo

from src.utils import load_config

# Load config
CONFIG, MODULE_CONFIGS, ENABLED_SERVERS = load_config("moduleSpotify")
MONGODB_URL = CONFIG["mongodb"]["url"]
DATA_FOLDER = CONFIG["misc"]["dataFolder"]

DRY_RUN = "--dry-run" in sys.argv

# The first enabled server is the one that had the old data
PRIMARY_GUILD_ID = str(ENABLED_SERVERS[0])

print(f"Migration script for Spotify module")
print(f"Primary guild ID (old data): {PRIMARY_GUILD_ID}")
print(f"Enabled servers: {ENABLED_SERVERS}")
print(f"Data folder: {DATA_FOLDER}")
print(f"Dry run: {DRY_RUN}")
print()


def migrate_mongodb():
    """Copy documents from Playlist.{collection} to Playlist_{guild_id}.{collection}."""
    client = pymongo.MongoClient(MONGODB_URL)

    old_db = client["Playlist"]
    new_db = client[f"Playlist_{PRIMARY_GUILD_ID}"]

    for collection_name in ["playlistItemsFull", "votes"]:
        old_col = old_db[collection_name]
        new_col = new_db[collection_name]

        count = old_col.count_documents({})
        existing = new_col.count_documents({})

        print(f"[MongoDB] {collection_name}: {count} documents in old DB, {existing} already in new DB")

        if existing > 0:
            print(f"  -> SKIPPED: new collection already has data. Delete Playlist_{PRIMARY_GUILD_ID}.{collection_name} first if you want to re-migrate.")
            continue

        if count == 0:
            print(f"  -> Nothing to migrate.")
            continue

        if DRY_RUN:
            print(f"  -> Would copy {count} documents to Playlist_{PRIMARY_GUILD_ID}.{collection_name}")
        else:
            docs = list(old_col.find())
            new_col.insert_many(docs)
            print(f"  -> Copied {count} documents to Playlist_{PRIMARY_GUILD_ID}.{collection_name}")

    client.close()


def migrate_json_file(old_name: str, new_name: str):
    """Copy a JSON file from old name to new per-server name."""
    old_path = os.path.join(DATA_FOLDER, old_name)
    new_path = os.path.join(DATA_FOLDER, new_name)

    if not os.path.exists(old_path):
        print(f"[JSON] {old_name}: not found, skipping.")
        return

    if os.path.exists(new_path):
        print(f"[JSON] {new_name}: already exists, SKIPPED. Delete it first if you want to re-migrate.")
        return

    # Validate it's valid JSON
    try:
        with open(old_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[JSON] {old_name}: invalid JSON ({e}), skipping.")
        return

    entries = len(data) if isinstance(data, (dict, list)) else "N/A"
    
    if DRY_RUN:
        print(f"[JSON] Would copy {old_name} -> {new_name} ({entries} entries)")
    else:
        shutil.copy2(old_path, new_path)
        print(f"[JSON] Copied {old_name} -> {new_name} ({entries} entries)")


def migrate_json_files():
    """Migrate all Spotify JSON files to per-server format."""
    files_to_migrate = [
        ("addwithvotes.json", f"addwithvotes_{PRIMARY_GUILD_ID}.json"),
        ("voteinfos.json", f"voteinfos_{PRIMARY_GUILD_ID}.json"),
        ("snapshot.json", f"snapshot_{PRIMARY_GUILD_ID}.json"),
        ("reminderspotify.json", f"reminderspotify_{PRIMARY_GUILD_ID}.json"),
    ]

    for old_name, new_name in files_to_migrate:
        migrate_json_file(old_name, new_name)


if __name__ == "__main__":
    print("=" * 50)
    print("Migrating MongoDB collections...")
    print("=" * 50)
    migrate_mongodb()
    print()
    print("=" * 50)
    print("Migrating JSON files...")
    print("=" * 50)
    migrate_json_files()
    print()

    if DRY_RUN:
        print("Dry run complete. No changes were made.")
        print("Run without --dry-run to apply the migration.")
    else:
        print("Migration complete!")
        print("The old data (Playlist DB and original JSON files) has NOT been deleted.")
        print("You can remove them manually once you've verified everything works.")
