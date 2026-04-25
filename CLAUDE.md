# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Install runtime + dev tooling (ruff, mypy, pytest, pre-commit, detect-secrets)
pip install -e ".[dev]"
pre-commit install

# Run the bot (reads config/config.json)
python main.py

# Lint / format
ruff check .
ruff format --check .       # CI runs --check; drop --check to apply
ruff check --fix .

# Type check (src.core.* is strict, rest is lenient)
mypy src

# Tests
pytest
pytest tests/test_smoke.py::test_placeholder    # single test
pytest --cov=src --cov-report=term              # with coverage (CI)

# Pre-commit (runs ruff + detect-secrets + hygiene hooks)
pre-commit run --all-files

# Docker (production layout — mounts ./config, ./data, ./logs)
docker compose up -d
```

`pyproject.toml` is the single source of truth for dependencies — the Docker image installs from it.

## Architecture

Three-layer split. Respect the boundaries:

- **`extensions/`** — Discord-facing layer. Each entry is either `extensions/<name>.py` or a package `extensions/<name>/__init__.py` containing an `interactions.Extension` subclass plus a module-level `setup(bot)` factory. Auto-discovered by `main.py` at startup.
- **`features/`** — Pure domain logic and persistence. **Must not import `interactions`** — this is what makes features unit-testable. Each feature owns a `repository.py` that reads/writes MongoDB.
- **`src/`** — Shared infrastructure.
  - `src/core/` is framework-free (config, db, http, logging, errors, migrations, images, text). Strictly typed under mypy.
  - `src/discord_ext/` holds interactions.py-dependent UI helpers (embeds, paginator, autocomplete, persistent messages).
  - `src/integrations/` holds external-API clients with **no Discord imports** (Spotify, Notion, Minecraft RCON).
  - `src/webui/` is the optional FastAPI dashboard (run in a daemon thread alongside the bot).

### Extension loading

`main.py` walks `extensions/` and loads every `.py` file or package. Enabled state comes from `config["extensions"]["extensions.<name>"]` (bool). Default behavior:

- Entries starting with `_` (e.g. `extensions/_minecraft/`) are **disabled by default** — the convention for archived/in-progress code. Don't delete them.
- All others are enabled by default.
- The Web UI writes to the same config key, so toggles persist across restarts.

`pyproject.toml` also excludes `extensions/_*` from ruff and mypy.

### Composition convention

When an extension grows past ~200 lines, promote it to a package and split responsibilities into mixin modules (e.g. `xp/leveling.py`, `xp/commands.py`, `xp/leaderboard.py`) composed via multiple inheritance in `__init__.py`. Shared constants and the Pydantic config schema go in `_common.py`. Domain logic moves into a matching `features/<name>/` package.

### Web UI schema (strongly recommended for any new extension)

Any extension that reads from `config["module<Name>"]` should declare a Pydantic schema using the `@register_module(...)` decorator from `src.webui.schemas` (typically in `extensions/<name>/_common.py`, see `extensions/xp/_common.py` for a canonical example). This is what makes the module appear in the dashboard with editable, typed fields, per-server toggles, and validation — without it the only way to configure the extension is hand-editing `config/config.json` and restarting. New global config sections use `@register_section(...)` the same way.

### Per-guild data isolation

Each Discord server gets its own MongoDB database named `guild_{guild_id}`; cross-guild data lives in a shared `global` database. Access via the singleton in `src/core/db.py`. Repositories under `features/<name>/repository.py` encapsulate this.

### Config

All config lives in `config/config.json`. Loaded through `src.core.config.load_config(module_name)`, which returns `(global_config, per_guild_module_config, enabled_guild_ids)`. The store is reactive so the Web UI and running extensions stay in sync. `migrate_config_module_keys()` runs at startup to rewrite legacy keys — add new entries there when renaming config paths.

### Async everywhere

Motor (MongoDB), aiohttp (HTTP), asyncssh (SFTP), native async RCON. Never block the event loop. Use the shared aiohttp session from `src.core.http` rather than creating per-call clients.

## Notes

- Python 3.12, target-version `py312` in ruff. Line length 100.
- Logging: `from src.core import logging as logutil; logger = logutil.init_logger("name")`.
- Most user-facing strings are French — match existing language when editing command descriptions and embeds.
- Stale doc warning: `.github/copilot-instructions.md` describes an older flat `src/` layout (`src/utils.py`, `src/mongodb.py`, etc.) that has been refactored into the three-layer split above. Trust `README.md` and the actual file tree, not that file.
- `interactions.py` here is the `discord-py-interactions` package — not `discord.py`. Use `interactions.Extension`, `@slash_command`, `@listen()`, `@interactions.Task.create(IntervalTrigger(...))`.
- `tests/test_smoke.py` is a placeholder to keep CI green; real tests are still being backfilled.
