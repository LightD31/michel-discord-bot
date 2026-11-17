"""The modules mypy still skips in extensions/ (pyproject.toml) exist (Q01).

The list only shrinks: a module renamed or removed must leave it too, or its
successor would be type-checked under a name nobody skips while the stale line
hides the next file to take that name.
"""

import tomllib

from source_scan import ROOT

EXCLUSION_NOTE = "extensions/ is checked too"


def excluded_modules() -> list[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    overrides = config["tool"]["mypy"]["overrides"]
    (skipped,) = [o for o in overrides if o.get("ignore_errors")]
    return list(skipped["module"])


def test_every_skipped_module_exists_and_is_listed_once():
    modules = excluded_modules()
    assert len(modules) == len(set(modules))
    missing = [
        m
        for m in modules
        if not (ROOT / (m.replace(".", "/") + ".py")).is_file()
        and not (ROOT / m.replace(".", "/") / "__init__.py").is_file()
    ]
    assert missing == [], f"remove them from the mypy overrides: {missing}"


def test_only_extensions_are_skipped():
    assert all(m.startswith("extensions.") for m in excluded_modules())
    assert len(excluded_modules()) <= 64  # when the list was made; lower it as it shrinks
