# Architecture

The three layers, and where each kind of code goes. The one-line rules of `CLAUDE.md` point here.

## Architecture

Three-layer split. Respect the boundaries (`tests/test_layering.py` checks the imports by full module name: `features/` imports neither `interactions` nor `src.discord_ext` nor `src.webui`, `src/core` imports no other part of `src`, and `extensions/` never import `pymongo` / `bson`):

- **`extensions/`** — Discord-facing layer. Each is either `extensions/<name>.py` or a package `extensions/<name>/__init__.py` containing an `interactions.Extension` subclass plus a module-level `setup(bot)` factory. Auto-discovered by `main.py` at startup. The class lists the per-guild config modules it reads in `MODULE_KEYS` (a tuple, `()` for none) — see *Web UI schema* below.

- **`features/`** — Pure domain logic and persistence. **Must not import `interactions`** — this is what makes features unit-testable. Domain code returns plain data (bytes, dicts, models) and lets the extension build Discord objects. Each feature owns a `repository.py` that reads/writes MongoDB and translates driver exceptions into `src.core.errors.DatabaseError` so extensions never import `pymongo`: every method carries `@translates_db_errors` from `src.core.db` (see `features/xp/repository.py`). A repository of one guild's data derives from `GuildRepository` (`src/core/db.py`, P32): it names its main collection in `COLLECTION`, reaches the others with `self._col("name")`, and lists its indexes in `indexes()`; `ensure_indexes()` makes them at startup and logs a failure rather than raise it, so an extension's `on_startup` just awaits it. `bson` counts as the driver too: a repository method taking an id a user typed accepts a `str` and parses the `ObjectId` itself, so a malformed id is an ordinary "not found" rather than an `InvalidId` escaping into a command handler.

- **`src/`** — Shared infrastructure.
  - `src/core/` is framework-free (config, db, http, logging, errors, images, text) and the bottom layer: what another layer adds to it registers itself (the dashboard's log stream goes through `logging.add_shared_handler()`). Strictly typed under mypy. Pure formatters used by several layers live here (`text.format_discord_timestamp`).
  - `src/discord_ext/` holds interactions.py-dependent UI helpers (embeds, paginator, autocomplete, persistent messages, the staff check shared by moderation and suggestions in `staff.py`). Renderers shared between an extension and a WebUI route also live here (e.g. `rolemenus.py`, `embedmanager.py`): a WebUI route never imports an extension module.
  - `src/integrations/` holds external-API clients with **no Discord imports** (Spotify, Notion, LLDAP, Shlink…).
  - `src/webui/` is the optional FastAPI dashboard (run in a daemon thread alongside the bot).

## Per-guild data isolation

Each Discord server gets its own MongoDB database named `guild_{guild_id}`; cross-guild data lives in a shared `global` database. Access via the singleton in `src/core/db.py` (`get_guild_collection`, `get_global_collection`). The driver is PyMongo's native async API (`AsyncMongoClient`; Motor is deprecated and gone). A client belongs to one event loop, so one is created per loop — the bot loop and the Web UI's uvicorn loop each get their own — and a collection is never cached across loops. Unlike Motor, `aggregate()` is a coroutine: `await (await col.aggregate(pipeline)).to_list()`. Repositories under `features/<name>/repository.py` encapsulate this.

## Notes

- `interactions.py` here is the `discord-py-interactions` package — not `discord.py`. Use `interactions.Extension`, `@slash_command`, `@listen()`, `@interactions.Task.create(IntervalTrigger(...))`.
