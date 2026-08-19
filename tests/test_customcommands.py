"""Tests for per-guild custom slash commands.

Two halves: the pure parser in ``features/customcommands`` (what a guild's
config list turns into), and the extension that registers the survivors as
real guild-scoped slash commands.
"""

import json
import random

import pytest
from interactions import Client

from extensions.customcommands import CustomCommandsExtension
from features.customcommands import (
    MAX_COMMANDS_PER_GUILD,
    MAX_RESPONSE_LENGTH,
    CustomCommand,
    normalize_name,
    parse_commands,
    pick_response,
)
from src.core import config as core_config

GUILD = "123456789012345678"


# ── Parser ──────────────────────────────────────────────────────────


def test_parses_a_full_entry():
    parsed = parse_commands(
        [{"name": "fesse", "description": "Fesses", "responses": ["https://a/gif.gif", "paf"]}]
    )
    assert parsed.errors == []
    assert parsed.commands == [
        CustomCommand(name="fesse", description="Fesses", responses=["https://a/gif.gif", "paf"])
    ]


def test_normalizes_names_and_defaults_the_description():
    parsed = parse_commands([{"name": "  Massage Du Cul ", "responses": ["ok"]}])
    assert [c.name for c in parsed.commands] == ["massage-du-cul"]
    assert parsed.commands[0].description  # never empty — Discord requires one


def test_normalize_name_is_idempotent():
    assert normalize_name("Massage  Du Cul") == normalize_name("massage-du-cul") == "massage-du-cul"
    assert normalize_name(None) == ""


@pytest.mark.parametrize(
    "entry",
    [
        {"responses": ["ok"]},  # no name
        {"name": "a" * 33, "responses": ["ok"]},  # too long for Discord
        {"name": "sa!ut", "responses": ["ok"]},  # illegal character
        {"name": "vide", "responses": []},  # nothing to answer with
        {"name": "vide", "responses": ["   "]},  # blank response
        {"name": "trop-long", "responses": ["x" * (MAX_RESPONSE_LENGTH + 1)]},
        "pas-un-objet",
    ],
)
def test_drops_unusable_entries_with_a_reason(entry):
    parsed = parse_commands([entry])
    assert parsed.commands == []
    assert len(parsed.errors) == 1


def test_tolerates_a_non_list_config():
    assert parse_commands(None).commands == []
    assert parse_commands({"name": "fesse"}).commands == []


def test_accepts_string_and_object_responses():
    parsed = parse_commands(
        [{"name": "fesse", "responses": "seule"}, {"name": "autre", "responses": [{"text": "obj"}]}]
    )
    assert [c.responses for c in parsed.commands] == [["seule"], ["obj"]]


def test_deduplicates_responses_and_names():
    parsed = parse_commands(
        [
            {"name": "fesse", "responses": ["a", "a", "b"]},
            {"name": "FESSE", "responses": ["c"]},
        ]
    )
    assert [c.responses for c in parsed.commands] == [["a", "b"]]
    assert any("double" in e for e in parsed.errors)


def test_skips_names_already_used_by_the_bot():
    parsed = parse_commands([{"name": "ping", "responses": ["pong"]}], reserved_names={"ping"})
    assert parsed.commands == []
    assert "déjà utilisé" in parsed.errors[0]


def test_caps_the_number_of_commands_per_guild():
    entries = [{"name": f"cmd{i}", "responses": ["ok"]} for i in range(MAX_COMMANDS_PER_GUILD + 3)]
    parsed = parse_commands(entries)
    assert len(parsed.commands) == MAX_COMMANDS_PER_GUILD
    assert len(parsed.errors) == 3


def test_pick_response_only_returns_configured_responses():
    command = CustomCommand(name="fesse", description="d", responses=["a", "b"])
    picks = {pick_response(command, random.Random(seed)) for seed in range(20)}
    assert picks and picks <= {"a", "b"}


# ── Extension registration ──────────────────────────────────────────


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point ``load_config`` at a throwaway config file."""

    def write(servers: dict) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"config": {}, "servers": servers}), encoding="utf-8")
        monkeypatch.setattr(core_config, "CONFIG_PATH", str(path))

    return write


def make_bot() -> Client:
    return Client(token="x")


def test_registers_configured_commands_for_each_guild(config_file):
    config_file(
        {
            GUILD: {
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [{"name": "fesse", "description": "Fesses", "responses": ["paf"]}],
                }
            },
            "999": {"moduleCustomCommands": {"enabled": False, "commands": []}},
        }
    )
    bot = make_bot()
    extension = CustomCommandsExtension(bot)
    extension._register_all()

    scope = bot.interactions_by_scope[int(GUILD)]
    assert set(scope) == {"fesse"}
    assert str(scope["fesse"].description) == "Fesses"
    assert 999 not in bot.interactions_by_scope  # module disabled there


def test_does_not_shadow_an_existing_command(config_file):
    config_file(
        {
            GUILD: {
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [{"name": "journa", "responses": ["nope"]}],
                }
            }
        }
    )
    bot = make_bot()

    async def builtin(ctx):
        pass

    from interactions import SlashCommand

    bot.add_interaction(
        SlashCommand(name="journa", description="Built-in", scopes=[GUILD], callback=builtin)
    )
    extension = CustomCommandsExtension(bot)
    extension._register_all()

    assert str(bot.interactions_by_scope[int(GUILD)]["journa"].description) == "Built-in"


def test_drop_unregisters_what_it_added(config_file):
    config_file(
        {
            GUILD: {
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [{"name": "fesse", "responses": ["paf"]}],
                }
            }
        }
    )
    bot = make_bot()
    extension = CustomCommandsExtension(bot)
    extension._register_all()
    assert "fesse" in bot.interactions_by_scope[int(GUILD)]

    extension.drop()
    assert "fesse" not in bot.interactions_by_scope[int(GUILD)]


@pytest.mark.asyncio
async def test_the_registered_command_answers_with_a_configured_response(config_file, monkeypatch):
    config_file(
        {
            GUILD: {
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [{"name": "fesse", "responses": ["paf", "pif"]}],
                }
            }
        }
    )
    # Shortening is a no-op here — the point is what reaches ctx.send().
    monkeypatch.setattr("extensions.customcommands.shorten_text", lambda text: _identity(text))

    bot = make_bot()
    extension = CustomCommandsExtension(bot)
    extension._register_all()
    command = bot.interactions_by_scope[int(GUILD)]["fesse"]

    sent: list[str] = []

    class FakeContext:
        async def send(self, content):
            sent.append(content)

    await command.call_callback(command.callback, FakeContext())
    assert sent[0] in ("paf", "pif")


async def _identity(text: str) -> str:
    return text
