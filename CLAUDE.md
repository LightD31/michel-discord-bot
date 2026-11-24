# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Create .venv with the locked runtime deps + dev tooling
# (ruff, mypy, pytest, pre-commit, detect-secrets, pip-audit)
uv sync
uv run pre-commit install

# Update uv.lock after editing dependencies in pyproject.toml
# (CI fails if uv.lock drifts from pyproject.toml)
uv lock

# Run the bot (reads config/config.json)
uv run python main.py

# Lint / format
uv run ruff check .
uv run ruff format --check .       # CI runs --check; drop --check to apply
uv run ruff check --fix .

# Type check (src.core.* is strict, src.* and features.* check unannotated bodies
# too, extensions/ is lenient but checked whole, no module skipped; CI on every PR)
uv run mypy src features extensions
uv run pre-commit run mypy --hook-stage manual   # same check via the opt-in hook

# Tests
uv run pytest
uv run pytest tests/test_rss_parser.py::test_parse_rss_extracts_two_entries   # single test
uv run pytest --cov --cov-report=term   # with coverage over src, features, extensions (CI)
uv run coverage report --include='src/*,features/*' --fail-under=81 --format=total  # CI's second floor

# Browser smoke test of the dashboard (tests/test_spa_smoke.py, test_spa_pickers.py):
# skipped without a Chromium. CI runs `uv run playwright install --with-deps chromium` first.
PLAYWRIGHT_CHROMIUM_PATH=/path/to/chrome uv run pytest -m browser

# The dashboard on a fake bot and a seeded test MongoDB (tools/dashboard_demo.py,
# not in the image): prints a login link per session (developer, admin, member).
# --shots <dir> captures every page, desktop and phone: the before/after
# screenshots of a UI change. It writes only to databases it marked as its own.
uv run python tools/dashboard_demo.py --mongo mongodb://localhost:27017
uv run python tools/dashboard_demo.py --mongo mongodb://localhost:27017 --shots /tmp/shots

# Repository tests against a real MongoDB (tests/test_mongo_*.py): skipped unless
# MONGO_TEST_URL is set. CI's test job runs them against a mongo service.
docker run -d --rm -p 27017:27017 mongo:8   # CI pins it in .github/mongo-test/Dockerfile
MONGO_TEST_URL=mongodb://localhost:27017 uv run pytest -m mongo

# Pre-commit (ruff lint+format, detect-secrets, hygiene hooks; mypy is manual-stage only)
uv run pre-commit run --all-files

# Regenerate config.example.json after adding/changing Web UI schemas.
# Needs a config/config.json in place (copy config.example.json over) because
# several extensions read config at import time; the script now exits
# non-zero if any extension fails to import, and CI checks the result for drift.
uv run python scripts/generate_config_example.py

# Docker (production layout — mounts ./config and ./data, read-only otherwise)
docker compose up -d
```

(`uv run` can be dropped once `.venv` is activated.)

## Architecture

Three layers; `tests/test_layering.py` checks the imports:

- **`extensions/`**: the Discord layer, one `Extension` per `extensions/<name>.py` or package (mixins composed in `__init__.py`, the schema and constants in `_common.py`, shared declarations in `_base.py`), plus a `setup(bot)`. Auto-loaded by `main.py`. Never imports `pymongo` / `bson`.
- **`features/`**: domain logic and persistence (`repository.py` per feature, driver errors turned into `DatabaseError`). Never imports `interactions`, `src.discord_ext` or `src.webui`.
- **`src/`**: `core/` (framework-free, imports no other part of `src`), `discord_ext/` (interactions.py helpers and renderers shared with the dashboard), `integrations/` (API clients, no Discord), `webui/` (the FastAPI dashboard and its build-free SPA, in a daemon thread).

Per guild: a `guild_<id>` MongoDB database, settings in `config/config.json` under `servers.<id>`. One deployment: rename config paths and documents directly, no migration, and list the changes in the PR.

## Rules

Each rule is enforced by the test named, or explained in the file named. Read the file before working in its area.

Layout and lifecycle (`docs/conventions/extension-lifecycle.md`, `architecture.md`)
- Each extension class declares `MODULE_KEYS`; one that takes its config at load also `RELOAD_ON_SAVE = True` — `tests/test_module_registry.py`
- Mixins declare what they use (annotations, `TYPE_CHECKING` stubs, `_base.py`); mypy skips nothing — `tests/test_mypy_exclusions.py`
- Read a guild command's context with `guild_id_of` / `guild_of` / `member_of` / `message_of` — `tests/test_command_context.py`
- Background work starts in `on_startup`, guarded by `if not task.running` — `tests/test_task_start_guard.py`
- Non-`Task` resources are released in `async_drop`; never re-implement reload — `extension-lifecycle.md`
- Every command is scoped with `guild_scopes(MODULE.guild_ids())`; menus carry `@menu_help` — `tests/test_command_scopes.py`, `tests/test_help_command.py`
- French command names, hyphenated; one permission per default — `tests/test_command_names.py`, `tests/test_default_permissions.py`

Config and dashboard (`config.md`, `dashboard-security.md`, `dashboard-frontend.md`)
- Every `module…` setting has a registered schema; numbers bounded, roles with a `role_use`, no raw ids, no message-id fields — `tests/test_module_registry.py`
- A service's account or key is a module secret, never a global section — `config.md`
- Regenerate `config.example.json` after a schema change — CI drift check
- Read settings live (`MODULE.get(...)`), never at import; never mutate what the store returns — `tests/test_module_registry.py`, `config.md`
- Persistent messages live in `bot_messages` slots, not config — `config.md`
- Routes check their tier (`require_guild_admin` / `require_guild_member` / `require_developer`) — `tests/test_webui_guild_boundary.py`, `tests/test_webui_member_space.py`
- A route making the bot act applies the Discord rule the user would face — `tests/test_webui_role_grants.py`, `dashboard-security.md`
- Channels are looked up with `fetch_guild_channel()` — `tests/test_channel_lookups.py`
- Config writes go through `ctx.mutate_config` and are audited; secrets masked and restored — `tests/test_webui_save_config.py`, `tests/test_webui_secrets.py`
- Bot work from a route goes through `ctx.call_bot` / `run_on_bot_loop` — `dashboard-security.md`
- No inline script or handler in the SPA; actions in `registerActions` — `tests/test_spa_csp.py`
- New markup uses the `html` tag; `escapeHtml(` only goes down; no static `style=`, `hidden` to toggle; form fields go in `FIELDS` — `tests/test_spa_html.py`
- Contrast tokens at WCAG AA; no native dialog; the smoke test stays clean — `tests/test_spa_contrast.py`, `tests/test_spa_smoke.py`

Async and I/O (`async-io.md`)
- Never block the loop: blocking SDKs and CPU work go through a thread, with a timeout — `tests/test_spotify_async.py`, `async-io.md`
- URLs someone else chose go through `fetch_untrusted()`; credentials never in a URL — `tests/test_core_http_untrusted.py`
- Background work goes through `spawn()`, never a bare `create_task` — `async-io.md`
- No URL literals; config fields instead — `tests/test_no_hardcoded_urls.py`

Discord messages (`discord-messages.md`)
- Edit through `edit_message_if_changed()`; copy an embed with `copy_embed()` — `tests/test_message_edit.py`
- Mentions: `everyone_if_written` / `mentions_written_in` / `NO_PINGS` — `tests/test_mention_policy.py`
- Every embed is `card()` / `custom()`, every V2 container `card_v2()`; replies start with `ok()` / `oops()` / `ask()` — `tests/test_look.py`
- Michel says *tu*, in French — `tests/test_voice.py`, `docs/VOIX.md`
- Refusals say what to do next — `tests/test_refusals_say_what_to_do.py`
- Links go through the shortener; long text through forms — `discord-messages.md`

Data (`data-and-retention.md`)
- Dates are aware UTC (`clock.now()`), wall times on `clock.LOCAL_TZ` — `tests/test_datetimes.py`
- Change stored state in one conditional update, with a `mongo` test — `data-and-retention.md`
- A collection holding a member id gets its line in `features/userdata/repository.py` and in `Privacy.md` — `tests/test_mongo_userdata.py`
- Retention in `Privacy.md` §7 matches the code — `data-and-retention.md`
- Backups change only with the restore script — `tests/test_backup_restore.py`

Errors and logs (`observability.md`)
- In a broad `except`, `log_failure()` — `tests/test_failure_logging.py`
- Don't wrap a command in `try/except Exception` to apologise; let `extensions/errors.py` answer — `discord-messages.md`
- Metrics in the extension's own registry; health in `src/core/health.py`; a new `Task` gets its label — `tests/test_webui_guild_health.py`
- A `Task` looping over guilds binds each one with `log_context.scope(guild_id=…)` — `tests/test_incidents.py`

Tests and tooling (`testing.md`, `tooling.md`)
- Tests use `store_config` / `mongo`, never the developer's setup — `testing.md`
- Handlers are tested with `tests/discord_fakes.py`; repository methods against MongoDB — `testing.md`
- Raise coverage floors, never lower them; keep them below the figures without MongoDB — `pyproject.toml`
- `uv.lock` from `pyproject.toml`; Actions pinned by SHA; Python 3.12 syntax only — `tooling.md`
- This file stays under 12 KB, and its links resolve — `tests/test_claude_md.py`

## Where the details are

`docs/conventions/`: `architecture.md`, `extension-lifecycle.md`, `config.md`, `dashboard-security.md`, `dashboard-frontend.md`, `async-io.md`, `discord-messages.md`, `data-and-retention.md`, `observability.md`, `features.md` (P4–P11 features: seasons, reports, packs, Secret Santa, Communauté+), `testing.md`, `tooling.md`. `docs/conventions/README.md` maps each paragraph of the former CLAUDE.md to its file. Operations: `docs/RUNBOOK.md`. Voice: `docs/VOIX.md`. Privacy: `Privacy.md`.
