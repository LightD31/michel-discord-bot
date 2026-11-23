"""The three-layer split holds: ``extensions/`` → ``features/`` → ``src/``.

- ``features/`` is pure domain code: no ``interactions`` (that's what keeps it
  unit-testable), nothing that depends on it (``src.discord_ext``), no
  dashboard and no ``extensions``.
- ``src/`` is shared infrastructure: it never reaches up into ``extensions``
  (the dashboard renders through ``src/discord_ext`` instead), and
  ``src/core`` and ``src/integrations`` stay framework-free. ``src/core`` is
  the bottom layer: the dashboard registers its log handler with it, core
  never imports the dashboard.
- ``extensions/`` reach MongoDB through the repositories only: no driver
  (``pymongo``, ``bson``) there.

Imports are compared by full module name, relative ones resolved, and
``importlib.import_module("…")`` with a literal counts too (T02).
"""

import pytest
from source_scan import Imports, parse, python_files, rel

RULES = [
    ("features", {"interactions", "extensions", "src.discord_ext", "src.webui"}),
    ("src", {"extensions"}),
    ("src/core", {"interactions", "src.discord_ext", "src.webui", "src.integrations"}),
    ("src/integrations", {"interactions", "src.discord_ext", "src.webui"}),
    ("src/discord_ext", {"src.webui"}),
    ("extensions", {"pymongo", "bson", "motor"}),
]


def _within(module: str, forbidden: set[str]) -> str | None:
    return next((f for f in forbidden if module == f or module.startswith(f + ".")), None)


def layer_offenders(imports: Imports, forbidden: set[str]) -> list[tuple[int, str]]:
    return [(line, module) for line, module in imports.modules if _within(module, forbidden)]


@pytest.mark.parametrize(("package", "forbidden"), RULES, ids=[rule[0] for rule in RULES])
def test_layer_imports(package: str, forbidden: set[str]):
    offenders = [
        f"{rel(path)}:{line} imports {module}"
        for path in python_files(package)
        for line, module in layer_offenders(parse(path)[1], forbidden)
    ]
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "source",
    [
        "import interactions",
        "import interactions.api.events as events",
        "from interactions import Client",
        "from src.discord_ext.look import card",
        "from src.discord_ext import look",
        "import importlib\nimportlib.import_module('interactions')",
        "__import__('src.webui.app')",
    ],
)
def test_the_scan_sees_every_spelling(source):
    import ast

    forbidden = {"interactions", "src.discord_ext", "src.webui"}
    assert layer_offenders(Imports(ast.parse(source)), forbidden)


def test_a_neighbouring_package_is_not_mistaken_for_a_forbidden_one():
    import ast

    imports = Imports(ast.parse("import src.discord_extras\nfrom src.core import text"))
    assert layer_offenders(imports, {"src.discord_ext"}) == []
