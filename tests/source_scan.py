"""Helpers for the tests that scan the bot's own sources (T02).

Those tests used to match a name as written: ``datetime.now()`` was caught, but
``import datetime as dt; dt.datetime.now()`` was not, and a test scanning
``Path("extensions")`` scanned nothing when run from another directory. Here a
call is resolved through the file's imports to the dotted name it really
designates, and paths start from the repository root.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Every file of the bot: what runs in the container (scripts included).
BOT_SOURCES = ("main.py", "src", "features", "extensions", "scripts")


def python_files(*names: str) -> Iterator[Path]:
    """The ``.py`` files under *names* (relative to the root), parked extensions excluded."""
    for name in names:
        path = ROOT / name
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file in files:
            rel = file.relative_to(ROOT).parts
            if rel[0] == "extensions" and len(rel) > 1 and rel[1].startswith("_"):
                continue  # work in progress, never loaded (main.py)
            yield file


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name


def module_name(path: Path) -> str:
    """``src/core/db.py`` → ``src.core.db``; a package's ``__init__`` is the package."""
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _absolute(node: ast.ImportFrom, package: str) -> str:
    """The module an ``ImportFrom`` names, relative imports resolved against *package*."""
    if not node.level:
        return node.module or ""
    base = package.split(".") if package else []
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join([*base, node.module] if node.module else base)


def _package_of(path: Path | None) -> str:
    if path is None or not path.is_relative_to(ROOT):
        return ""
    name = module_name(path)
    return name if path.name == "__init__.py" else name.rpartition(".")[0]


class Imports:
    """What each local name of a module stands for, from its import statements.

    ``import datetime`` → ``datetime``; ``import interactions as ipy`` →
    ``ipy`` is ``interactions``; ``from datetime import datetime as dt`` →
    ``dt`` is ``datetime.datetime``; ``from . import _common`` in
    ``extensions/xp/x.py`` → ``_common`` is ``extensions.xp._common``.
    """

    def __init__(self, tree: ast.AST, path: Path | None = None) -> None:
        self.names: dict[str, str] = {}
        self.modules: list[tuple[int, str]] = []  # (line, module) of every import
        package = _package_of(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.modules.append((node.lineno, alias.name))
                    if alias.asname:
                        self.names[alias.asname] = alias.name
                    else:
                        root = alias.name.split(".")[0]
                        self.names[root] = root
            elif isinstance(node, ast.ImportFrom):
                module = _absolute(node, package)
                self.modules.append((node.lineno, module))
                for alias in node.names:
                    full = f"{module}.{alias.name}" if module else alias.name
                    self.names[alias.asname or alias.name] = full
                    if node.level or alias.name[:1].islower():
                        # `from src.core import db`: db may be a module, imported too.
                        self.modules.append((node.lineno, full))
            elif isinstance(node, ast.Call) and _dynamic_import(node):
                self.modules.append((node.lineno, _dynamic_import(node) or ""))

    def resolve(self, node: ast.expr) -> str | None:
        """The dotted name *node* designates (``dt.now`` → ``datetime.datetime.now``).

        A name that wasn't imported resolves to itself (a builtin, a local);
        anything else (a call result, a subscript) is ``None``.
        """
        if isinstance(node, ast.Name):
            return self.names.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            owner = self.resolve(node.value)
            return f"{owner}.{node.attr}" if owner else None
        return None


def _dynamic_import(call: ast.Call) -> str | None:
    """The module of ``importlib.import_module("x")`` / ``__import__("x")``, if literal."""
    func = call.func
    named = (isinstance(func, ast.Name) and func.id in ("__import__", "import_module")) or (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    )
    if named and call.args and isinstance(call.args[0], ast.Constant):
        value = call.args[0].value
        return value if isinstance(value, str) else None
    return None


@functools.cache
def parse(path: Path) -> tuple[ast.Module, Imports, str]:
    """A file's tree, its imports and its source, read once for every guard (P27).

    Shared between the guards: none may change the tree it gets.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return tree, Imports(tree, path), source


def is_none(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((kw for kw in call.keywords if kw.arg == name), None)
