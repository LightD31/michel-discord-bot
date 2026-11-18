"""A command's default permission never combines ADMINISTRATOR with another flag.

Discord reads ``default_member_permissions`` as "every one of these": an
administrator has them all anyway, so ``ADMINISTRATOR | MANAGE_MESSAGES`` hid
``/effacer`` and ``/envoyer`` from every moderator (and ``/aide`` with them), when
``MANAGE_MESSAGES`` alone was meant (Q4). Name the one permission the command
needs; administrators pass every check.
"""

import ast

import pytest
from source_scan import Imports, keyword, python_files, rel

PERMISSION_CALLS = {"slash_default_member_permission"}


def _flags(node: ast.expr, imports: Imports) -> list[str]:
    """The permission names an ``A | B | C`` expression ORs together."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flags(node.left, imports) + _flags(node.right, imports)
    return [(imports.resolve(node) or "").rpartition(".")[2]]


def offenders(tree: ast.AST, imports: Imports, where: str) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (imports.resolve(node.func) or "").rpartition(".")[2]
        values = [node.args[0]] if name in PERMISSION_CALLS and node.args else []
        kw = keyword(node, "default_member_permissions")
        if kw is not None:
            values.append(kw.value)
        for value in values:
            flags = _flags(value, imports)
            if len(flags) > 1 and "ADMINISTRATOR" in flags:
                found.append(f"{where}:{node.lineno}: {' | '.join(flags)}")
    return found


def test_no_command_asks_for_administrator_and_something_else():
    found = []
    for path in python_files("extensions", "src"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += offenders(tree, Imports(tree, path), rel(path))
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "from interactions import Permissions, slash_default_member_permission\n"
        "@slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_ROLES)\n"
        "async def f(ctx): ...",
        "import interactions as ipy\n"
        "@ipy.slash_default_member_permission(\n"
        "    ipy.Permissions.KICK_MEMBERS | ipy.Permissions.ADMINISTRATOR\n"
        ")\n"
        "async def f(ctx): ...",
        "from interactions import Permissions, slash_command\n"
        "@slash_command(name='x', default_member_permissions=(Permissions.MANAGE_GUILD"
        " | Permissions.BAN_MEMBERS) | Permissions.ADMINISTRATOR)\n"
        "async def f(ctx): ...",
        "from interactions import SlashCommand\n"
        "from interactions.models.discord.enums import Permissions as P\n"
        "cmd = SlashCommand(name='x', default_member_permissions=P.ADMINISTRATOR | P.MANAGE_GUILD)",
    ],
)
def test_the_guard_catches_every_spelling(source):
    tree = ast.parse(source)
    assert offenders(tree, Imports(tree), "x.py")


@pytest.mark.parametrize(
    "source",
    [
        "@slash_default_member_permission(Permissions.MANAGE_ROLES)\nasync def f(ctx): ...",
        "@slash_default_member_permission(Permissions.MANAGE_ROLES | Permissions.MANAGE_GUILD)\n"
        "async def f(ctx): ...",
        "ok = has_any(permissions, ADMINISTRATOR | MANAGE_ROLES)",
    ],
)
def test_the_guard_lets_the_rest_through(source):
    tree = ast.parse(source)
    assert offenders(tree, Imports(tree), "x.py") == []
