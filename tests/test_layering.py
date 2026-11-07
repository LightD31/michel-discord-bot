"""The three-layer split holds: ``extensions/`` → ``features/`` → ``src/``.

- ``features/`` is pure domain code: no ``interactions`` (that's what keeps it
  unit-testable) and no ``extensions``.
- ``src/`` is shared infrastructure: it never reaches up into ``extensions``
  (the dashboard renders through ``src/discord_ext`` instead), and
  ``src/core`` and ``src/integrations`` stay framework-free.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

RULES = [
    ("features", {"interactions", "extensions"}),
    ("src", {"extensions"}),
    ("src/core", {"interactions"}),
    ("src/integrations", {"interactions"}),
]


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(("package", "forbidden"), RULES, ids=[rule[0] for rule in RULES])
def test_layer_imports(package: str, forbidden: set[str]):
    offenders = [
        f"{path.relative_to(ROOT)} imports {sorted(_imported_roots(path) & forbidden)}"
        for path in sorted((ROOT / package).rglob("*.py"))
        if _imported_roots(path) & forbidden
    ]
    assert not offenders, "\n".join(offenders)
