"""/aide: built from the loaded commands, grouped, filtered by permissions, paginated."""

from types import SimpleNamespace

import interactions
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run
from interactions import (
    GLOBAL_SCOPE,
    ContextType,
    IntegrationType,
    Permissions,
    slash_command,
    slash_default_member_permission,
)

import extensions.giveaway  # noqa: F401 — registers the moduleGiveaway schema
from extensions.help import HelpExtension, collect_entries, render_pages
from src.core.text import EMBED_FIELD_VALUE_LIMIT

GUILD = 10


def handler():
    """A fresh callback: decorators store permissions on the function itself."""

    async def callback(ctx) -> None:
        pass

    return callback


def owned_by(command, extension):
    command.extension = extension
    return command


GIVEAWAY_EXT = SimpleNamespace(MODULE_KEYS=("moduleGiveaway",))
RANDOM_EXT = SimpleNamespace(MODULE_KEYS=(), HELP_LABEL="🎲 Hasard")


def client_with_commands():
    start = slash_command(
        name="giveaway",
        description="Gérer les giveaways",
        sub_cmd_name="start",
        sub_cmd_description="Lancer un giveaway",
        scopes=[GUILD],
    )(slash_default_member_permission(Permissions.MANAGE_GUILD)(handler()))
    # Discord applies the first subcommand's permissions to the whole command.
    listing = start.subcommand(sub_cmd_name="list", sub_cmd_description="Lister")(handler())
    pick = slash_command(
        name="pick",
        description="Choisir au hasard",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )(handler())
    dm_only = slash_command(name="dm", description="En MP", contexts=[ContextType.BOT_DM])(
        handler()
    )
    elsewhere = slash_command(name="ailleurs", description="Autre serveur", scopes=[GUILD + 1])(
        handler()
    )
    client = interactions.Client()
    for command in (
        owned_by(start, GIVEAWAY_EXT),
        owned_by(listing, GIVEAWAY_EXT),
        owned_by(pick, RANDOM_EXT),
        dm_only,
        elsewhere,
    ):
        client.add_command(command)
    start.cmd_id[GUILD] = 111
    pick.cmd_id[GLOBAL_SCOPE] = 222
    return client


def everyone(required):
    return not required


def staff(required):
    return True


def test_a_member_sees_the_servers_and_the_global_commands_grouped():
    entries = collect_entries(client_with_commands().interactions_by_scope, GUILD, staff)
    assert {(e.group, e.name) for e in entries} == {
        ("🎁 Giveaways", "giveaway start"),
        ("🎁 Giveaways", "giveaway list"),
        ("🎲 Hasard", "pick"),
    }
    by_name = {e.name: e for e in entries}
    assert by_name["giveaway start"].mention == "</giveaway start:111>"
    assert by_name["giveaway start"].description == "Lancer un giveaway"
    assert by_name["pick"].mention == "</pick:222>"


def test_commands_needing_permissions_the_member_lacks_are_hidden():
    entries = collect_entries(client_with_commands().interactions_by_scope, GUILD, everyone)
    assert [e.name for e in entries] == ["pick"]


def test_a_command_never_synced_is_named_without_a_link():
    client = client_with_commands()
    client.interactions_by_scope[GLOBAL_SCOPE]["pick"].cmd_id.clear()
    entries = collect_entries(client.interactions_by_scope, GUILD, staff)
    assert next(e for e in entries if e.name == "pick").mention == "`/pick`"


def test_long_lists_spread_over_fields_and_pages_discord_accepts():
    client = interactions.Client()
    for i in range(120):
        command = slash_command(
            name=f"commande{i:03d}", description="Une description assez longue " * 3, scopes=[GUILD]
        )(handler())
        client.add_command(owned_by(command, GIVEAWAY_EXT))
    entries = collect_entries(client.interactions_by_scope, GUILD, staff)
    pages = render_pages(entries)
    assert len(pages) > 1
    for page in pages:
        assert len(page.fields) <= 25
        assert all(len(f.value) <= EMBED_FIELD_VALUE_LIMIT for f in page.fields)
        assert len(page) <= 6000
    headings = [f.name for page in pages for f in page.fields]
    assert headings[0] == "🎁 Giveaways" and headings[1] == "🎁 Giveaways (suite)"
    listed = "\n".join(f.value for page in pages for f in page.fields)
    assert all(f"/commande{i:03d}" in listed for i in range(120))


async def test_aide_answers_privately_and_searches(store_config):
    store_config({"servers": {}})
    guild = FakeGuild(GUILD)
    channel = guild.add_channel(FakeChannel(20))
    member = guild.add_member(FakeMember(7, "bob"))
    ctx = FakeCtx(member, channel)
    ctx.bot.interactions_by_scope = client_with_commands().interactions_by_scope
    ext = bare_extension(HelpExtension, ctx.bot)

    await run(HelpExtension.aide, ext, ctx)
    (page,) = ctx.replies[-1]["embeds"]
    assert ctx.replies[-1]["ephemeral"] is True
    assert [f.name for f in page.fields] == ["🎲 Hasard"]  # bob has no Manage Server

    member.guild_permissions = Permissions.ADMINISTRATOR
    await run(HelpExtension.aide, ext, ctx, search="GIVEAWAY")
    (page,) = ctx.replies[-1]["embeds"]
    assert [f.name for f in page.fields] == ["🎁 Giveaways"]
    assert "pour « GIVEAWAY »" in page.description

    await run(HelpExtension.aide, ext, ctx, search="introuvable")
    assert ctx.last == "Aucune commande ne correspond à « introuvable »."
