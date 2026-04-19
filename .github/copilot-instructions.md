# Copilot Instructions — Michel Discord Bot

## Project Overview

Michel is a modular, multi-guild Discord bot written in Python 3.12 using the **interactions.py** library (slash commands). It stores data in MongoDB (one database per guild) via the async **Motor** driver, and optionally runs a **FastAPI** web dashboard for configuration management.

## Repository Layout

```
main.py                 # Entry point — discovers and loads extensions, starts the bot + optional Web UI
config.py               # DEBUG flag
dict.py                 # Dictionaries of random phrases (greetings, Spotify confirmations, activities, choices)

extensions/             # Each .py file is an independent bot extension (auto-loaded at startup)
                        # Files prefixed with _ are disabled/archived and NOT loaded

src/
├── config_manager.py   # Reads config/config.json; provides load_config(module) → (global_cfg, per_guild_cfg, enabled_guilds)
├── logutil.py          # Colored ANSI logger (CustomFormatter); init_logger() / get_logger()
├── mongodb.py          # MongoManager singleton — guild_<id> DB per server + global DB; async Motor
├── utils.py            # Shared helpers: pagination, image gen (Pillow), markdown escape, HTTP fetch, number format
├── spotify.py          # Spotipy OAuth, embed builders, vote helpers
├── vlrgg.py            # VLR.gg (Valorant esports) API client with TTL cache
├── minecraft.py        # Minecraft player stats via SFTP (asyncssh) + NBT parsing
├── minecraft_rcon.py   # Raw async RCON socket implementation
├── minecraft_config.py # Minecraft tuning constants (cache TTL, timeouts, limits)
├── coloc/              # Zunivers game integration package
│   ├── api_client.py   # ZuniversAPIClient — async HTTP with retry
│   ├── models.py       # Dataclasses: Reminder, ReminderCollection, EventState, HardcoreSeason, ZuniversEvent
│   ├── storage.py      # StorageManager — persists reminders & event states in MongoDB
│   └── constants.py    # API URLs, timing constants
└── webui/              # Optional FastAPI dashboard
    ├── app.py          # Routes: module toggles, config CRUD, SSE log stream
    ├── server.py       # start_webui() launches Uvicorn in a daemon thread
    ├── auth.py         # Discord OAuth2 + session management (admin-restricted)
    ├── schemas.py      # MODULE_SCHEMAS & GLOBAL_CONFIG_SCHEMAS — field types for dynamic form rendering
    └── static/         # Frontend (index.html)
```

## Key Conventions

### Language & Runtime
- Python 3.12. Use modern syntax (f-strings, `match`, type hints, `|` union types where appropriate).
- **Async everywhere** — Motor for MongoDB, aiohttp for HTTP, asyncssh for SFTP, native async RCON. Never block the event loop.

### Bot Framework
- Built on **interactions.py** (not discord.py). Use `interactions.Extension` for extensions, `@slash_command`, `@component_callback`, `@listen()`, etc.
- The client is created in `main.py` with `intents=interactions.Intents.ALL` and `delete_unused_application_cmds=True`.

### Extension Pattern
- One extension per file in `extensions/`.
- Each file defines a class named `{Name}Extension` inheriting from `interactions.Extension`. No `setup(bot)` function needed — extensions are auto-discovered.
- Extensions prefixed with `_` are **not loaded** (archived). Do not delete them.
- Extensions load their config via `load_config("module_name")` from `src/utils.py`, which returns `(global_config, per_guild_module_config, enabled_guild_ids)`.
- Logger initialization: `logger = logutil.init_logger(os.path.basename(__file__))`.
- Use `@interactions.Task.create(IntervalTrigger(...))` or `@interactions.Task.create(TimeTrigger(...))` for scheduled tasks.

### Database
- MongoDB via **Motor** (async driver). Managed by `MongoManager` singleton in `src/mongodb.py`.
- **Per-guild isolation**: each Discord server has its own database named `guild_{guild_id}`. Global data goes in the `global` database.
- Access: `MongoManager().get_guild_collection(guild_id, "collection_name")` or `MongoManager().get_global_collection("collection_name")`.

### Configuration
- All config in `config/config.json` (not committed). Loaded via `src/config_manager.py`.
- Structure: top-level keys for `discord`, `webui`, `mongodb`, and per-module sections.
- Each module can be enabled/disabled per guild. The Web UI or manual JSON edits manage this.

### Logging
- Use `from src import logutil` then `logger = logutil.init_logger("module_name")`.
- Colored ANSI output. Debug mode shows file name and line numbers.

### Error Handling
- Extensions should handle their own errors gracefully and log them rather than crashing the bot.
- Use custom exception classes when appropriate (see `BirthdayError`, `DatabaseError`, `ZuniversAPIError`).

### Web UI
- Optional FastAPI dashboard (enabled via `webui.enabled` in config).
- Discord OAuth2 authentication restricted to admin user IDs.
- Dynamic config forms generated from JSON schemas in `src/webui/schemas.py`.
- Server-Sent Events (SSE) for real-time log streaming.

## Code Style

- Follow PEP 8. Use type hints for function signatures.
- Prefer `async def` and `await` over synchronous blocking calls.
- Use dataclasses or Pydantic models for structured data.
- Embed construction: use `interactions.Embed()` with proper color, title, description, and fields.
- Keep command descriptions concise and in the language appropriate for the target audience (mostly French for this bot's users).
- Use the shared utilities in `src/utils.py` (pagination, fetch, image generation) rather than reimplementing.

## Testing & Running

- **Docker**: `docker compose up -d` (production). Mounts `config/`, `data/`, `logs/`.
- **Local**: activate venv, then `pip install -e ".[dev]"` (declared in `pyproject.toml`, includes ruff/mypy/pytest/pre-commit/detect-secrets) and `pre-commit install`. Run the bot with `python main.py`. The Docker image installs from the same `pyproject.toml`.
- **Tests**: `pytest` (config in `pyproject.toml`). Lint: `ruff check .`. Types: `mypy src`.

## Common Tasks

### Adding a new extension
1. Create `extensions/myext.py`.
2. Define a class inheriting `interactions.Extension`.
3. Add a `setup(bot)` function.
4. Add the module schema in `src/webui/schemas.py` if the extension has configuration.
5. Restart the bot.

### Adding a new source module
1. Create `src/mymodule.py`.
2. Keep it async-compatible.
3. Import and use it from your extension.

### Disabling an extension
Prefix the filename with `_` (e.g., rename `myext.py` → `_myext.py`).
