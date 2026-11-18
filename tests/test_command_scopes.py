"""Module commands only exist on the guilds where their module is enabled.

interactions.py's decorators turn an empty ``scopes`` list into a global
command (``scopes or [GLOBAL_SCOPE]``), so a module enabled nowhere would show
its commands on every server. ``guild_scopes`` sidesteps that; these tests pin
the library behaviour it relies on, and check every extension command uses it.
"""

import ast

import interactions
import pytest
from interactions import GLOBAL_SCOPE, slash_command
from source_scan import Imports, keyword, parse, python_files, rel

from src.discord_ext.scopes import guild_scopes


async def _noop(ctx) -> None:
    pass


def test_guild_scopes_converts_ids_and_skips_blank_entries():
    assert list(guild_scopes(["1", 2, None, "", "  ", " 3 "])) == [1, 2, 3]
    # 0 is GLOBAL_SCOPE: letting it through would make the command global again.
    assert list(guild_scopes([0, "0"])) == []


def test_a_raw_empty_scope_list_falls_back_to_global():
    # The trap guild_scopes exists for: if this stops holding, the helper's
    # truthiness override is no longer needed.
    cmd = slash_command(name="trap", description="d", scopes=[])(_noop)
    assert cmd.scopes == [GLOBAL_SCOPE]


def test_empty_guild_scopes_register_the_command_nowhere():
    cmd = slash_command(name="nowhere", description="d", scopes=guild_scopes([]))(_noop)
    assert cmd.scopes == []
    assert type(cmd.scopes) is list  # the truthy override stays out of the command

    client = interactions.Client()
    client.add_command(cmd)
    assert not any("nowhere" in cmds for cmds in client.interactions_by_scope.values())


def test_subcommands_inherit_the_guild_scopes():
    base = slash_command(
        name="group",
        description="d",
        sub_cmd_name="first",
        sub_cmd_description="d",
        scopes=guild_scopes(["10", "20"]),
    )(_noop)
    second = base.subcommand(sub_cmd_name="second", sub_cmd_description="d")(_noop)
    assert second.scopes == [10, 20]

    empty = slash_command(
        name="empty",
        description="d",
        sub_cmd_name="a",
        sub_cmd_description="d",
        scopes=guild_scopes([]),
    )(_noop)
    assert empty.subcommand(sub_cmd_name="b", sub_cmd_description="d")(_noop).scopes == []


# ── Every extension command is scoped ────────────────────────────────────

COMMAND_FACTORIES = {
    "slash_command",
    "SlashCommand",
    "context_menu",
    "message_context_menu",
    "user_context_menu",
    "ContextMenu",
}

# Deliberately available everywhere, by (file, command name): random_ reads no
# per-guild config, and /aide lists whatever each server has (guild-only:
# contexts=[GUILD]). A new command in these files still needs its scopes.
GLOBAL_COMMANDS = {
    ("extensions/help.py", "aide"),
    ("extensions/random_.py", "pick"),
    ("extensions/random_.py", "roll"),
    ("extensions/random_.py", "coin"),
    ("extensions/random_.py", "shuffle"),
    # A member's own data, on every server at once (and in DMs): F10.
    ("extensions/mydata.py", "mes-donnees"),
}


def _command_name(call: ast.Call) -> str | None:
    named = keyword(call, "name")
    value = named.value if named is not None else (call.args[0] if call.args else None)
    if isinstance(value, ast.Call) and value.args:  # en_fr_name("pick", "choisir")
        value = value.args[0]
    return value.value if isinstance(value, ast.Constant) else None


def _is_factory(call: ast.Call, imports: Imports) -> bool:
    name = imports.resolve(call.func)
    return name is not None and name.rpartition(".")[2] in COMMAND_FACTORIES


def _is_safe_scope(value: ast.expr, imports: Imports) -> bool:
    """Only ``guild_scopes(...)``: a raw list turns global when empty, or holds 0."""
    if not isinstance(value, ast.Call):
        return False
    return (imports.resolve(value.func) or "").rpartition(".")[2] == "guild_scopes"


def scope_offenders(tree: ast.AST, imports: Imports, where: str) -> list[str]:
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_factory(node, imports)):
            continue
        scopes = keyword(node, "scopes")
        if scopes is None:
            if (where, _command_name(node)) not in GLOBAL_COMMANDS:
                offenders.append(f"{where}:{node.lineno}: no scopes (global on every server)")
        elif not _is_safe_scope(scopes.value, imports):
            offenders.append(
                f"{where}:{node.lineno}: scopes={ast.unparse(scopes.value)} can turn global"
                " — use guild_scopes(...)"
            )
    return offenders


def user_install_offenders(tree: ast.AST, imports: Imports, where: str) -> list[str]:
    """Guild-scoped commands that declare a user install, which Discord ignores there.

    A user-installed command lives on the member's account, not on a server:
    on a command with guild scopes the declaration does nothing, and code
    written for it never runs (``/sondage`` had an in-memory fallback for it).
    """
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_factory(node, imports)):
            continue
        kinds = keyword(node, "integration_types")
        if keyword(node, "scopes") is not None and kinds is not None:
            names = {(imports.resolve(n) or "") for n in ast.walk(kinds.value)}
            if any(name.endswith("USER_INSTALL") for name in names):
                offenders.append(f"{where}:{node.lineno}: user install on a guild command")
    return offenders


def test_no_guild_command_declares_a_user_install():
    offenders = []
    for path in python_files("extensions"):
        tree, imports, _ = parse(path)
        offenders += user_install_offenders(tree, imports, rel(path))
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "source",
    [
        "from interactions import IntegrationType, slash_command\n"
        "@slash_command(name='x', scopes=guild_scopes([]),"
        " integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL])\n"
        "def f(): ...",
        "import interactions as ipy\n"
        "@ipy.slash_command(name='x', scopes=guild_scopes([]),"
        " integration_types=[ipy.IntegrationType.USER_INSTALL])\ndef f(): ...",
    ],
)
def test_the_user_install_scan_catches(source):
    tree = ast.parse(source)
    assert user_install_offenders(tree, Imports(tree), "x.py")


def test_a_global_command_may_be_user_installed():
    source = (
        "from interactions import IntegrationType, slash_command\n"
        "@slash_command(name='roll', integration_types=[IntegrationType.USER_INSTALL])\n"
        "def f(): ...\n"
    )
    tree = ast.parse(source)
    assert user_install_offenders(tree, Imports(tree), "x.py") == []


def test_every_extension_command_is_scoped_to_its_guilds():
    offenders = []
    for path in python_files("extensions"):
        tree, imports, _ = parse(path)
        offenders += scope_offenders(tree, imports, rel(path))
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "source",
    [
        "from interactions import slash_command\n@slash_command(name='x')\ndef f(): ...",
        "from interactions import slash_command as cmd\n@cmd(name='x')\ndef f(): ...",
        "import interactions as ipy\n@ipy.slash_command(name='x')\ndef f(): ...",
        "from interactions import slash_command\n@slash_command(name='x', scopes=[0])\n"
        "def f(): ...",
        "from interactions import GLOBAL_SCOPE, SlashCommand\n"
        "SlashCommand(name='x', scopes=[GLOBAL_SCOPE])",
        # Allowlisted by command, not by file: a new global command there is caught.
        "from interactions import slash_command\n@slash_command(name='new')\ndef f(): ...",
    ],
)
def test_the_scan_catches(source):
    tree = ast.parse(source)
    assert scope_offenders(tree, Imports(tree), "extensions/random_.py")


def test_the_scan_accepts_guild_scopes_and_the_allowlisted_commands():
    source = (
        "from interactions import slash_command\n"
        "from src.discord_ext.scopes import guild_scopes\n"
        "@slash_command(name='x', scopes=guild_scopes(MODULE.guild_ids()))\ndef f(): ...\n"
        "@slash_command(name='roll')\ndef g(): ...\n"
    )
    tree = ast.parse(source)
    assert scope_offenders(tree, Imports(tree), "extensions/random_.py") == []
