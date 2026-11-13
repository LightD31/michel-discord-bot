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
            classes[cls_name] = {
                "keys": None if own is None else list(own),
                "reload_on_save": cls.__dict__.get("RELOAD_ON_SAVE", False),
            }
    out["extensions"][name] = classes
out["schemas"] = sorted(schemas.MODULE_SCHEMAS)
out["fields"] = {key: list(cls.model_fields) for key, cls in schemas._MODULE_REGISTRY.items()}
out["module_schemas"] = schemas.MODULE_SCHEMAS
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


def test_no_module_schema_declares_a_message_id(probe):
    """Where the bot's messages live is runtime state: ``bot_messages``, not config.json."""
    offenders = [
        f"{key}.{name}"
        for key, names in probe["fields"].items()
        for name in names
        if name.lower().endswith("messageid")
    ]
    assert offenders == []


def test_module_secrets_name_where_they_are_sent(probe):
    """A guild admin who re-points a secret must type it again (``secret_field(sent_to=…)``).

    Without ``sent_to``, the masked placeholder would carry the stored secret to
    whatever host a co-admin just entered.
    """
    offenders = []
    for key, schema in probe["module_schemas"].items():
        fields = schema["fields"]
        for name, meta in fields.items():
            if not meta.get("secret"):
                continue
            targets = meta.get("sentTo") or []
            if not targets:
                offenders.append(f"{key}.{name}: no sent_to")
            offenders.extend(
                f"{key}.{name}: sent_to names unknown field {target}"
                for target in targets
                if target not in fields
            )
    assert offenders == []


def test_module_roles_say_what_the_bot_does_with_them(probe):
    """Every role field declares ``ui(role_use=…)``: it picks the rule a save must pass.

    Without it a dashboard save could name an administrator role the bot then
    hands out, or a role it pings, with nothing checked (``schemas.ROLE_USES``).
    """
    offenders = [
        f"{key}.{name}"
        for key, schema in probe["module_schemas"].items()
        for name, meta in schema["fields"].items()
        if meta.get("type") == "role" and not meta.get("roleUse")
    ]
    assert offenders == []


def test_every_extension_declares_its_modules_explicitly(probe):
    for name, classes in probe["extensions"].items():
        assert len(classes) == 1, f"{name} should define one Extension class: {sorted(classes)}"
        (declared,) = classes.values()
        assert declared["keys"] is not None, f"{name}: declare MODULE_KEYS on the class, even empty"
        assert isinstance(declared["reload_on_save"], bool), f"{name}: RELOAD_ON_SAVE is a bool"


def _declared(probe) -> dict[str, list[str]]:
    return {
        name: next(iter(classes.values()))["keys"] or []
        for name, classes in probe["extensions"].items()
    }


def _reloads_on_save(probe) -> set[str]:
    return {
        name
        for name, classes in probe["extensions"].items()
        if next(iter(classes.values()))["reload_on_save"]
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


# ── Live reads ──────────────────────────────────────────────────────────

# Reading these at import takes a copy that a save would leave stale.
SNAPSHOT_READS = {"get", "items", "enabled"}


def _import_time_reads(path: Path) -> list[str]:
    """``<ModuleConfig>.get/items/enabled(...)`` calls run when *path* is imported.

    Anything inside a function runs later and reads the live config;
    ``guild_ids()`` is allowed at import (command scopes, per-guild state),
    since a change of the enabled guilds reloads the extension anyway.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    accessors = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "ModuleConfig"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    found: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            return  # runs later, against the live config
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SNAPSHOT_READS
            and isinstance(node.func.value, ast.Name)
            and (node.func.value.id in accessors or node.func.value.id.isupper())
        ):
            where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
            found.append(f"{where}:{node.lineno}: {ast.unparse(node)}")
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def test_extensions_read_their_settings_live_unless_they_reload_on_save(probe):
    # An extension that copies its settings at import would ignore every
    # dashboard save, now that a save no longer reloads it: either read them
    # through ModuleConfig at use time, or declare RELOAD_ON_SAVE.
    snapshotting = _reloads_on_save(probe)
    offenders = [
        line
        for name in _declared(probe)
        if name not in snapshotting
        for path in _source_files(name)
        for line in _import_time_reads(path)
    ]
    assert not offenders, "\n".join(offenders)


def test_import_time_reads_are_found(tmp_path):
    source = tmp_path / "ext.py"
    source.write_text(
        "XP = ModuleConfig('moduleXp')\n"
        "SETTINGS = XP.get('1')\n"
        "SCOPES = guild_scopes(XP.guild_ids())\n"
        "class Ext:\n"
        "    LIMIT = XP.items()\n"
        "    def run(self):\n"
        "        return XP.get('1')\n",
        encoding="utf-8",
    )
    found = _import_time_reads(source.resolve())
    assert [line.rsplit(": ", 1)[1] for line in found] == ["XP.get('1')", "XP.items()"]


# CodeQL's clear-text logging query treats a value as sensitive when the name
# it came through looks sensitive (a subset of its heuristics). Every guild id
# read through BIRTHDAY.guild_ids() then counted as personal data, and each
# log line printing one became a high-severity alert.
SENSITIVE_LOOKING = re.compile(
    r"(?i)secret|pass(wd|word|code|phrase)|token|auth|birth.?da(te|y)|da(te|y).?(of.?)?birth"
    r"|private|acc(ou)?nt|cert|ssn|salary|medical|credit|session|api.?key"
)


def test_module_accessor_names_do_not_look_sensitive():
    offenders = []
    for path in sorted(EXTENSIONS_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "ModuleConfig"
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name) and SENSITIVE_LOOKING.search(target.id):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {target.id}")
    assert not offenders, "\n".join(offenders)
