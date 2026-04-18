# Michel

[![License](https://img.shields.io/badge/license-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/python-3.12-brightgreen.svg)](https://www.python.org/)
[![interactions.py](https://img.shields.io/badge/interactions.py-latest-blueviolet.svg)](https://github.com/interactions-py/interactions.py)

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
| **Random** | `/pick` (random choice from a list) and `/dice` (configurable die roll) powered by Random.org. |
| **Feur** | Classic French joke — the bot replies "feur" when someone ends a message with "quoi", with per-user stats. |
| **Tricount** | Shared expense tracker — create groups, log expenses, and compute balances. |
| **Confrérie** | Literary guild features backed by the Notion API — reading stats, challenges, publisher management. |
| **Zunivers (Coloc)** | Daily reminders, event tracking, Hardcore season monitoring, corporation recaps, and Advent calendar for the Zunivers collectible game. |
| **VLR.gg Tracker** | Valorant esports match tracking — schedules, live score updates, and post-match results from VLR.gg. |
| **Uptime** | Server monitoring via Uptime Kuma — periodic status embeds and maintenance notifications. |
| **Backup** | Scheduled (and on-demand) JSON backups of all MongoDB databases with configurable retention. |
| **Utilities** | `/ping`, `/delete`, `/send`, `/poll` (reaction polls with vote tracking), `/remind` (scheduled reminders). |

### Archived / Disabled Extensions

Extensions prefixed with `_` are not loaded automatically. They include:

- **Minecraft** — Server status monitoring, player stats via SFTP/RCON.
- **Olympics** — Medal tracking for the Milan-Cortina 2026 Winter Olympics.
- **Satisfactory** — Game server status via pyfactorybridge.
- **Speedons** — Speedrun charity marathon tracker.
- **Streamlabs Charity** — Charity campaign amount & streamer status tracker.
- **Zevent** — Zevent 2025 charity marathon tracker (amount raised, streamer status, milestones).

---

## Architecture

```
main.py                 # Entry point — loads extensions, starts client & optional Web UI
config.py               # DEBUG flag
dict.py                 # Random phrase dictionaries used by the bot

extensions/             # One file = one feature (auto-loaded unless prefixed with _)
src/
├── config_manager.py   # Loads config/config.json, filters per-guild module config
├── logutil.py          # Colored ANSI logging with CustomFormatter
├── mongodb.py          # MongoManager singleton — one DB per guild + a global DB (motor async)
├── utils.py            # Shared helpers (pagination, image gen, markdown escape, HTTP fetch…)
├── spotify.py          # Spotipy auth, embed builders, vote counting
├── vlrgg.py            # VLR.gg API client with TTL cache
├── raiderio.py         # Raider.IO Mythic+ leaderboard client
├── minecraft.py        # Minecraft player stats via SFTP + NBT parsing
├── minecraft_rcon.py   # Raw async RCON implementation
├── minecraft_config.py # Minecraft tuning constants
├── coloc/              # Zunivers integration (API client, models, storage)
└── webui/              # FastAPI dashboard (OAuth2, SSE logs, dynamic config forms)
```

**Key design choices:**

- **Per-guild isolation** — Each Discord server has its own MongoDB database (`guild_{id}`), plus a shared `global` database.
- **Hot-loadable extensions** — Extensions are discovered at startup by scanning `extensions/*.py`. Prefixing a file with `_` disables it.
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
- `./data` → `/app/data` (persistent data)
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

Runtime dependencies are declared in `pyproject.toml`. `requirements.txt` is
kept in sync for the Docker image until the Dockerfile is migrated to install
from the pyproject.

---

## Configuration

All configuration lives in `config/config.json`. The structure contains:

- **`discord`** — `botToken`, `devGuildId`.
- **`webui`** — `enabled`, `host`, `port`, OAuth2 settings.
- **Per-module configs** — Each extension reads its own section via `load_config("module_name")`, which returns the global config, the per-guild config for that module, and the list of guilds where the module is enabled.

Modules can be toggled per server through the Web UI or directly in the JSON file.

---

## Extensions

Every extension is a single Python file in the `extensions/` directory containing an `interactions.py` `Extension` subclass. To create a new extension:

1. Create `extensions/myext.py`.
2. Define an `Extension` subclass.
3. Add a `setup()` function at the module level.
4. Restart the bot — the extension will be auto-loaded.

Prefix the file with `_` to keep it in the repo without loading it.

---

## Web UI

An optional FastAPI-based dashboard available when `webui.enabled` is `true` in config.

- **Authentication** — Discord OAuth2 restricted to a list of admin user IDs.
- **Features** — Toggle modules per server, edit module and global configuration through dynamic forms (powered by JSON schemas in `src/webui/schemas.py`), live log streaming via SSE.
- **Stack** — FastAPI + Uvicorn, served in a daemon thread alongside the bot.

---

## Development

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Bot framework | [interactions.py](https://github.com/interactions-py/interactions.py) |
| Database | MongoDB via [Motor](https://motor.readthedocs.io/) (async) |
| Web UI | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| Spotify | [Spotipy](https://spotipy.readthedocs.io/) |
| Twitch | [twitchAPI](https://pytwitchapi.dev/) (EventSub) |
| AI | [OpenRouter](https://openrouter.ai/) (OpenAI, Anthropic, DeepSeek, Gemini, Grok) |
| Minecraft | mcstatus, asyncssh, native RCON |
| Monitoring | Uptime Kuma (SocketIO) |
| Image gen | Pillow |

### Project Conventions

- Python 3.12, async/await throughout.
- One extension per file in `extensions/`.
- Shared logic goes in `src/`.
- MongoDB: one database per guild (`guild_{id}`), global data in `global`.
- Logging via `src/logutil` (colored ANSI output).

---

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request.

## License

This project is licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0).
