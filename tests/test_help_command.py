"""/aide: built from the loaded commands, grouped, filtered by permissions, paginated."""

import copy
from datetime import timedelta
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
from extensions.help import (
    HelpEntry,
    HelpExtension,
    collect_entries,
    module_menus,
    module_page,
    modules,
    render_pages,
)
from src.core import clock
from src.core.text import EMBED_FIELD_VALUE_LIMIT
from src.webui.schemas import ModuleHelp

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


def aide_ctx(store_config, permissions=Permissions.NONE):
    store_config({"servers": {}})
    guild = FakeGuild(GUILD)
    channel = guild.add_channel(FakeChannel(20))
    member = guild.add_member(FakeMember(7, "bob", permissions=permissions))
    ctx = FakeCtx(member, channel)
    ctx.bot.interactions_by_scope = client_with_commands().interactions_by_scope
    return ctx, bare_extension(HelpExtension, ctx.bot)


async def test_aide_opens_on_the_modules_privately(store_config):
    ctx, ext = aide_ctx(store_config)
    await run(HelpExtension.aide, ext, ctx)
    reply = ctx.replies[-1]
    assert reply["ephemeral"] is True
    (page,) = reply["embeds"]
    assert "Choisis un module" in page.description
    assert "**🎲 Hasard**" in page.description
    assert "Giveaways" not in page.description  # bob has no Manage Server
    (row,) = reply["components"]
    (menu,) = row.components
    assert menu.custom_id == "aide:0"
    assert [o.label for o in menu.options] == ["🎲 Hasard"]


async def test_a_module_picked_shows_what_it_does_and_its_commands(store_config):
    ctx, ext = aide_ctx(store_config, Permissions.ADMINISTRATOR)
    ctx.values = ["🎁 Giveaways"]
    await run(HelpExtension.on_module_chosen, ext, ctx)
    (edit,) = ctx.origin_edits
    (page,) = edit["embeds"]
    assert page.title == "🎁 Giveaways"
    assert page.description.startswith("Des tirages au sort")
    fields = {f.name: f.value for f in page.fields}
    assert "⚙️ Tout seul" in fields
    assert "/giveaway start" in fields["Les autres commandes"]
    (menu,) = edit["components"][0].components
    assert [o.default for o in menu.options] == [True, False]  # the chosen module stays picked


async def test_a_module_gone_since_shows_the_list_again(store_config):
    ctx, ext = aide_ctx(store_config)
    ctx.values = ["🎁 Giveaways"]  # bob can't use any of it
    await run(HelpExtension.on_module_chosen, ext, ctx)
    (page,) = ctx.origin_edits[-1]["embeds"]
    assert page.title == "❓ Aide"


def test_more_than_25_modules_spread_over_several_lists():
    entries = [
        HelpEntry(f"Module {i:02d}", f"c{i}", f"`/c{i}`", "d", ModuleHelp("À quoi ça sert."))
        for i in range(30)
    ]
    rows = module_menus(modules(entries))
    assert [len(r.components[0].options) for r in rows] == [25, 5]
    assert [r.components[0].custom_id for r in rows] == ["aide:0", "aide:1"]


def test_a_module_page_features_the_commands_to_know():
    about = ModuleHelp("Des niveaux.", ("/niveau", "/absente"), "De l'XP à chaque message.")
    group = [
        HelpEntry("⭐ XP", "niveau", "</niveau:1>", "Ton niveau", about),
        HelpEntry("⭐ XP", "classement", "</classement:2>", "Le classement", about),
    ]
    page = module_page("⭐ XP", group)
    fields = {f.name: f.value for f in page.fields}
    assert fields["🧭 À connaître"] == "</niveau:1> — Ton niveau\n`/absente`"
    assert fields["⚙️ Tout seul"] == "De l'XP à chaque message."
    assert fields["Les autres commandes"] == "</classement:2> — Le classement"


async def test_aide_still_searches(store_config):
    ctx, ext = aide_ctx(store_config, Permissions.ADMINISTRATOR)
    await run(HelpExtension.aide, ext, ctx, search="GIVEAWAY")
    (page,) = ctx.replies[-1]["embeds"]
    assert [f.name for f in page.fields] == ["🎁 Giveaways"]
    assert "pour « GIVEAWAY »" in page.description

    await run(HelpExtension.aide, ext, ctx, search="introuvable")
    assert ctx.last.startswith("Aucune commande ne correspond à « introuvable ».")


# ── Hello when Michel joins (B7) ──────────────────────────────────────────


def joined_guild(minutes_ago: int, *, can_send: bool = True):
    guild = FakeGuild(GUILD)
    system = guild.add_channel(FakeChannel(30))
    guild.system_channel = system
    guild.me.joined_at = clock.now() - timedelta(minutes=minutes_ago)
    rights = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES | Permissions.EMBED_LINKS
    system.overrides[guild.me.id] = rights if can_send else Permissions.VIEW_CHANNEL
    return guild, system


async def hello(guild, store_config, *, ready=True, webui=None):
    store_config({"config": {"webui": webui or {}}, "servers": {}})
    bot = SimpleNamespace(is_ready=ready, get_guild=lambda gid: guild)
    ext = bare_extension(HelpExtension, bot)
    await HelpExtension.on_guild_join.callback(ext, SimpleNamespace(guild_id=GUILD))


async def test_michel_says_hello_once_invited(store_config):
    guild, system = joined_guild(1)
    await hello(guild, store_config, webui={"enabled": True, "baseUrl": "https://michel.example"})
    (message,) = system.sent
    (embed,) = message.embeds
    assert embed.title == "👋 Salut, moi c'est Michel"
    assert "/aide" in embed.description
    assert "[tableau de bord](https://michel.example)" in embed.description


async def test_no_dashboard_link_when_the_dashboard_is_off(store_config):
    guild, system = joined_guild(1)
    await hello(guild, store_config, webui={"enabled": False, "baseUrl": "https://michel.example"})
    assert "https://" not in system.sent[0].embeds[0].description


async def test_the_startup_replay_and_old_servers_get_no_hello(store_config):
    guild, system = joined_guild(1)
    await hello(guild, store_config, ready=False)
    old, old_system = joined_guild(60 * 24 * 30)
    await hello(old, store_config)
    assert system.sent == [] and old_system.sent == []


async def test_no_hello_where_michel_cannot_write(store_config):
    guild, system = joined_guild(1, can_send=False)
    await hello(guild, store_config)
    assert system.sent == []


# ── Right-click menus (B1) ────────────────────────────────────────────────


def test_right_click_menus_are_listed_with_where_to_find_them():
    import extensions.moderation  # noqa: F401 — registers moduleModeration
    from extensions.moderation import ModerationExtension

    client = interactions.Client()
    for shared in (ModerationExtension.report_message, ModerationExtension.history_menu):
        menu = copy.copy(shared)  # the class's own stays as it is
        menu.scopes = [GUILD]
        client.add_command(owned_by(menu, SimpleNamespace(MODULE_KEYS=("moduleModeration",))))
    by_name = {e.name: e for e in collect_entries(client.interactions_by_scope, GUILD, staff)}
    report = by_name["🚩 Signaler"]
    assert report.group == "🛡️ Modération" and report.mention == "**🚩 Signaler**"
    assert report.description == (
        "Signaler ce message aux modérateurs, en privé *(clic droit sur un message → Applications)*"
    )
    assert "clic droit sur un membre" in by_name["🛡️ Historique"].description
    # A moderators' menu is hidden from members, as Discord hides it.
    names = [e.name for e in collect_entries(client.interactions_by_scope, GUILD, everyone)]
    assert names == ["🚩 Signaler"]
    (page,) = render_pages(list(by_name.values()))
    assert "clic droit" in page.description


def test_every_right_click_menu_says_what_it_does():
    import importlib
    import pkgutil

    import extensions
    from src.discord_ext.menus import MENU_HELP

    menus = []
    for info in pkgutil.iter_modules(extensions.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"extensions.{info.name}")
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, interactions.Extension):
                menus += [
                    m for m in vars(value).values() if isinstance(m, interactions.ContextMenu)
                ]
                for base in value.__mro__[1:]:
                    menus += [
                        m for m in vars(base).values() if isinstance(m, interactions.ContextMenu)
                    ]
    names = {str(m.name) for m in menus}
    assert len(names) >= 6
    assert sorted(n for n in names if n not in MENU_HELP) == []
    # Discord: at most 5 message menus and 5 user menus per server.
    by_type = {t: {str(m.name) for m in menus if m.type == t} for t in (2, 3)}
    assert all(len(found) <= 5 for found in by_type.values()), by_type


def test_the_overview_points_members_to_their_space_when_the_dashboard_has_an_address():
    from extensions.help import overview_page

    assert "Mon espace" not in (overview_page({}).description or "")
    page = overview_page({}, "https://michel.example/")
    assert page.description.endswith("[Mon espace](https://michel.example/#/me)")


def test_the_dashboard_address_is_read_only_when_the_dashboard_is_on(store_config):
    from extensions.help import dashboard_url

    store_config({"config": {"webui": {"enabled": False, "baseUrl": "https://michel.example"}}})
    assert dashboard_url() is None
    store_config({"config": {"webui": {"enabled": True, "baseUrl": " https://michel.example "}}})
    assert dashboard_url() == "https://michel.example"
