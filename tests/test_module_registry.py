"""Each extension declares the per-guild config modules it reads, in ``MODULE_KEYS``.

The dashboard reloads the loaded extensions declaring a module after a save or
a toggle (``WebUIContext.module_extension_map``), and only accepts modules
whose schema is registered. Nothing scans the sources at runtime any more, so
these tests keep the declarations explicit and complete.
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.webui.context import WebUIContext, declared_modules, extension_path

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS_DIR = ROOT / "extensions"
MODULE_KEY = re.compile(r"^module[A-Z]\w*$")

# Read on demand by many extensions (load_discord2name), owned by none.
UNOWNED_MODULES = {"discord2name"}

# Imports every extension in a fresh interpreter, against config.example.json:
# several extensions read the config at import time, and importing them here
# would leave their module-level state (and registered schemas) behind for the
# rest of the suite.
_PROBE = r"""
import importlib, inspect, json, os, sys

import src.core.config as config

config.CONFIG_PATH = "config.example.json"

from interactions import Extension

from src.webui import schemas

out = {"extensions": {}, "failures": []}
for entry in sorted(os.listdir("extensions")):
    if entry.startswith("__"):
        continue
    if entry.endswith(".py"):
        name = "extensions." + entry[:-3]
    elif os.path.isfile(os.path.join("extensions", entry, "__init__.py")):
        name = "extensions." + entry
    else:
        continue
    try:
        module = importlib.import_module(name)
    except Exception as e:
        out["failures"].append(f"{name}: {e!r}")
        continue
    classes = {}
    for cls_name, cls in vars(module).items():
        if (
            inspect.isclass(cls)
            and issubclass(cls, Extension)
            and cls is not Extension
            and cls.__module__ == name
        ):
            own = cls.__dict__.get("MODULE_KEYS")
            classes[cls_name] = None if own is None else list(own)
    out["extensions"][name] = classes
out["schemas"] = sorted(schemas.MODULE_SCHEMAS)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f)
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> dict:
    target = tmp_path_factory.mktemp("registry") / "probe.json"
    subprocess.run(
        [sys.executable, "-c", _PROBE, str(target)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads(target.read_text(encoding="utf-8"))


def test_every_extension_imports(probe):
    assert probe["failures"] == []


def test_every_extension_declares_its_modules_explicitly(probe):
    for name, classes in probe["extensions"].items():
        assert len(classes) == 1, f"{name} should define one Extension class: {sorted(classes)}"
        (keys,) = classes.values()
        assert keys is not None, f"{name}: declare MODULE_KEYS on the class, even empty"


def _declared(probe) -> dict[str, list[str]]:
    return {
        name: next(iter(classes.values())) or [] for name, classes in probe["extensions"].items()
    }


def test_declared_modules_have_a_schema(probe):
    schemas = set(probe["schemas"])
    for name, keys in _declared(probe).items():
        unknown = set(keys) - schemas
        assert not unknown, f"{name} declares modules no schema registers: {sorted(unknown)}"


def test_every_module_schema_has_an_extension(probe):
    declared = {key for keys in _declared(probe).values() for key in keys}
    orphans = set(probe["schemas"]) - declared - UNOWNED_MODULES
    assert not orphans, f"no extension declares {sorted(orphans)}"


def _source_files(name: str) -> list[Path]:
    entry = name.removeprefix("extensions.")
    single = EXTENSIONS_DIR / f"{entry}.py"
    return [single] if single.exists() else sorted((EXTENSIONS_DIR / entry).rglob("*.py"))


def test_module_keys_named_in_an_extension_are_declared(probe):
    # A safety net, not the source of truth: a module key spelled out anywhere
    # in an extension's files (load_config("…"), a schema, a field save) means
    # the extension reads that module.
    for name, keys in _declared(probe).items():
        named: set[str] = set()
        for path in _source_files(name):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and MODULE_KEY.match(node.value)
                ):
                    named.add(node.value)
        missing = named - set(keys)
        assert not missing, f"{name} reads {sorted(missing)} without declaring it in MODULE_KEYS"


# ── The map the dashboard reloads from ──────────────────────────────────


def _ext(path: str, *keys: str) -> SimpleNamespace:
    return SimpleNamespace(extension_name=path, MODULE_KEYS=keys)


def test_module_map_comes_from_the_loaded_extensions():
    bot = SimpleNamespace(
        ext={
            "Reminders": _ext("extensions.reminders", "moduleUtils"),
            "Admin": _ext("extensions.admin", "moduleUtils"),
            "Metrics": _ext("extensions.metrics", "moduleSpotify", "moduleXp"),
            "Xp": _ext("extensions.xp", "moduleXp"),
            "Random": SimpleNamespace(extension_name="extensions.random_"),  # declares nothing
        }
    )
    ctx = WebUIContext(bot=bot, bot_loop=None, oauth=None)  # type: ignore[arg-type]

    assert ctx.module_extension_map() == {
        "moduleUtils": ["extensions.admin", "extensions.reminders"],
        "moduleSpotify": ["extensions.metrics"],
        "moduleXp": ["extensions.metrics", "extensions.xp"],
    }
    assert ctx.get_extension_module_paths() == [
        "extensions.reminders",
        "extensions.admin",
        "extensions.metrics",
        "extensions.xp",
        "extensions.random_",
    ]


def test_module_map_is_empty_without_a_bot():
    assert WebUIContext(bot=None, bot_loop=None, oauth=None).module_extension_map() == {}  # type: ignore[arg-type]


def test_extension_helpers_fall_back_to_the_class():
    class Plain:
        pass

    plain = Plain()
    assert extension_path(plain) == __name__
    assert declared_modules(plain) == ()
