# Michel

[![License](https://img.shields.io/badge/license-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/python-3.12%2B-brightgreen.svg)](https://www.python.org/)
[![interactions.py](https://img.shields.io/badge/interactions.py-latest-blueviolet.svg)](https://github.com/interactions-py/interactions.py)
[![CI](https://github.com/LightD31/michel-discord-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/LightD31/michel-discord-bot/actions/workflows/ci.yml)
[![Docker](https://github.com/LightD31/michel-discord-bot/actions/workflows/docker.yml/badge.svg)](https://github.com/LightD31/michel-discord-bot/actions/workflows/docker.yml)

A modular, multi-guild Discord bot built with **interactions.py**. Michel ships with a plugin-based architecture where each feature is an independently loadable extension, a per-guild MongoDB backend, and an optional FastAPI web dashboard for live configuration. Most user-facing strings are in French.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Extensions](#extensions)
- [Web UI](#web-ui)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## Features

35 extensions are auto-discovered from `extensions/` at startup. Each one can be toggled per server from the Web UI.

### Community & engagement

| Module | Description |
|--------|-------------|
| **XP & Levels** | Message- and voice-based XP with cooldowns, level-up announcements, Pillow rank cards, and a paginated leaderboard. |
| **Birthday** | Store birthdays with timezone support, send daily greetings, and assign a birthday role. |
| **Welcome** | Weighted-random welcome and farewell messages, with an optional illustrated welcome card. |
| **Feur** | Classic French joke — the bot replies "feur" when someone ends a message with "quoi", with per-user stats. |
| **Starboard** | Mirrors highly-reacted messages to a dedicated channel once a reaction threshold is hit. |
| **Suggestions** | Community suggestion box with up/down voting and approve/deny/implement workflows. |
| **Polls** | `/poll`, `/poll-anonyme`, and `/poll-classement` (ranked-choice) — button-based, persisted across restarts, with optional auto-close. |
| **Giveaway** | Reaction-entry giveaways with a scheduled random draw. |
| **Secret Santa** | Organize a Secret Santa entirely in Discord — registrations, forbidden pairings, random draw, and DM delivery. |
| **Reaction Roles** | Self-assignable roles via persistent buttons, with a menu builder in the Web UI. |
| **Reminders** | `/reminder` with optional recurrence and extra recipients, DM delivery with a snooze button, persisted and restored across restarts. |
| **Random** | `/pick` (random choice from a list) and `/dice` (configurable die roll) powered by Random.org. |
| **User Info** | Per-user profile lookup and shared member-tracking stats. |
| **Tricount** | Shared expense tracker — groups, expenses, recurring charges, balances, and chart reports. |

### Moderation & administration

| Module | Description |
|--------|-------------|
| **Moderation** | `/warn`, `/timeout`, `/kick`, `/ban`, `/unban`, numbered infraction history, a modlog channel, and automod (anti-invite / anti-spam / banned-words). Infractions are browsable and revocable from the Web UI. |
| **Admin** | Moderator utilities: `/ping`, `/delete`, `/send`, `/slowmode`, `/lock`, `/unlock`. |
| **Embed Manager** | Custom embeds created and managed from the Web UI, auto-published to a channel. |
| **Backup** | Daily (and on-demand `/backup`) JSON exports of all MongoDB databases with configurable retention. |

### Integrations & notifications

| Module | Description |
|--------|-------------|
| **Twitch** | Live notifications, stream status embeds, Discord scheduled-event sync, and emote sync via Twitch EventSub WebSocket. |
| **YouTube** | Polls YouTube channels every 5 min and posts new video links in a configured channel. |
| **Spotify** | Collaborative playlist management with song proposals, community voting, and playlist change tracking. OAuth re-authentication happens through the Web UI. |
| **RSS** | Generic feed poller (RSS / Atom / Steam / Epic / subreddit) with per-feed channel and message-template overrides. |
| **AI Compare** | Ask a question and compare answers from multiple LLMs via OpenRouter, then vote for the best. |
| **Confrérie** | Literary guild features backed by the Notion API — reading stats, challenges, publisher management. |
| **Zunivers / Coloc** | Daily reminders, event tracking, Hardcore season monitoring, corporation recaps, and Advent calendar for the Zunivers collectible game. |
| **VLR.gg Tracker** | Valorant esports match tracking — schedules, live score updates, and post-match results from VLR.gg. |
| **MDI Tracker** | Mythic Dungeon International (World of Warcraft) tracking via the Raider.IO API. |
| **Uptime** | Mirrors Uptime Kuma status updates into channels via Socket.IO — periodic status embeds and maintenance notifications. |
| **Minecraft** | Server status monitoring and player stats via SFTP/RCON. *(disabled by default)* |
| **Satisfactory** | Game server status via pyfactorybridge. |

### Event trackers (time-bound)

| Module | Description |
|--------|-------------|
| **Zevent** | Zevent 2025 charity marathon tracker — amount raised, streamer status, milestones. *(disabled by default)* |
| **Olympics** | Medal tracking for the Milan-Cortina 2026 Winter Olympics. *(disabled by default)* |
| **Speedons** | Speedons speedrun charity marathon tracker. *(disabled by default)* |
| **Streamlabs Charity** | Charity campaign amount & streamer status tracker. |

---

## Architecture

The codebase (~33k lines) is split into three top-level layers, each with a clear role:

```
main.py                 # Entry point — discovers & loads extensions, starts client,
                        # heartbeat task + watchdog thread, optional Web UI

extensions/             # Discord-facing layer — one file or package per feature
├── birthday.py         #   single-file extension
├── xp/                 #   package extension: mixins composed in __init__.py
│   ├── __init__.py     #     Extension subclass + setup(bot)
│   ├── _common.py      #     shared constants + Pydantic Web UI schema
│   ├── leveling.py     #     one mixin per concern
│   ├── voice.py
│   ├── commands.py
│   └── leaderboard.py
└── …                   #   35 extensions total (see Features above)

features/               # Domain logic — pure Python, no Discord imports
├── xp/                 #   level curve, TTL cache, rank card, repository
├── moderation/         #   duration parsing, automod filters, models, repository
├── rss/                #   feed parser, network, repository
└── …                   #   one package per feature; each owns its repository.py

src/                    # Shared infrastructure
├── core/               # Framework-free essentials (strictly typed under mypy)
│   ├── config.py       #   reactive ConfigStore with atomic writes
│   ├── db.py           #   MongoManager singleton (motor, per-event-loop clients)
│   ├── http.py         #   shared aiohttp session with retry/backoff + URL redaction
│   ├── logging.py      #   colored ANSI logger factory
│   ├── errors.py       #   exception hierarchy (BotError, HttpError, …)
│   ├── images.py       #   Pillow helpers (dynamic text images)
│   └── text.py         #   markdown escaping, weighted message picker, text utils
├── discord_ext/        # interactions.py-dependent UI helpers
│   ├── embeds.py       #   color palette, spacer field, timestamp formatter
│   ├── messages.py     #   send_error/success, persistent-message bootstrap
│   ├── autocomplete.py #   shared autocomplete handlers + enabled-guild check
│   └── paginator.py    #   CustomPaginator + reaction-poll formatter
├── integrations/       # Pure external-API clients (no Discord imports)
│   ├── spotify.py      #   Spotipy auth with lazy client, vote counting
│   ├── notion.py       #   Notion API client
│   └── minecraft_rcon.py
├── webui/              # FastAPI dashboard
│   ├── app.py          #   router assembly + session restore on startup
│   ├── auth.py         #   Discord OAuth2, CSRF state, session persistence
│   ├── sessions.py     #   MongoDB-backed sessions with TTL index
│   ├── context.py      #   WebUIContext + authorization helpers
│   ├── schemas.py      #   @register_module / @register_section Pydantic registry
│   ├── routes/         #   auth, bot, config, extensions, servers, rolemenus,
│   │                   #   moderation, spotify, frontend (SPA catch-all)
│   ├── sse/            #   live log streaming over Server-Sent Events
│   ├── log_handler.py  #   in-memory ring buffer feeding the log view
│   └── static/         #   single-page app (index.html)
└── assets/             # Bundled fonts used by image rendering

tests/                  # pytest suite (core, webui auth/config, feature logic)
scripts/                # generate_config_example.py — config skeleton from schemas
grafana/                # standalone Grafana dashboard exports (spotify, xp)
```

**Key design choices:**

- **Three-layer split** — `extensions/` owns Discord I/O, `features/` owns domain logic and persistence, `src/` owns shared infrastructure. Features are unit-testable without importing `interactions`.
- **Per-guild isolation** — Each Discord server has its own MongoDB database (`guild_{id}`), plus a shared `global` database.
- **Auto-discovered extensions** — Extensions are loaded at startup unless disabled via the Web UI or `config["extensions"]`.
- **Mixin-based extensions** — Larger extensions (xp, twitch, uptime, zevent, tricount…) are split into mixin classes by concern and composed in the package's `__init__.py`.
- **Schema-driven dashboard** — Each module declares a Pydantic config schema; the Web UI renders typed forms, per-server toggles, and validation from it.
- **Async everywhere** — Motor for MongoDB, aiohttp for HTTP, asyncssh for SFTP, native async RCON. The bot loop and the Web UI loop each get their own motor/aiohttp clients.
- **Self-healing** — A heartbeat file plus a watchdog thread make the process exit when the gateway wedges, so Docker's restart policy brings it back.

---

## Getting Started

### Prerequisites

- Python 3.12+ (CI and the Docker image run 3.14)
- A running MongoDB instance
- A Discord bot token

### Docker Compose (recommended)

Images are published to GHCR (`ghcr.io/lightd31/michel-discord-bot`) with SBOM and provenance attestations.

```bash
git clone https://github.com/LightD31/michel-discord-bot.git
cd michel-discord-bot
# Create and edit your configuration
mkdir -p config
cp config.example.json config/config.json   # generated skeleton — fill in the secrets
# (regenerate the skeleton after schema changes: python scripts/generate_config_example.py)
# Launch
docker compose up -d
```

Volumes:
- `./config` → `/app/config` (configuration files)
- `./data` → `/app/data` (persistent data, backups)
- `./logs` → `/app/logs` (log files)

The Web UI is bound to `127.0.0.1:8080` — put a TLS-terminating reverse proxy in front of it. The container runs as a non-root user and has a healthcheck wired to the bot's heartbeat file, so a wedged gateway connection triggers an automatic restart (`restart: unless-stopped`).

### Local Development

```bash
git clone https://github.com/LightD31/michel-discord-bot.git
cd michel-discord-bot
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# Install pinned runtime deps + dev tooling (ruff, mypy, pytest, pre-commit, detect-secrets)
pip install -r requirements.txt -e ".[dev]"
pre-commit install

python main.py
```

Runtime dependencies are declared (as ranges) in `pyproject.toml` and locked
to exact versions in `requirements.txt`, which is what the Docker image and CI
install from. After changing dependencies in `pyproject.toml`, regenerate the
lock with:

```bash
uv pip compile pyproject.toml -o requirements.txt --universal
```

CI fails if the lockfile drifts from `pyproject.toml`.

---

## Configuration

All configuration lives in `config/config.json`, with two top-level keys:

- **`config`** — global sections: `discord` (token, IDs), `mongodb`, `webui`, `backup`, plus API credentials for integrations (Spotify, Twitch, YouTube, Notion, OpenRouter, Uptime Kuma, Random.org, Shlink…) and an optional `extensions` map of `"extensions.<name>": bool` to explicitly enable or disable discovered extensions.
- **`servers`** — per-guild module configs keyed by guild ID (`moduleXp`, `moduleBirthday`, …, each with an `enabled` flag), plus a `discord2name` display-name mapping.

Config is loaded through `src.core.config`. Extensions call `load_config("moduleName")`, which returns the global config, the per-guild config for that module, and the list of guilds where the module is enabled. The store is reactive (atomic writes + subscriber notifications), so the Web UI and running extensions stay in sync without restarts.

`config.example.json` is generated from the registered Pydantic schemas by `scripts/generate_config_example.py` — regenerate it after adding or changing schemas.

---

## Extensions

An extension is either a single Python file (`extensions/myext.py`) or a package (`extensions/myext/__init__.py`) containing an `interactions.py` `Extension` subclass. To create a new one:

1. Create `extensions/myext.py` (or `extensions/myext/__init__.py` for a multi-file package).
2. Define an `Extension` subclass.
3. Expose a module-level `setup(bot)` function that instantiates it.
4. Declare a Pydantic config schema with `@register_module("moduleMyext")` so it shows up in the Web UI.
5. Restart the bot — the extension will be auto-loaded.

To disable, use the Web UI or set `"extensions.myext": false` in `config.json`. (Entries whose name starts with `_` are disabled by default; none currently exist.)

**Composition convention** — When an extension grows beyond ~200 lines, promote it to a package and split responsibilities into mixin modules (`leveling.py`, `commands.py`, `leaderboard.py`, …) assembled via multiple inheritance in `__init__.py`. Shared constants and the Pydantic config schema go in `_common.py`. Domain logic (persistence, pure functions, API clients) moves into a matching `features/<name>/` package so it can be tested without Discord.

A few extensions intentionally share config rather than registering their own module: `polls` and `reminders` read `moduleUtils` (owned by `admin`), and `coloc` rides on `moduleZunivers`.

---

## Web UI

An optional FastAPI-based dashboard, enabled when `webui.enabled` is `true` in config. It runs in a daemon thread alongside the bot.

- **Authentication** — Discord OAuth2 with CSRF-protected state and MongoDB-persisted sessions (TTL-indexed, httponly cookies).
- **Authorization tiers** — Any authenticated user sees the guilds they manage (`MANAGE_GUILD`/`ADMINISTRATOR` or guild owner); user IDs listed in `webui.developerUserIds` additionally get global config, extension reload, and live logs.
- **Schema-driven forms** — Module and global config forms are generated from the Pydantic schemas registered via `@register_module` / `@register_section` in `src/webui/schemas.py`.
- **Custom views** — Reaction-role menu builder (`routes/rolemenus.py`), moderation infraction browser (`routes/moderation.py`), and Spotify OAuth management (`routes/spotify.py`) go beyond plain forms and call back into Discord through the bot client.
- **Live logs** — Streaming over Server-Sent Events from an in-memory ring buffer (developer-only).
- **Frontend** — A single-page app in `src/webui/static/index.html`; `routes/frontend.py` catch-alls unknown paths back to it.

---

## Development

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Bot framework | [interactions.py](https://github.com/interactions-py/interactions.py) (`discord-py-interactions`) |
| Database | MongoDB via [Motor](https://motor.readthedocs.io/) (async) |
| Web UI | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) + [sse-starlette](https://github.com/sysid/sse-starlette) |
| Config validation | [Pydantic v2](https://docs.pydantic.dev/) |
| Spotify | [Spotipy](https://spotipy.readthedocs.io/) |
| Twitch | [twitchAPI](https://pytwitchapi.dev/) (EventSub) |
| Notion | [notion-client](https://github.com/ramnes/notion-sdk-py) |
| AI | [OpenRouter](https://openrouter.ai/) via the OpenAI SDK |
| Minecraft | mcstatus, asyncssh, native RCON |
| Monitoring | Uptime Kuma (Socket.IO), Grafana dashboard exports in `grafana/` |
| Image gen | Pillow |

### Tooling

- **Lint & format** — [ruff](https://docs.astral.sh/ruff/) (`ruff check`, `ruff format`).
- **Type checking** — [mypy](https://mypy-lang.org/) (`mypy src`). `src.core.*` is strict; the rest is lenient during incremental typing adoption. Runs in CI on every PR, and locally as an opt-in hook: `pre-commit run mypy --hook-stage manual`.
- **Tests** — [pytest](https://docs.pytest.org/) with `pytest-asyncio` (auto mode) and coverage over `src` + `features`. The suite covers core infrastructure (db, http, config saving), Web UI auth (OAuth, sessions, CSRF), and feature logic (giveaway draw, moderation, MDI client, RSS parser, weighted messages).
- **Pre-commit** — ruff (lint + format), [detect-secrets](https://github.com/Yelp/detect-secrets) (against `.secrets.baseline`), and hygiene hooks (whitespace, JSON/YAML validity, large files, merge conflicts).
- **CI** (`.github/workflows/ci.yml`) — six jobs: ruff lint/format, mypy, pytest with coverage (per-file table in the run summary, XML artifact kept 14 days), lockfile drift check, `pip-audit` CVE scan, and a detect-secrets sweep. Also runs on a weekly schedule so CVE scanning doesn't stall between pushes.
- **Docker** (`.github/workflows/docker.yml`) — multi-stage build published to GHCR with provenance and SBOM attestations.
- **Dependabot** — weekly grouped updates for pip, Docker base image, and GitHub Actions.

### Project Conventions

- Python 3.12+ (`requires-python >= 3.12`; CI/Docker run 3.14), async/await throughout.
- **Layering** — Discord code in `extensions/`, domain logic in `features/`, shared infrastructure in `src/`. `features/` must not import `interactions`.
- **Persistence** — One MongoDB database per guild (`guild_{id}`), shared data in `global`. Each feature owns a repository module under `features/<name>/repository.py`.
- **Logging** — `src.core.logging.init_logger(name)` (colored ANSI output; `MICHEL_DEBUG=1` for debug level).
- **HTTP** — Use the shared session from `src.core.http` rather than creating per-call aiohttp clients.
- **Language** — User-facing strings (command descriptions, embeds) are in French.

---

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request.

## License

This project is licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0).
