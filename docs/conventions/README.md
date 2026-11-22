# Conventions

The reasoning behind the one-line rules of `CLAUDE.md`, by topic. Read the file of the area you work in before changing it; a guard test's failure message says which rule it holds.

| File | Read it when you touch |
|---|---|
| [architecture.md](architecture.md) | the layers, where a new module goes, per-guild databases |
| [extension-lifecycle.md](extension-lifecycle.md) | loading, reloading, mixins, `Task`s, slash command scopes, custom commands |
| [config.md](config.md) | a schema, a setting, a persistent message, the URLs in config |
| [dashboard-security.md](dashboard-security.md) | a dashboard route, its tier, saves, secrets, the member space, public pages |
| [dashboard-frontend.md](dashboard-frontend.md) | the SPA: modules, actions, escaping, previews, the save bar, "Tester" buttons |
| [async-io.md](async-io.md) | anything that waits: HTTP, blocking SDKs, CPU work, pollers, cross-loop calls |
| [discord-messages.md](discord-messages.md) | what the bot posts: edits, mentions, links, errors, tone, embeds, forms |
| [data-and-retention.md](data-and-retention.md) | MongoDB documents, dates, backups, retention and member data |
| [observability.md](observability.md) | logging, error reporting, Prometheus metrics, health |
| [features.md](features.md) | XP seasons and the Gazette, reports, first steps, Secret Santa, Communauté+ |
| [testing.md](testing.md) | the test suite, fakes, MongoDB tests, source-scanning guards |
| [tooling.md](tooling.md) | dependencies, the lockfile, CI pins, pre-commit, Python versions |

## Where each paragraph of the former CLAUDE.md went

D5 (P12) moved the *Architecture* and *Notes* sections here verbatim, one paragraph or top-level bullet at a time; the commands stayed in `CLAUDE.md`. The paragraph on mixins was then brought up to date with D4 (`_base.py`, no module skipped by mypy).

| # | Was under | Starts with | Now in |
|---|---|---|---|
| 0 | Dependencies | `pyproject.toml` declares dependency ranges (runtime in `[project]`, t… | [tooling.md](tooling.md) |
| 1 | Dependencies | > **Pulling CVE fixes into the lock.** `uv lock` keeps every existing … | [tooling.md](tooling.md) |
| 2 | Architecture | Three-layer split (~38k lines, excluding tests). Respect the boundarie… | [architecture.md](architecture.md) |
| 3 | Architecture | - **`extensions/`** — Discord-facing layer (39 entries). Each is eithe… | [architecture.md](architecture.md) |
| 4 | Architecture | - **`features/`** — Pure domain logic and persistence. **Must not impo… | [architecture.md](architecture.md) |
| 5 | Architecture | - **`src/`** — Shared infrastructure. - `src/core/` is framework-free … | [architecture.md](architecture.md) |
| 6 | Extension loading | `main.py` walks `extensions/` and loads every `.py` file or package. E… | [extension-lifecycle.md](extension-lifecycle.md) |
| 7 | Extension loading | - Entries starting with `_` are disabled by default (none currently ex… | [extension-lifecycle.md](extension-lifecycle.md) |
| 8 | Extension loading | - All others are enabled by default. Several modules ship disabled in … | [extension-lifecycle.md](extension-lifecycle.md) |
| 9 | Extension loading | - The Web UI writes to the same config key, so toggles persist across … | [extension-lifecycle.md](extension-lifecycle.md) |
| 10 | Extension loading | `pyproject.toml` excludes `extensions/_*` from ruff and mypy.… | [extension-lifecycle.md](extension-lifecycle.md) |
| 11 | Extension loading | `main.py` also runs a heartbeat task (touches `/tmp/bot_heartbeat` eve… | [extension-lifecycle.md](extension-lifecycle.md) |
| 12 | Composition convention | When an extension grows past ~200 lines, promote it to a package and s… | [extension-lifecycle.md](extension-lifecycle.md) |
| 13 | Web UI schema (strongly recommended for any new extension) | Any extension that reads from `config["module<Name>"]` should declare … | [config.md](config.md) |
| 14 | Web UI schema (strongly recommended for any new extension) | The dashboard never asks for or shows a raw id. A channel is a `channe… | [config.md](config.md) |
| 15 | Web UI schema (strongly recommended for any new extension) | The extension class also declares the modules it reads: `MODULE_KEYS =… | [config.md](config.md) |
| 16 | Web UI schema (strongly recommended for any new extension) | Deliberate exceptions that share config instead of registering their o… | [config.md](config.md) |
| 17 | Custom Web UI views | When schema-driven forms aren't enough (e.g. CRUD over a list of objec… | [dashboard-frontend.md](dashboard-frontend.md) |
| 18 | Web UI authorization | Three tiers, enforced via helpers on `WebUIContext` (`src/webui/contex… | [dashboard-security.md](dashboard-security.md) |
| 19 | Web UI authorization | - **Per-guild checks are live.** `require_guild_admin()` is async: it … | [dashboard-security.md](dashboard-security.md) |
| 20 | Web UI authorization | - **The member space** (P10, C6, `src/webui/routes/me.py`, page `js/pa… | [dashboard-security.md](dashboard-security.md) |
| 21 | Web UI authorization | - **Sessions** live 24 h server-side, the same as their cookie (`SESSI… | [dashboard-security.md](dashboard-security.md) |
| 22 | Web UI authorization | - **The dashboard bar (`MANAGE_GUILD`) grants no Discord power by itse… | [dashboard-security.md](dashboard-security.md) |
| 23 | Web UI authorization | - **A guild's settings stay in that guild.** `bot.fetch_channel()` (an… | [dashboard-security.md](dashboard-security.md) |
| 24 | Web UI authorization | - **Module saves** (`PUT …/modules/{module_name}` and `…/toggle`) only… | [dashboard-security.md](dashboard-security.md) |
| 25 | Web UI authorization | - **Live preview and the save bar** (P9, C4 / C5). Under each template… | [dashboard-frontend.md](dashboard-frontend.md) |
| 26 | Web UI authorization | - **"Tester" buttons** (C9, `src/webui/routes/checks.py`): "🧪 Tester" … | [dashboard-frontend.md](dashboard-frontend.md) |
| 27 | Web UI authorization | - **Template tests** (`POST …/template-test`, `src/webui/template_test… | [dashboard-frontend.md](dashboard-frontend.md) |
| 28 | Web UI authorization | - **Saves are validated against the schemas.** `SchemaBase` is `extra=… | [dashboard-security.md](dashboard-security.md) |
| 29 | Web UI authorization | - **Security headers** (`src/webui/security_headers.py`, pure ASGI mid… | [dashboard-security.md](dashboard-security.md) |
| 30 | Web UI authorization | - **The SPA reports what really happened.** `api()` (`js/core/api.js`)… | [dashboard-frontend.md](dashboard-frontend.md) |
| 31 | Web UI authorization | - **The SPA escapes every value it interpolates into HTML.** Guild nam… | [dashboard-frontend.md](dashboard-frontend.md) |
| 32 | Web UI authorization | The one deliberate exception is `routes/public.py`: the per-guild publ… | [dashboard-security.md](dashboard-security.md) |
| 33 | Web UI config I/O rules | - **Mutations**: use `ctx.mutate_config(mutator)` — an atomic read-mod… | [dashboard-security.md](dashboard-security.md) |
| 34 | Web UI config I/O rules | - **Secrets**: schema fields declared `secret=True` are masked to a pl… | [dashboard-security.md](dashboard-security.md) |
| 35 | Web UI → bot dispatch | The dashboard serves requests from a daemon thread with its own uvicor… | [extension-lifecycle.md](extension-lifecycle.md) |
| 36 | Web UI → bot dispatch | `run_extension_op` also completes the lifecycle the library's reload l… | [extension-lifecycle.md](extension-lifecycle.md) |
| 37 | Web UI → bot dispatch | - **Before an unload or reload**, it stops every `Task` on the outgoin… | [extension-lifecycle.md](extension-lifecycle.md) |
| 38 | Web UI → bot dispatch | - **After a load or reload on a running client**, it runs the new inst… | [extension-lifecycle.md](extension-lifecycle.md) |
| 39 | Web UI → bot dispatch | So:… | [extension-lifecycle.md](extension-lifecycle.md) |
| 40 | Web UI → bot dispatch | - Start background work from `@listen() async def on_startup` (idempot… | [extension-lifecycle.md](extension-lifecycle.md) |
| 41 | Web UI → bot dispatch | - Release anything that isn't a `Task` (a websocket, a socket.io clien… | [extension-lifecycle.md](extension-lifecycle.md) |
| 42 | Web UI → bot dispatch | - Never re-implement reload handling inside an extension.… | [extension-lifecycle.md](extension-lifecycle.md) |
| 43 | Per-guild data isolation | Each Discord server gets its own MongoDB database named `guild_{guild_… | [architecture.md](architecture.md) |
| 44 | Config | All config lives in `config/config.json`: a `config` key for global se… | [config.md](config.md) |
| 45 | Config | Read it through the live accessors, at use time:… | [config.md](config.md) |
| 46 | Config | - `MODULE = ModuleConfig("moduleXp")` next to the schema (in `_common.… | [config.md](config.md) |
| 47 | Config | - `global_config()` for the global sections (they are never reloaded f… | [config.md](config.md) |
| 48 | Config | - Command scopes are the one read taken at import, `scopes=guild_scope… | [config.md](config.md) |
| 49 | Config | - Name the accessor so it doesn't look sensitive to CodeQL (no `secret… | [config.md](config.md) |
| 50 | Config | - A refresher that keeps one persistent message per guild (a status, a… | [config.md](config.md) |
| 51 | Config | - **Where a persistent message lives is not config.** Every message th… | [config.md](config.md) |
| 52 | Config | - What the accessors return is the store's own data, shared with every… | [config.md](config.md) |
| 53 | Async everywhere | PyMongo async (MongoDB), aiohttp (HTTP), asyncssh (SFTP). Never block … | [async-io.md](async-io.md) |
| 54 | Async everywhere | - **URLs someone else chose go through `fetch_untrusted()`, not `fetch… | [async-io.md](async-io.md) |
| 55 | Async everywhere | - **Synchronous SDKs run off the loop.** spotipy is blocking (`request… | [async-io.md](async-io.md) |
| 56 | Async everywhere | - **CPU work runs off the loop too.** Pillow renders (the rank and wel… | [async-io.md](async-io.md) |
| 57 | Async everywhere | - **Pollers fetch in parallel, within a bound.** The RSS cycle (`exten… | [async-io.md](async-io.md) |
| 58 | Async everywhere | - **Credentials never go in a URL.** Send API keys in a header (YouTub… | [async-io.md](async-io.md) |
| 59 | Async everywhere | Background work that isn't an `interactions` `@Task.create` trigger go… | [async-io.md](async-io.md) |
| 60 | Async everywhere | Anything that hands work between the bot loop and the Web UI's uvicorn… | [async-io.md](async-io.md) |
| 61 | Notes | - `requires-python >= 3.12`; CI and the Docker image run Python 3.15, … | [tooling.md](tooling.md) |
| 62 | Notes | - **Windows + detect-secrets**: run scans with `PYTHONUTF8=1` (e.g. `$… | [tooling.md](tooling.md) |
| 63 | Notes | - **Editing a Discord message**: go through `edit_message_if_changed()… | [discord-messages.md](discord-messages.md) |
| 64 | Notes | - **Mentions**: `main.py` installs a default (`src/discord_ext/mention… | [discord-messages.md](discord-messages.md) |
| 65 | Notes | - **Dates**: an instant the bot keeps (a deadline, a creation date) is… | [data-and-retention.md](data-and-retention.md) |
| 66 | Notes | - **Freshness markers in a refreshed embed**: render them from a *fixe… | [discord-messages.md](discord-messages.md) |
| 67 | Notes | - **Posting an external link**: route it through `features.links` (`sh… | [discord-messages.md](discord-messages.md) |
| 68 | Notes | - **No URLs in the code**: user-facing links, icons, images and third-… | [config.md](config.md) |
| 69 | Notes | - **Config-defined slash commands**: `extensions/customcommands` turns… | [extension-lifecycle.md](extension-lifecycle.md) |
| 70 | Notes | - **Slash command scopes**: a module's commands exist only on the guil… | [extension-lifecycle.md](extension-lifecycle.md) |
| 71 | Notes | - **Backups** (`MongoManager.backup_all`) write one gzip-compressed Ca… | [data-and-retention.md](data-and-retention.md) |
| 72 | Notes | - **Interaction errors**: `extensions/errors.py` replaces interactions… | [discord-messages.md](discord-messages.md) |
| 73 | Notes | - Logging: `from src.core import logging as logutil; logger = logutil.… | [observability.md](observability.md) |
| 74 | Notes | - Most user-facing strings are French — match existing language when e… | [discord-messages.md](discord-messages.md) |
| 75 | Notes | - `interactions.py` here is the `discord-py-interactions` package — no… | [architecture.md](architecture.md) |
| 76 | Notes | - Real tests exist under `tests/` (core db/http/config, Web UI auth an… | [testing.md](testing.md) |
| 77 | Notes | - **Tests never touch the developer's setup.** `tests/conftest.py` poi… | [testing.md](testing.md) |
| 78 | Notes | - **Guards that scan the sources** (dates, layers, command scopes, fai… | [testing.md](testing.md) |
| 79 | Notes | - **Repository tests run against a real MongoDB** (`tests/test_mongo_*… | [testing.md](testing.md) |
| 80 | Notes | - **Change stored state in one update, never by writing back a copy.**… | [data-and-retention.md](data-and-retention.md) |
| 81 | Notes | - **Retention.** What the bot stores is kept only as long as something… | [data-and-retention.md](data-and-retention.md) |
| 82 | Notes | - **XP seasons, `/profil` and the Gazette** (P5). A season is `seasonM… | [features.md](features.md) |
| 83 | Notes | - **Reports to moderators** (`extensions/moderation/reports.py`, `feat… | [features.md](features.md) |
| 84 | Notes | - **One look** (P6, `src/discord_ext/look.py`): every embed is `card(m… | [discord-messages.md](discord-messages.md) |
| 85 | Notes | - **Forms and pickers** (P7): a member never types a list, an id or a … | [discord-messages.md](discord-messages.md) |
| 86 | Notes | - **First steps and the server page** (P8). The server page leads with… | [features.md](features.md) |
| 87 | Notes | - **Secret Santa** (`extensions/secretsanta/`, `features/secretsanta/`… | [features.md](features.md) |
| 88 | Notes | - **Community+** (P11). *Custom commands* (A9, `features/customcommand… | [features.md](features.md) |
| 89 | Notes | - **Stored keys are values, not field names**, when users choose them:… | [data-and-retention.md](data-and-retention.md) |
| 90 | Notes | - Several extensions are tied to dated events (olympics → Milan-Cortin… | [extension-lifecycle.md](extension-lifecycle.md) |
| 91 | Notes | - **Prometheus metrics** (`extensions/metrics.py`, global section `met… | [observability.md](observability.md) |
