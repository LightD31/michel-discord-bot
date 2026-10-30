"""Module commands only exist on the guilds where their module is enabled.

interactions.py's decorators turn an empty ``scopes`` list into a global
command (``scopes or [GLOBAL_SCOPE]``), so a module enabled nowhere would show
its commands on every server. ``guild_scopes`` sidesteps that; these tests pin
the library behaviour it relies on, and check every extension command uses it.
"""

import ast
from pathlib import Path

import interactions
from interactions import GLOBAL_SCOPE, slash_command

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

EXTENSIONS_DIR = Path(__file__).resolve().parents[1] / "extensions"

COMMAND_FACTORIES = {
    "slash_command",
    "SlashCommand",
    "context_menu",
    "message_context_menu",
    "user_context_menu",
}

# Deliberately available everywhere: random_ reads no per-guild config, and
# compareai's /ask and /ask-stats are user-installable so they work in DMs.
GLOBAL_COMMAND_FILES = {
    "extensions/random_.py",
    "extensions/compareai/ask.py",
    "extensions/compareai/voting.py",
}


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_safe_scope(value: ast.expr) -> bool:
    """``guild_scopes(...)``, or a non-empty literal list of guild ids."""
    if isinstance(value, ast.Call):
        return _callee(value) == "guild_scopes"
    return isinstance(value, ast.List) and bool(value.elts)


def test_every_extension_command_is_scoped_to_its_guilds():
    offenders = []
    for path in sorted(EXTENSIONS_DIR.rglob("*.py")):
        rel = path.relative_to(EXTENSIONS_DIR.parent).as_posix()
        if rel.split("/")[1].startswith("_"):  # parked work-in-progress, never loaded
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and _callee(node) in COMMAND_FACTORIES):
                continue
            scopes = next((kw.value for kw in node.keywords if kw.arg == "scopes"), None)
            if scopes is None:
                if rel not in GLOBAL_COMMAND_FILES:
                    offenders.append(f"{rel}:{node.lineno}: no scopes (global on every server)")
            elif not _is_safe_scope(scopes):
                offenders.append(
                    f"{rel}:{node.lineno}: scopes={ast.unparse(scopes)} turns global when empty"
                    " — use guild_scopes(...)"
                )
    assert not offenders, "\n".join(offenders)
