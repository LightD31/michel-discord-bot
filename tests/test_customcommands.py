"""Tests for per-guild custom slash commands.

Two halves: the pure parser in ``features/customcommands`` (what a guild's
config list turns into), and the extension that registers the survivors as
real guild-scoped slash commands.
"""

import random

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole
from interactions import Client

from extensions.customcommands import CustomCommandsExtension
from features.customcommands import (
    MAX_COMMANDS_PER_GUILD,
    MAX_COOLDOWN_SECONDS,
    MAX_RESPONSE_LENGTH,
    Cooldowns,
    CustomCommand,
    Response,
    normalize_name,
    parse_commands,
    pick_response,
)
from features.customcommands.render import split_image

GUILD = "123456789012345678"


# ── Parser ──────────────────────────────────────────────────────────


def test_parses_a_full_entry():
    parsed = parse_commands(
        [
            {
                "name": "blague",
                "description": "Raconte une blague",
                "responses": ["https://a/gif.gif", "paf"],
            }
        ]
    )
    assert parsed.errors == []
    assert parsed.commands == [
        CustomCommand(
            name="blague",
            description="Raconte une blague",
            responses=["https://a/gif.gif", "paf"],
        )
    ]


def test_normalizes_names_and_defaults_the_description():
    parsed = parse_commands([{"name": "  Blague Du Jour ", "responses": ["ok"]}])
    assert [c.name for c in parsed.commands] == ["blague-du-jour"]
    assert parsed.commands[0].description  # never empty — Discord requires one


def test_normalize_name_is_idempotent():
    assert normalize_name("Blague  Du Jour") == normalize_name("blague-du-jour") == "blague-du-jour"
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
    assert parse_commands({"name": "blague"}).commands == []


def test_accepts_string_and_object_responses():
    parsed = parse_commands(
        [
            {"name": "blague", "responses": "seule"},
            {"name": "autre", "responses": [{"text": "obj"}]},
        ]
    )
    assert [c.responses for c in parsed.commands] == [["seule"], ["obj"]]


def test_deduplicates_responses_and_names():
    parsed = parse_commands(
        [
            {"name": "blague", "responses": ["a", "a", "b"]},
            {"name": "BLAGUE", "responses": ["c"]},
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
    command = CustomCommand(name="blague", description="d", responses=["a", "b"])
    picks = {pick_response(command, random.Random(seed)) for seed in range(20)}
    assert picks and picks <= {"a", "b"}


# ── Extension registration ──────────────────────────────────────────


@pytest.fixture
def config_file(store_config):
    """Serve these servers' config from the config store."""

    def write(servers: dict) -> None:
        store_config({"config": {}, "servers": servers})

    return write


def make_bot() -> Client:
    return Client(token="x")


def test_registers_configured_commands_for_each_guild(config_file):
    config_file(
        {
            GUILD: {
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [
                        {
                            "name": "blague",
                            "description": "Raconte une blague",
                            "responses": ["paf"],
                        }
                    ],
                }
            },
            "999": {"moduleCustomCommands": {"enabled": False, "commands": []}},
        }
    )
    bot = make_bot()
    extension = CustomCommandsExtension(bot)
    extension._register_all()

    scope = bot.interactions_by_scope[int(GUILD)]
    assert set(scope) == {"blague"}
    assert str(scope["blague"].description) == "Raconte une blague"
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
                    "commands": [{"name": "blague", "responses": ["paf"]}],
                }
            }
        }
    )
    bot = make_bot()
    extension = CustomCommandsExtension(bot)
    extension._register_all()
    assert "blague" in bot.interactions_by_scope[int(GUILD)]

    extension.drop()
    assert "blague" not in bot.interactions_by_scope[int(GUILD)]


def registered(config_file, monkeypatch, **command):
    """Register one command from *command*'s settings and return it."""
    config_file(
        {
            GUILD: {
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [{"name": "blague", "responses": ["paf"], **command}],
                }
            }
        }
    )
    # Shortening is a no-op here: the point is what reaches ctx.send().
    monkeypatch.setattr("extensions.customcommands.shorten_text", _identity)
    monkeypatch.setattr("extensions.customcommands.COOLDOWNS", Cooldowns(clock=CLOCK.read))
    bot = make_bot()
    CustomCommandsExtension(bot)._register_all()
    return bot.interactions_by_scope[int(GUILD)][command.get("name", "blague")]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def read(self) -> float:
        return self.now


CLOCK = Clock()


def member_ctx(*role_ids: int, uid: int = 5, name: str = "Hugo") -> FakeCtx:
    guild = FakeGuild(int(GUILD), "Les Copains")
    author = guild.add_member(FakeMember(uid, name, roles=[FakeRole(r) for r in role_ids]))
    ctx = FakeCtx(author, guild.add_channel(FakeChannel(7, "général", guild=guild)))
    ctx.args, ctx.kwargs = [], {}  # what interactions.py reads the options from
    return ctx


@pytest.mark.asyncio
async def test_the_registered_command_answers_with_a_configured_response(config_file, monkeypatch):
    command = registered(config_file, monkeypatch, responses=["paf", "pif"])
    ctx = member_ctx()
    await command.call_callback(command.callback, ctx)
    assert ctx.last in ("paf", "pif")
    # The admins' response pings what it names, and @everyone only if it says so.
    assert "everyone" not in (ctx.replies[0]["allowed_mentions"].parse or [])


@pytest.mark.asyncio
async def test_a_command_aimed_at_a_member_names_both_and_pings_the_target(
    config_file, monkeypatch
):
    command = registered(
        config_file,
        monkeypatch,
        name="calin",
        target="optional",
        responses=[
            "{auteur} fait un câlin à {cible} sur {serveur} 🤗 https://cdn.exemple.fr/calin.gif"
        ],
    )
    assert [str(o.name) for o in command.options] == ["cible"] and not command.options[0].required
    ctx = member_ctx()
    lea = ctx.guild.add_member(FakeMember(8, "Léa"))
    ctx.kwargs = {"cible": lea}
    await command.call_callback(command.callback, ctx)
    reply = ctx.replies[0]
    assert reply["content"] == "<@5> fait un câlin à <@8> sur Les Copains 🤗"
    assert reply["embeds"][0].image.url == "https://cdn.exemple.fr/calin.gif"
    assert reply["allowed_mentions"].users == [8]  # not the author

    alone = member_ctx()
    await command.call_callback(command.callback, alone)
    assert alone.last.startswith("<@5> fait un câlin à tout le monde")


def test_a_required_target_is_a_required_option(config_file, monkeypatch):
    command = registered(config_file, monkeypatch, target="required")
    assert command.options[0].required
    assert not registered(config_file, monkeypatch).options


@pytest.mark.asyncio
async def test_a_command_kept_to_some_roles_refuses_the_others(config_file, monkeypatch):
    command = registered(config_file, monkeypatch, roles=["30", "31"])
    outsider = member_ctx(29)
    await command.call_callback(command.callback, outsider)
    assert outsider.replies[0]["ephemeral"] and outsider.last.startswith("🔒")
    assert "<@&30>" in outsider.last
    member = member_ctx(31)
    await command.call_callback(command.callback, member)
    assert member.last == "paf"


@pytest.mark.asyncio
async def test_the_cooldown_makes_a_member_wait_and_no_one_else(config_file, monkeypatch):
    command = registered(config_file, monkeypatch, cooldown=30)
    first = member_ctx()
    await command.call_callback(command.callback, first)
    CLOCK.now += 10
    again = member_ctx()
    await command.call_callback(command.callback, again)
    assert (
        again.replies[0]["ephemeral"] and again.last == "⏱️ Encore 20 s avant de relancer /blague."
    )
    other = member_ctx(uid=6, name="Léa")
    await command.call_callback(command.callback, other)
    assert other.last == "paf"
    CLOCK.now += 20
    later = member_ctx()
    await command.call_callback(command.callback, later)
    assert later.last == "paf"


def test_the_options_are_read_within_their_bounds():
    parsed = parse_commands(
        [
            {
                "name": "a",
                "responses": ["x"],
                "target": "everyone",
                "cooldown": 99999,
                "roles": ["12", "12", "abc"],
            },
            {"name": "b", "responses": ["x"], "cooldown": -4, "target": "required"},
        ]
    )
    a, b = parsed.commands
    assert (a.target, a.cooldown, a.roles) == ("none", MAX_COOLDOWN_SECONDS, ["12"])
    assert (b.target, b.cooldown) == ("required", 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://media.exemple.fr/a.gif", Response("", "https://media.exemple.fr/a.gif")),
        (
            "Paf <https://media.exemple.fr/a.PNG?x=1>",
            Response("Paf", "https://media.exemple.fr/a.PNG?x=1"),
        ),
        (
            "Regarde https://tenor.exemple.fr/view/123",
            Response("Regarde https://tenor.exemple.fr/view/123"),
        ),
        ("juste du texte", Response("juste du texte")),
    ],
)
def test_an_image_link_becomes_the_embed_image(text, expected):
    assert split_image(text) == expected


async def _identity(text: str) -> str:
    return text
