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

# Type check (src.core.* is strict, features.* checks unannotated bodies too,
# rest is lenient; runs in CI on every PR)
uv run mypy src features
uv run pre-commit run mypy --hook-stage manual   # same check via the opt-in hook

# Tests
uv run pytest
uv run pytest tests/test_rss_parser.py::test_parse_rss_extracts_two_entries   # single test
uv run pytest --cov=src --cov=features --cov-report=term  # with coverage (CI)

# Repository tests against a real MongoDB (tests/test_mongo_*.py): skipped unless
# MONGO_TEST_URL is set. CI's test job runs them against a mongo service.
docker run -d --rm -p 27017:27017 mongo:8
MONGO_TEST_URL=mongodb://localhost:27017 uv run pytest -m mongo

# Pre-commit (ruff lint+format, detect-secrets, hygiene hooks; mypy is manual-stage only)
uv run pre-commit run --all-files

# Regenerate config.example.json after adding/changing Web UI schemas.
# Needs a config/config.json in place (copy config.example.json over) because
# several extensions read config at import time; the script now exits
# non-zero if any extension fails to import, and CI checks the result for drift.
uv run python scripts/generate_config_example.py

# Docker (production layout — mounts ./config, ./data, ./logs)
docker compose up -d
```

(`uv run` can be dropped once `.venv` is activated.)

`pyproject.toml` declares dependency ranges (runtime in `[project]`, tooling in the `dev` dependency group); `uv.lock` pins both, and the Docker image (`uv sync --locked --no-dev`) and CI (`uv sync` with `UV_LOCKED=1`) install from it. Edit ranges in `pyproject.toml`, then run `uv lock`. CI also runs `pip-audit` against the runtime pins (`uv export --no-dev`) and a detect-secrets sweep against `.secrets.baseline`.

> **Pulling CVE fixes into the lock.** `uv lock` keeps every existing pin that still satisfies `pyproject.toml`, so it never picks up newer releases on its own. To pull in security fixes, run `uv lock --upgrade-package <name>`, or `uv lock --upgrade` to refresh everything. A CVE fix in a transitive dependency can require bumping its *direct* pinning parent too (e.g. the `starlette` 1.3.x fix is gated behind a `fastapi` bump that lifts its `<1.3.0` cap).
>
> **uv version.** The Dockerfile's `uv` stage and `UV_TOOL_VERSION` in `.github/workflows/ci.yml` name the uv release used to install; Dependabot bumps only the Dockerfile one, so move the CI one along with it.
>
> **GitHub Actions are pinned by commit SHA** (`uses: owner/action@<sha> # vX.Y.Z`). When adding or bumping an action by hand, pin the commit of a release tag (the peeled `^{}` entry from `git ls-remote --tags`), never a branch or a moving tag; Dependabot keeps existing pins current. Any recent uv reads and writes the same `uv.lock` (unlike `uv pip compile --universal`, `uv lock` resolves against `requires-python` rather than the host interpreter, so the Python version you lock on doesn't matter).

## Architecture

Three-layer split (~38k lines, excluding tests). Respect the boundaries (`tests/test_layering.py` checks the imports):

- **`extensions/`** — Discord-facing layer (38 entries). Each is either `extensions/<name>.py` or a package `extensions/<name>/__init__.py` containing an `interactions.Extension` subclass plus a module-level `setup(bot)` factory. Auto-discovered by `main.py` at startup. The class lists the per-guild config modules it reads in `MODULE_KEYS` (a tuple, `()` for none) — see *Web UI schema* below.
- **`features/`** — Pure domain logic and persistence. **Must not import `interactions`** — this is what makes features unit-testable. Domain code returns plain data (bytes, dicts, models) and lets the extension build Discord objects. Each feature owns a `repository.py` that reads/writes MongoDB and translates driver exceptions into `src.core.errors.DatabaseError` so extensions never import `pymongo` — decorate methods with `@translates_db_errors` from `src.core.db` (see `features/xp/repository.py`), or raise `DatabaseError` explicitly with a contextual message (see `features/moderation/repository.py`). `bson` counts as the driver too: a repository method taking an id a user typed accepts a `str` and parses the `ObjectId` itself, so a malformed id is an ordinary "not found" rather than an `InvalidId` escaping into a command handler.
- **`src/`** — Shared infrastructure.
  - `src/core/` is framework-free (config, db, http, logging, errors, images, text). Strictly typed under mypy.
  - `src/discord_ext/` holds interactions.py-dependent UI helpers (embeds, paginator, autocomplete, persistent messages). Renderers shared between an extension and a WebUI route also live here (e.g. `rolemenus.py`, `embedmanager.py`): a WebUI route never imports an extension module.
  - `src/integrations/` holds external-API clients with **no Discord imports** (Spotify, Notion, Minecraft RCON, LLDAP).
  - `src/webui/` is the optional FastAPI dashboard (run in a daemon thread alongside the bot).

### Extension loading

`main.py` walks `extensions/` and loads every `.py` file or package. Enabled state comes from `config["extensions"]["extensions.<name>"]` (bool). Default behavior:

- Entries starting with `_` are disabled by default (none currently exist — `main.py` still honors the convention if you need to park work-in-progress code).
- All others are enabled by default. Several modules ship disabled in `config.example.json` instead (minecraft, olympics, speedons, zevent) — that's the normal way to disable, via config, not renaming.
- The Web UI writes to the same config key, so toggles persist across restarts.

`pyproject.toml` excludes `extensions/_*` from ruff and mypy.

`main.py` also runs a heartbeat task (touches `/tmp/bot_heartbeat` every 45 s) and a watchdog thread that exits the process when the heartbeat goes stale; the Docker healthcheck and `restart: unless-stopped` rely on this for self-healing. Don't remove or block either. SIGTERM / SIGINT (`docker compose stop`) go through `GracefulShutdown` (`src/discord_ext/lifecycle.py`): every loaded extension is released (Tasks stopped, `async_drop()` awaited), the shared HTTP and MongoDB clients are closed, the gateway is stopped and the process exits 0, within about 7 s or it exits anyway. A missing or token-less `config/config.json` stops startup with an explicit message. Gateway intents are enumerated explicitly in `main.py` — add the matching intent (with a comment) when a new extension listens to a new gateway event.

### Composition convention

When an extension grows past ~200 lines, promote it to a package and split responsibilities into mixin modules (e.g. `xp/leveling.py`, `xp/commands.py`, `xp/leaderboard.py`) composed via multiple inheritance in `__init__.py`. Shared constants and the Pydantic config schema go in `_common.py`. Domain logic moves into a matching `features/<name>/` package.

### Web UI schema (strongly recommended for any new extension)

Any extension that reads from `config["module<Name>"]` should declare a Pydantic schema using the `@register_module(...)` decorator from `src.webui.schemas` (typically in `extensions/<name>/_common.py`, see `extensions/xp/_common.py` for a canonical example). This is what makes the module appear in the dashboard with editable, typed fields, per-server toggles, and validation — without it the only way to configure the extension is hand-editing `config/config.json` and restarting. New global config sections use `@register_section(...)` the same way. After adding or changing schemas, regenerate `config.example.json` with `scripts/generate_config_example.py`.

The extension class also declares the modules it reads: `MODULE_KEYS = ("moduleXp",)` (several when it reads several, like `metrics`; `moduleUtils` is declared by admin, polls and reminders). Nothing scans the sources: the dashboard builds its module → extension map from the *loaded* extensions (`WebUIContext.module_extension_map()`) to know what to reload after a save or toggle, and accepts a module only when its schema is registered — so the module of an extension that isn't loaded is listed as inactive and can't be edited. `tests/test_module_registry.py` imports every extension and fails when a class doesn't declare `MODULE_KEYS`, declares a module without a schema, names a `module…` key in its files without declaring it, or when a registered schema has no extension (`discord2name` excepted, read on demand).

Deliberate exceptions that share config instead of registering their own module: `polls` and `reminders` read `moduleUtils` (owned by `admin`). `userinfo` and `random_` have no per-guild config at all.

### Custom Web UI views

When schema-driven forms aren't enough (e.g. CRUD over a list of objects, anything that mutates Discord state from the dashboard), drop a router in `src/webui/routes/<name>.py` exposing a `create_router(ctx: WebUIContext) -> APIRouter` factory and register it in `src/webui/app.py` next to the others. `ctx` carries the bot client, the config store, and the auth helpers, so routes can both read MongoDB *and* call back into Discord (post/edit messages, etc.). The reaction-role menu builder (`src/webui/routes/rolemenus.py` paired with `features/reactionroles/`) is the reference pattern; `routes/moderation.py` and `routes/spotify.py` follow it. The frontend lives in a single SPA at `src/webui/static/index.html`; `frontend.py` catch-alls any unknown path back to it, so new views are added by extending that file rather than creating per-view templates.

### Web UI authorization

Two tiers, enforced via helpers on `WebUIContext` (`src/webui/context.py`): any authenticated Discord user can manage the guilds where they hold `MANAGE_GUILD`/`ADMINISTRATOR` (or ownership); user IDs in `config.webui.developerUserIds` additionally get global config, extension reload, and the live log stream. Use `await ctx.require_guild_admin()` for per-guild routes and `require_developer()` for global ones — never skip the check. The auth endpoints are rate-limited per client IP (`src/webui/ratelimit.py`), and every mutating `/api/` request (`POST`/`PUT`/`PATCH`/`DELETE`) per session (`src/webui/mutation_limit.py`, 30 a minute). Forwarding headers are believed only from `webui.trustedProxies`, which defaults to loopback alone: a reverse proxy in a container or on another host must be listed.

- **Per-guild checks are live.** `require_guild_admin()` is async: it reads the member through the bot (`src/webui/guild_access.py`, cached 60 s), never trusting the OAuth guild list captured at login, so a demoted or banned member loses access within a minute. When the bot can't answer, the check fails closed (503). Only a dashboard with no bot client at all falls back to the login snapshot. `is_admin_user()` / `require_admin()` still read the snapshot and only gate endpoints with nothing guild-specific (module list, schemas).
- **Sessions** live 24 h server-side, the same as their cookie (`SESSION_TTL_SECONDS` in `src/webui/auth.py`), and are keyed in memory and in MongoDB (`src/webui/sessions.py`, TTL index) by the SHA-256 of the cookie value, never the cookie itself. The OAuth tokens are used once at login and dropped. `global.webui_sessions` is excluded from backups (`BACKUP_EXCLUDED_COLLECTIONS` in `src/core/db.py`).
- **The dashboard bar (`MANAGE_GUILD`) grants no Discord power by itself.** A route that makes the bot act with its own permissions on someone's behalf must apply the Discord rule the user would face by hand. The reference is role menus: `features/reactionroles/permissions.py` refuses staff roles outright (re-checked on every click) and requires `MANAGE_ROLES` plus a higher role from whoever builds the menu. The raw permission bits live in `src/core/permissions.py`.
- **Module saves** (`PUT …/modules/{module_name}` and `…/toggle`) only accept registered or discovered module names, and cap one module's config at 256 KB.
- **Saves are validated against the schemas.** `SchemaBase` is `extra="forbid"`, and the module and global-section save routes run `schemas.validation_errors()` inside the `mutate_config` mutator, so a 422 leaves `config.json` untouched; a global section must be registered (404 otherwise). The field annotation is the contract: it must match what the SPA sends for that widget type (e.g. `spotifymap` is a list). `scripts/check_config.py` runs the same check over a whole `config.json`.
- **Security headers** (`src/webui/security_headers.py`, pure ASGI middleware): no framing, `nosniff`, `Referrer-Policy: same-origin` (public dashboard tokens sit in the URL path), and HSTS when the request really came over HTTPS. Script sources are not restricted yet: the SPA relies on inline handlers.

The one deliberate exception is `routes/public.py`: the per-guild public XP and Spotify dashboards (SPA pages at `/public/{token}/{xp|spotify}`). `token` is a random per-guild secret (`servers.<id>.publicDashboardToken`, minted and rotated by guild admins through `/api/servers/{id}/public-dashboard`), never the guild id, which every member can see. The pages are opt-in per module through the `publicDashboard` flag (`src/webui/public_links.py`), answer a bad token and an unpublished page with the same 404, strip Discord ids from their payloads (names only), hide the open Spotify poll's tally and voter names until it closes (the voter count may show), and cache each aggregation for 5 minutes so anonymous traffic can't hammer MongoDB. Keep all of these properties if you add another public page.

### Web UI config I/O rules

- **Mutations**: use `ctx.mutate_config(mutator)` — an atomic read-modify-write under the config write lock — never `get_full_config()` + `save_config()` in sequence (that pattern silently drops concurrent edits). Include the acting user (`session.username`/`session.user_id` from the `require_*` helper) in the mutation log line.
- **Secrets**: schema fields declared `secret=True` are masked to a placeholder in every API response (`src/webui/secrets.py`); the save paths call `restore_section()` so an untouched placeholder keeps the on-disk value. Any new endpoint that returns config must mask (`mask_full_config` / `mask_section` / `mask_server_config`), and any new save path for schema-backed config must restore.

### Web UI → bot dispatch

The dashboard serves requests from a daemon thread with its own uvicorn loop, so **never touch the running client inline in a route**. Anything that mutates the bot — `load_extension` / `unload_extension` / `reload_extension`, a command sync, sending a message — runs on the bot loop via `ctx.run_on_bot_loop(...)`, with the extension helpers in `src/webui/botops.py` (`run_extension_op`, `sync_commands`) as the entry points. Called inline, `reload_extension` makes interactions.py schedule its `synchronise_interactions()` task on the *uvicorn* loop while the client's aiohttp session belongs to the bot loop; the task dies with `RuntimeError: Timeout context manager should be used inside a task`, and since nobody awaits it the only trace is a stray "Task exception was never retrieved" while commands silently never sync. `run_extension_op` also suppresses the library's implicit per-call sync (one unawaited task per load *and* unload) — callers do one awaited `sync_commands()` when the command set actually changed. A route doing its own Discord work (list channels, post a role menu…) goes through `await ctx.call_bot(fn, what=...)`: a bounded timeout (504 when the bot doesn't answer), `HTTPException` passed through, anything else logged and turned into a generic 500. Never put `str(e)` in a response `detail`.

`run_extension_op` also completes the lifecycle the library's reload leaves half-done.
- **Before an unload or reload**, it stops every `Task` on the outgoing instance and awaits its optional `async def async_drop(self)`. It then purges the package's submodules from `sys.modules`, so `_common` (and its config snapshot) and the mixins are re-imported. Without the purge, commands and tasks defined on cached mixins stay bound to the unloaded instance.
- **After a load or reload on a running client**, it runs the new instance's `on_startup` / `on_ready` listeners, because the gateway fires `Startup` / `Ready` only once per process.

So:
- Start background work from `@listen() async def on_startup` (idempotent: guard `Task.start()` with `if not task.running`), not from `__init__`.
- Release anything that isn't a `Task` (a websocket, a socket.io client, a listening port, a third-party API client) in `async_drop`. `twitch`, `streamlabs` and `metrics` are the examples. The same `release()` runs on an unload and at process shutdown.
- Never re-implement reload handling inside an extension.

### Per-guild data isolation

Each Discord server gets its own MongoDB database named `guild_{guild_id}`; cross-guild data lives in a shared `global` database. Access via the singleton in `src/core/db.py` (`get_guild_collection`, `get_global_collection`). Motor clients are created per event loop — the bot loop and the Web UI's uvicorn loop each get their own — so never cache a collection across loops. Repositories under `features/<name>/repository.py` encapsulate this.

### Config

All config lives in `config/config.json`: a `config` key for global sections and a `servers` key for per-guild module configs. Loaded through `src.core.config.load_config(module_name)`, which returns `(global_config, per_guild_module_config, enabled_guild_ids)`. The store is reactive (atomic tempfile-and-replace writes, subscriber callbacks) so the Web UI and running extensions stay in sync. There is a single deployment, run by the maintainer, so there is no backward compatibility to keep: rename or restructure config paths and stored documents directly, with no startup migration, fallback or transition option, and list in the PR description what to change in `config.json` or the database.

### Async everywhere

Motor (MongoDB), aiohttp (HTTP), asyncssh (SFTP), native async RCON. Never block the event loop. Outbound connections that carry credentials authenticate the server: the Minecraft SFTP connection trusts only the configured `minecraftSftpHostKey` (`features/minecraft/stats_sftp.py:trusted_host_keys`), and LLDAP password changes go over `ldaps://` with a verified certificate. Use the shared aiohttp session from `src.core.http` rather than creating per-call clients — it adds retry with jittered backoff and redacts credentials from logged URLs.

- **URLs someone else chose go through `fetch_untrusted()`, not `fetch()`.** That covers a guild's RSS feeds, `/rss test`, the Streamlabs team URL, and pagination links read from such a response. `fetch_untrusted()` (`src/core/http.py`) only reaches public addresses: its resolver drops loopback, private, link-local (cloud metadata) and reserved IPs, an IP-literal host is checked before connecting, and every redirect is re-checked. It caps the body at 5 MB and makes one attempt. A refusal raises `BlockedUrlError` (an `HttpError`). `fetch()` stays for the bot's own configured endpoints.
- **Synchronous SDKs run off the loop.** spotipy is blocking (`requests`, with a `time.sleep` on 429), so coroutines call it as `await spotify_api.<method>(...)` (`src/integrations/spotify.py`). That runs on a single dedicated worker thread, which keeps calls ordered and token refreshes race-free. Use `run_spotify(fn, ...)` for other blocking Spotify work (the OAuth code exchange), and catch `SPOTIFY_ERRORS`: transport errors are `requests` exceptions, not the builtin `ConnectionError`. `tests/test_spotify_async.py` fails if an extension calls the synchronous `sp` client. Any other blocking library goes through `asyncio.to_thread` the same way.
- **Credentials never go in a URL.** Send API keys in a header (YouTube uses `X-Goog-Api-Key`). `HttpError` redacts its URL and message on construction (`src/core/urls.py`) as a backstop, and log lines that print a URL use `redact_url()`.

Background work that isn't an `interactions` `@Task.create` trigger goes through `spawn()` (`src.core.tasks`), never bare `asyncio.create_task(...)`: the loop holds tasks only weakly, so a discarded handle can be garbage-collected mid-await and cancelled with no traceback. `spawn()` keeps the reference until completion and logs whatever the coroutine raised.

Anything that hands work between the bot loop and the Web UI's uvicorn loop has to cross threads explicitly. `asyncio.Queue`, `Future` and friends are not thread-safe — dispatch with `loop.call_soon_threadsafe(...)` (see `src/webui/log_handler.py`) or, for the Web UI calling into the bot, `ctx.run_on_bot_loop(...)`.

## Notes

- `requires-python >= 3.12`; CI and the Docker image run Python 3.15, and CI's `test-py312` job runs the suite on 3.12 so that floor stays real — don't use 3.13+-only syntax or APIs. Ruff target-version is `py312`, line length 100.
- **Windows + detect-secrets**: run scans with `PYTHONUTF8=1` (e.g. `$env:PYTHONUTF8=1` before `pre-commit run` or `detect-secrets-hook`). Without it, detect-secrets reads files in the legacy locale encoding, silently skips any file containing characters it can't decode (e.g. the ⭐ emoji in `config.example.json`), and will wrongly prune valid `.secrets.baseline` entries that CI then re-flags. Never commit a baseline shrink you can't reproduce with `PYTHONUTF8=1`.
- **Editing a Discord message**: go through `edit_message_if_changed()` (`src.discord_ext.messages`) rather than calling `Message.edit()` directly. It enforces one order — diff the payload against what the message already shows, and only if something changed check whether the message sits in an archived thread and unarchive it before editing. Skipping the diff would make every periodic refresher resurrect archived threads for a no-op write. Pass `ignore_timestamp=True` on refreshers that stamp `datetime.now()` on each render, and `force=True` when the payload is known-new but undiffable. To change an embed the message already shows, edit `copy_embed(message.embeds[0])`, never `message.embeds[0]` itself: the in-place edit also changes the message's copy, the diff compares the embed with itself, and the edit is silently skipped (this froze the Spotify, poll and suggestion vote tallies).
- **Freshness markers in a refreshed embed**: render them from a *fixed* instant (`<t:…:R>` via `TimestampStyles.RelativeTime`), never from `datetime.now()` recomputed each cycle. Discord renders the relative label client-side and keeps "il y a 5 minutes" current on its own, so a fixed epoch stays accurate without an edit — whereas a recomputed one changes the payload every cycle and defeats `edit_message_if_changed()` entirely. Same trap with `interactions.ext.paginators.Paginator`: every button `custom_id` derives from a per-instance uuid, so rebuilding the paginator each refresh churns the components (and leaks a component callback per cycle, since `add_component_callback` rejects duplicate listeners). Keep one instance per persistent message and call `CustomPaginator.set_pages()`.
- **Posting an external link**: route it through `features.links` (`shorten_url` for a single URL, `shorten_text` for a rendered message) or `src.discord_ext.links` (`shorten_embed` / `shorten_embeds` for a whole embed) so it goes through the configured Shlink instance. Every entry point degrades to the original URL when Shlink is off or unreachable — never let a shortener failure lose a post. Wired into RSS entries, YouTube notifications, the Embed manager, MDI, Streamlabs, and the Spotify/Confrérie recap messages. Links that have an *identity* (a guild's Spotify dashboard, an Embed-manager entry) go through `shorten_keyed(key, url)` instead: the short URL's slug is derived from the slot key, so editing the configured target retargets the existing short URL (PATCH) rather than minting a second one — links already shared in Discord keep working. Use plain `shorten_url` for one-off targets (an RSS entry, a video) where every URL is its own thing. Periodically-refreshed embeds are safe to shorten: `shorten_url` reuses the memoized short URL and, on an API failure, falls back to the last one it minted (`cache_get(..., allow_stale=True)`), so a refresh renders an identical payload and `edit_message_if_changed()` still skips the edit. Deliberately *not* shortened: URLs Discord unfurls specially (`open.spotify.com` track links lose their player preview) and embed image/thumbnail URLs (Discord fetches those itself — `should_shorten()` rejects media URLs).
- **No URLs in the code**: user-facing links, icons, images and third-party endpoints belong in config, declared as a `ui(..., "url")` field on the module/section schema and read at use time — never hardcoded, not even as a schema `default`. An unset URL must degrade gracefully (omit the embed field, skip the task with a log line), not crash. Canonical URLs *derived* from data (`https://twitch.tv/{login}`, `https://youtu.be/{video_id}`, a Discord message jump link) are the exception.
- **Config-defined slash commands**: `extensions/customcommands` turns each guild's `moduleCustomCommands.commands` list into real guild-scoped `interactions.SlashCommand` objects — no command name is hardcoded. Register them from `async_start` (runs once every other extension is loaded, before the first command sync) or immediately in `__init__` when the bot is already ready (a dashboard reload never fires `async_start` again), append them to `self._commands` so `Extension.drop()` unregisters them, and never claim a name already in `bot.interactions_by_scope` — `add_interaction` raises on a duplicate. The dashboard save path (`src/webui/routes/servers.py`) reloads the extension and then syncs that one guild scope on the bot loop, so a new command reaches Discord without a restart.
- **Slash command scopes**: a module's commands exist only on the guilds where the module is enabled. Pass `scopes=guild_scopes(enabled_servers)` (`src.discord_ext.scopes`) to every `@slash_command` / `SlashCommand`, never the raw list: interactions.py turns an empty `scopes` into a *global* command (`scopes or [GLOBAL_SCOPE]`), so a module enabled nowhere would show on every server. Whole-bot maintenance commands go on the dev guild (`guild_scopes([devGuildId])`, like `/backup`). The only global commands are `random_` and compareai's user-installable `/ask` / `/ask-stats` (anyone can install the app and spend the bot's LLM credits, so every AI answer first passes `features/ai/quota.py`: the kill switch, external-use rules, then a daily call reservation in `features/ai/usage.py` that fails closed); `tests/test_command_scopes.py` fails on any other unscoped command. The dashboard keeps Discord in step: toggling a module, or a save that flips its `enabled`, reloads every loaded extension that declares the module in `MODULE_KEYS` (skipping ones disabled in the extension list) and syncs that guild's commands.
- **Backups** (`MongoManager.backup_all`) write one gzip-compressed Canonical Extended JSON Lines file per collection plus a `manifest.json` (`src/core/backup_format.py`, shared with `scripts/restore_backup.py`), streamed in batches on worker threads. Change the on-disk layout only together with the restore script and its round-trip test (`tests/test_backup_restore.py`). Operating procedures (restore, off-site copy, incidents, secret rotation) live in `docs/RUNBOOK.md`.
- **Interaction errors**: `extensions/errors.py` replaces interactions.py's default `CommandError` / `ComponentError` / `ModalError` listeners. Cooldowns, concurrency limits and failed checks get a French ephemeral reply; anything else is logged with its traceback and a short reference that the member is shown (`src/discord_ext/errors.py`). The reference is the interaction's correlation id, so it finds every line the interaction logged. Don't wrap a whole command in `try/except Exception` just to tell the member something went wrong: let it raise.
- Logging: `from src.core import logging as logutil; logger = logutil.init_logger("name")`. `MICHEL_DEBUG=1` enables debug level, `MICHEL_LOG_FORMAT=json` one JSON object per line. Every record carries the log context (`src/core/log_context.py`: `correlation_id`, `guild_id`, `command`, `task`), bound for each command, button and modal by the client's `global_pre_run_callback` (`src/discord_ext/interaction_context.py`) and for each `Task` run by `src/discord_ext/task_health.py`. Background work started some other way can bind its own with `log_context.scope(...)`. Log an unexpected failure with `exc_info` (`logger.exception`, or `exc_info=e`): that is what the optional error reporting forwards (`src/core/error_reporting.py`, global section `errorReporting`, off without a DSN); an error line without one stays in the logs, as an expected failure.
- Most user-facing strings are French — match existing language when editing command descriptions and embeds.
- `interactions.py` here is the `discord-py-interactions` package — not `discord.py`. Use `interactions.Extension`, `@slash_command`, `@listen()`, `@interactions.Task.create(IntervalTrigger(...))`.
- Real tests exist under `tests/` (core db/http/config, Web UI auth and config saving, giveaway draw, moderation, MDI client, RSS parser, weighted messages, Tricount balances, reaction-poll closing, reminder scheduling, birthdays, starboard rules, YouTube filters, welcome). Coverage is measured over `src` and `features` — when adding domain logic to `features/`, add a matching test. `pytest --cov` fails below `fail_under` in `[tool.coverage.report]`; raise that floor when coverage grows, never lower it to get a PR green. Keep the floor below the figure *without* MongoDB, so a local run without one still passes.
- **Repository tests run against a real MongoDB** (`tests/test_mongo_*.py`, `pytestmark = pytest.mark.mongo`). The `mongo` fixture (`tests/conftest.py`) points `mongo_manager` at `MONGO_TEST_URL`, on a guild database and a `global` database of its own (both dropped afterwards), and skips the test when the variable is unset. Use it for anything an in-memory fake would only pretend to check: aggregations, atomic updates (`$inc`, `find_one_and_update`, pipeline updates), unique and TTL indexes, dotted field paths. A new repository method gets a test there.
- **Stored keys are values, not field names**, when users choose them: a YouTube handle can contain a dot, which an update would read as a nested path (`features/youtube/repository.py` stores a list of `{"handle", "video_id"}` pairs for that reason). Interpolate a key into a field path only when it can't contain `.` or start with `$` (a Discord id, checked with `isdigit()` as in `SuggestionsRepository.toggle_vote`).
- Several extensions are tied to dated events (olympics → Milan-Cortina 2026, speedons → 2025 API slug). They ship config-disabled rather than deleted. `zevent` is no longer year-pinned: it resolves the edition from the stats API's `/events` listing (override with `zeventStatsEventId`), so only the two start dates need refreshing each year.
- **Prometheus metrics** (`extensions/metrics.py`, global section `metrics`): `/metrics` on its own port via `prometheus_client`, independent of the Web UI. Keep everything in the extension's own `CollectorRegistry` (never the global `REGISTRY`, which a reload would hit with duplicated series) and free the port in `async_drop`. Scrapes run on `prometheus_client`'s HTTP thread, so they only read the last snapshot published to `features.metrics.SnapshotCollector` plus plain client attributes; MongoDB is read only by the refresh task on the bot loop. Sample builders live in `features/metrics/samples.py` (pure, tested) — add a `MetricSpec` there for every new snapshot metric. Process health is recorded as it happens in `src/core/health.py` (framework-free, thread-safe): `Task` runs (`src/discord_ext/task_health.py` wraps `Task.__call__` / `Task.on_error` once in `main.py`, so no extension has to opt in), requests through the shared HTTP sessions (an aiohttp `TraceConfig` in `src/core/http.py`, one record per attempt), failed `spawn()` tasks and finished backups; `features/metrics/health.py` turns its snapshot into `michel_task_*`, `michel_http_requests_total`, `michel_background_task_failures_total` and `michel_backup_last_success_timestamp_seconds` at scrape time. A new periodic job gets those series for free if it is a `Task`; record anything else in `health` rather than in an extension-owned metric, so it is counted whether the exporter is on or not.
