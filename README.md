# Michel

[![License](https://img.shields.io/badge/license-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/python-3.12-brightgreen.svg)](https://www.python.org/)
[![interactions.py](https://img.shields.io/badge/interactions.py-latest-blueviolet.svg)](https://github.com/interactions-py/interactions.py)
[![CI](https://github.com/LightD31/michel-discord-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/LightD31/michel-discord-bot/actions/workflows/ci.yml)
[![Docker](https://github.com/LightD31/michel-discord-bot/actions/workflows/docker.yml/badge.svg)](https://github.com/LightD31/michel-discord-bot/actions/workflows/docker.yml)

A modular, multi-guild Discord bot built with **interactions.py**. Michel ships with a plugin-based architecture where each feature is an independently loadable extension, a per-guild MongoDB backend, and an optional FastAPI web dashboard for live configuration.

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

| Module | Description |
|--------|-------------|
| **AI Compare** | Ask a question and compare answers from multiple LLMs (GPT, Claude, DeepSeek, Gemini, Grok…) via OpenRouter, then vote for the best. |
| **Twitch** | Live notifications, stream status embeds, and Discord scheduled-event sync via Twitch EventSub WebSocket. |
| **YouTube** | Polls YouTube channels every 5 min and posts new video links in a configured channel. |
| **Spotify** | Collaborative playlist management with song proposals, a community voting system, and playlist change tracking. |
| **Secret Santa** | Organize a Secret Santa entirely in Discord — registrations, forbidden pairings, random draw, and DM delivery. |
| **XP & Levels** | Per-message XP gain (cooldown-based), level-up announcements, and a paginated leaderboard. |
| **Birthday** | Store birthdays with timezone support, send daily greetings, and assign a birthday role. |
| **Welcome** | Weighted-random welcome and farewell messages. |
| **Minecraft** *(disabled)* | Server status monitoring, player stats via SFTP/RCON. |
| **Olympics** *(disabled)* | Medal tracking for the Milan-Cortina 2026 Winter Olympics. |
| **Satisfactory** *(disabled)* | Game server status via pyfactorybridge. |
| **Speedons** *(disabled)* | Speedrun charity marathon tracker. |
| **Streamlabs Charity** *(disabled)* | Charity campaign amount & streamer status tracker. |
| **Zevent** *(disabled)* | Zevent 2025 charity marathon tracker (amount raised, streamer status, milestones). |
| **Random** | `/pick` (random choice from a list) and `/dice` (configurable die roll) powered by Random.org. |
| **Feur** | Classic French joke — the bot replies "feur" when someone ends a message with "quoi", with per-user stats. |
| **Tricount** | Shared expense tracker — create groups, log expenses, and compute balances. |
| **Confrérie** | Literary guild features backed by the Notion API — reading stats, challenges, publisher management. |
| **Zunivers (Coloc)** | Daily reminders, event tracking, Hardcore season monitoring, corporation recaps, and Advent calendar for the Zunivers collectible game. |
| **VLR.gg Tracker** | Valorant esports match tracking — schedules, live score updates, and post-match results from VLR.gg. |
| **Uptime** | Server monitoring via Uptime Kuma — periodic status embeds and maintenance notifications. |
| **Backup** | Scheduled (and on-demand) JSON backups of all MongoDB databases with configurable retention. |
| **Polls** | Reaction polls with vote tracking and result embeds. |
| **Reminders** | `/remind` scheduled reminders, persisted and restored across restarts. |
| **Admin** | Owner/admin utilities: `/ping`, `/delete`, `/send`, and the global embed manager. |
| **User Info** | Per-user profile lookup and shared user stats. |

---

## Architecture

The codebase is split into three top-level layers, each with a clear role:

```
main.py                 # Entry point — discovers & loads extensions, starts client & optional Web UI

extensions/             # Discord-facing layer — one package (or file) per feature
├── admin/              # /ping, /delete, /send, embedmanager
├── backup.py
├── birthday.py
├── compareai/          # AI Compare (ai_client, voting)
├── confrerie/          # Confrérie (stats, requests, updates, editors)
├── coloc/              # Zunivers integration entry point
├── feur.py
├── minecraft/          # (disabled) stats, status
├── olympics/           # (disabled) medals, tasks
├── polls/
├── random_.py
├── reminders/
├── secretsanta/        # sessions, bans, draws, buttons
├── spotify/            # auth, playlist, votes
├── tricount/           # groups, expenses, reports
├── twitch/             # eventsub, notifications, schedule, emotes
├── uptime/             # monitors, notifications, tasks, socketio_client
├── userinfo.py
├── vlrgg/              # api, embeds, notifications
├── welcome.py
├── xp/                 # leveling, commands, leaderboard
├── youtube.py
├── zevent/             # (disabled)
└── zunivers/           # reminders, events, corporation

features/               # Domain logic — pure Python, no Discord imports
├── birthday/           # models + repository
├── coloc/              # Zunivers API client, models, storage
├── feur/
├── messages/           # phrase dictionaries (welcome, feur, level-up…)
├── minecraft/          # SFTP-based stats reader, tuning constants
├── polls/              # constants & helpers
├── random/             # random-org helpers
├── reminders/          # reminder repository
├── secretsanta/        # pairing algorithm, repository
├── uptime/             # model + repository
├── userinfo/           # shared user profile repository
├── vlrgg/              # VLR.gg HTTP client (cached)
└── xp/                 # level curve, TTL cache, XP repository

src/                    # Shared infrastructure
├── core/               # Framework-free essentials
│   ├── config.py       #   reactive JSON config store
│   ├── db.py           #   MongoDB singleton (motor async)
│   ├── http.py         #   shared aiohttp session with retry/fetch helpers
│   ├── logging.py      #   colored ANSI logger factory
│   ├── errors.py       #   base exception hierarchy
│   ├── images.py       #   Pillow helpers (rank cards, etc.)
│   ├── text.py         #   markdown escaping & text utilities
│   └── migrations.py   #   config-key migrations run at startup
├── discord_ext/        # interactions.py-dependent UI helpers
│   ├── embeds.py       #   color palette, spacer field, timestamp formatter
│   ├── messages.py     #   send_error/success, persistent-message bootstrap
│   ├── autocomplete.py #   shared autocomplete handlers + enabled-guild check
│   └── paginator.py    #   CustomPaginator + reaction-poll formatter
├── integrations/       # Pure external-API clients (no Discord imports)
│   ├── spotify.py      #   Spotipy auth, embed builders, vote counting
│   ├── notion.py       #   Notion API client
│   └── minecraft_rcon.py
├── webui/              # FastAPI dashboard
│   ├── app.py, server.py, auth.py, context.py
│   ├── schemas.py      #   per-module JSON schemas for dynamic forms
│   ├── routes/         #   auth, bot, config, extensions, servers, frontend
│   ├── sse/            #   live log streaming over Server-Sent Events
│   ├── log_handler.py
│   └── static/
└── assets/             # Bundled fonts used by image rendering
```

**Key design choices:**

- **Three-layer split** — `extensions/` owns Discord I/O, `features/` owns domain logic and persistence, `src/` owns shared infrastructure. Features are unit-testable without importing `interactions`.
- **Per-guild isolation** — Each Discord server has its own MongoDB database (`guild_{id}`), plus a shared `global` database.
- **Hot-loadable extensions** — Extensions are auto-discovered at startup and loaded unless explicitly disabled via the Web UI or `config["extensions"]`.
- **Mixin-based extensions** — Larger extensions (xp, zunivers, twitch, uptime, secretsanta…) are split into mixin classes by concern and composed in the package's `__init__.py`.
- **Async everywhere** — Motor for MongoDB, aiohttp for HTTP, asyncssh for SFTP, native async RCON.

---

## Getting Started

### Prerequisites

- Python 3.12+
- A running MongoDB instance
- A Discord bot token

### Docker Compose (recommended)

```bash
git clone https://github.com/LightD31/michel-discord-bot.git
cd michel-discord-bot
# Create and edit your configuration
mkdir -p config
cp config/config.example.json config/config.json   # if an example exists, or create manually
# Launch
docker compose up -d
```

Volumes:
- `./config` → `/app/config` (configuration files)
- `./data` → `/app/data` (persistent data, backups)
- `./logs` → `/app/logs` (log files)

The Web UI is exposed on port **8080** by default.

### Local Development

```bash
git clone https://github.com/LightD31/michel-discord-bot.git
cd michel-discord-bot
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# Install runtime + dev tooling (ruff, mypy, pytest, pre-commit, detect-secrets)
pip install -e ".[dev]"
pre-commit install

python main.py
```

Runtime dependencies are declared in `pyproject.toml`, which is the single
source of truth for both local installs and the Docker image.

---

## Configuration

All configuration lives in `config/config.json`. The top-level structure contains:

- **`discord`** — `botToken`, `devGuildId`.
- **`webui`** — `enabled`, `host`, `port`, OAuth2 settings.
- **`extensions`** — optional map of `"extensions.<name>": bool` to explicitly enable or disable discovered extensions (overrides the underscore-prefix default).
- **Per-module configs** — Each extension reads its own section via `load_config("module_name")`, which returns the global config, the per-guild config for that module, and the list of guilds where the module is enabled.

Config is loaded through `src.core.config`, which provides atomic writes and a reactive `ConfigStore` so the Web UI and running extensions stay in sync. A `migrate_config_module_keys()` pass runs at startup to rewrite legacy key names.

Modules can be toggled per server through the Web UI or directly in the JSON file.

---

## Extensions

An extension is either a single Python file (`extensions/myext.py`) or a package (`extensions/myext/__init__.py`) containing an `interactions.py` `Extension` subclass. To create a new one:

1. Create `extensions/myext.py` (or `extensions/myext/__init__.py` for a multi-file package).
2. Define an `Extension` subclass.
3. Expose a module-level `setup(bot)` function that instantiates it.
4. Restart the bot — the extension will be auto-loaded.

To disable, use the Web UI or set `"extensions.myext": false` in `config.json`.

**Composition convention** — When an extension grows beyond ~200 lines, promote it to a package and split responsibilities into mixin modules (`leveling.py`, `commands.py`, `leaderboard.py`, …) assembled via multiple inheritance in `__init__.py`. Shared constants and the Pydantic config schema go in `_common.py`. Domain logic (persistence, pure functions, API clients) moves into a matching `features/<name>/` package so it can be tested without Discord.

---

## Web UI

An optional FastAPI-based dashboard, enabled when `webui.enabled` is `true` in config.

- **Authentication** — Discord OAuth2 restricted to a list of admin user IDs.
- **Features** — Toggle modules per server, edit module and global configuration through dynamic forms (powered by the JSON schemas in `src/webui/schemas.py`), and stream live logs via Server-Sent Events.
- **Routes** — Split across `src/webui/routes/` (`auth`, `bot`, `config`, `extensions`, `servers`, `frontend`).
- **Stack** — FastAPI + Uvicorn, served in a daemon thread alongside the bot.

---

## Development

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Bot framework | [interactions.py](https://github.com/interactions-py/interactions.py) |
| Database | MongoDB via [Motor](https://motor.readthedocs.io/) (async) |
| Web UI | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) + [sse-starlette](https://github.com/sysid/sse-starlette) |
| Config validation | [Pydantic v2](https://docs.pydantic.dev/) |
| Spotify | [Spotipy](https://spotipy.readthedocs.io/) |
| Twitch | [twitchAPI](https://pytwitchapi.dev/) (EventSub) |
| Notion | [notion-client](https://github.com/ramnes/notion-sdk-py) |
| AI | [OpenRouter](https://openrouter.ai/) (OpenAI, Anthropic, DeepSeek, Gemini, Grok) |
| Minecraft | mcstatus, asyncssh, native RCON |
| Monitoring | Uptime Kuma (SocketIO) |
| Image gen | Pillow |

### Tooling

- **Lint & format** — [ruff](https://docs.astral.sh/ruff/) (`ruff check`, `ruff format`).
- **Type checking** — [mypy](https://mypy-lang.org/). `src.core.*` is strict; the rest is lenient during incremental typing adoption.
- **Tests** — [pytest](https://docs.pytest.org/) with `pytest-asyncio` (auto mode). Tests live in `tests/`.
- **Pre-commit** — ruff, mypy, and [detect-secrets](https://github.com/Yelp/detect-secrets) run on every commit.

### Project Conventions

- Python 3.12, async/await throughout.
- **Layering** — Discord code in `extensions/`, domain logic in `features/`, shared infrastructure in `src/`. `features/` must not import `interactions`.
- **Persistence** — One MongoDB database per guild (`guild_{id}`), shared data in `global`. Each feature owns a repository module under `features/<name>/repository.py`.
- **Logging** — `src.core.logging.init_logger(name)` (colored ANSI output).
- **HTTP** — Use the shared session from `src.core.http` rather than creating per-call aiohttp clients.

---

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request.

## License

This project is licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0).
