"""Commands speak French (P6, B6): ``/niveau``, ``/sondage``, ``/musique ajouter titre:``.

Every guild command, subcommand group, subcommand and option is named in
French, without accents, words joined by hyphens (``/mes-donnees``,
``/lever-exclusion``; an option may join them with ``_``, as Python does).
English names are only for the commands anyone may install, which carry a
French localisation (``random_``, ``/ask``: ``src/discord_ext/locale.py``).

The check is a list of English words seen in commands, plus the shape of a
name; a new command named in English with a word not on the list gets past
it, so add the word.
"""

import json
import re
import subprocess
import sys

import pytest
from source_scan import ROOT

# English words that named a command, a group or an option before P6, and their kin.
ENGLISH = {
    "add",
    "after",
    "allow",
    "approve",
    "ban",
    "banpair",
    "before",
    "cancel",
    "channel",
    "check",
    "choices",
    "count",
    "create",
    "delete",
    "deny",
    "draw",
    "edit",
    "editpoll",
    "end",
    "endcharitycount",
    "entries",
    "faces",
    "feed",
    "feurstats",
    "finish",
    "id_",
    "implement",
    "info_",
    "kick",
    "leaderboard",
    "list",
    "listbans",
    "lock",
    "max",
    "menu_id",
    "name",
    "pair",
    "pin",
    "poll",
    "prize",
    "purge",
    "rank",
    "register",
    "remind",
    "reminder",
    "remove",
    "reroll",
    "reset",
    "reveal",
    "send",
    "sensor",
    "set",
    "setup",
    "slowmode",
    "song",
    "songinfo",
    "start",
    "status",
    "suggest",
    "sync",
    "test",
    "timeout",
    "timezone",
    "title",
    "unban",
    "unbanpair",
    "unlock",
    "untimeout",
    "user",
    "username",
    "view",
    "vote",
    "warn",
    "with",
    "addsong",
    "addwithvote",
    "rappelvote",
    "hideyear",
    "subgroups",
    "backup",
    "backups",
    "member",
    "separator",
}
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
OPTION = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)*$")

_PROBE = r"""
import importlib, inspect, json, os, sys

import src.core.config as config

config.CONFIG_PATH = "config.example.json"

from interactions import SlashCommand

found = []
for entry in sorted(os.listdir("extensions")):
    name = entry.removesuffix(".py")
    if name.startswith("_") or name == "__pycache__":
        continue
    module = importlib.import_module("extensions." + name)
    for cls in vars(module).values():
        if not inspect.isclass(cls):
            continue
        for klass in cls.__mro__:
            for obj in vars(klass).values():
                if not isinstance(obj, SlashCommand):
                    continue
                localised = bool(getattr(obj.name, "french", None))
                found.append({
                    "extension": name,
                    "path": [str(p) for p in (obj.name, obj.group_name, obj.sub_cmd_name) if p],
                    "options": [str(o.name) for o in obj.options or []],
                    "localised": localised,
                })
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(found, f)
"""


@pytest.fixture(scope="module")
def commands(tmp_path_factory) -> list[dict]:
    target = tmp_path_factory.mktemp("names") / "commands.json"
    subprocess.run(
        [sys.executable, "-c", _PROBE, str(target)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads(target.read_text(encoding="utf-8"))


def english_words(name: str) -> set[str]:
    return {w for w in re.split(r"[-_]", name) if w in ENGLISH} | ({name} & ENGLISH)


def offenders(commands: list[dict]) -> list[str]:
    found = []
    for command in commands:
        if command["localised"]:
            continue  # installable anywhere: English with a French localisation
        where = "/" + " ".join(command["path"])
        for part in command["path"]:
            if not NAME.match(part) or english_words(part):
                found.append(f"{where}: {part}")
        for option in command["options"]:
            if not OPTION.match(option) or english_words(option):
                found.append(f"{where} {option}:")
    return sorted(set(found))


def test_commands_and_options_are_named_in_french(commands):
    assert commands, "the probe found no command"
    assert offenders(commands) == []


def test_the_probe_sees_the_renamed_commands(commands):
    paths = {" ".join(c["path"]) for c in commands}
    assert {"niveau", "classement", "musique ajouter", "tricount bilan", "sondage"} <= paths


@pytest.mark.parametrize(
    ("path", "options"),
    [
        (["rank"], []),
        (["giveaway", "start"], ["prize"]),
        (["zevent_finish"], []),
        (["poll"], ["question", "options"]),
        (["sondage"], ["reset_reactions"]),
        (["anniversaire"], ["hideyear"]),
        (["sondage"], ["Question"]),
        (["concours", "liste"], ["channel"]),
    ],
)
def test_english_or_misshapen_names_are_caught(path, options):
    command = {"path": path, "options": options, "localised": False}
    assert offenders([command]) != []


def test_a_french_command_passes():
    command = {
        "path": ["tricount", "recurrente", "ajouter"],
        "options": ["groupe", "montant", "depense_id"],
        "localised": False,
    }
    assert offenders([command]) == []


def test_a_localised_command_may_keep_its_english_name():
    assert offenders([{"path": ["pick"], "options": ["choices"], "localised": True}]) == []
