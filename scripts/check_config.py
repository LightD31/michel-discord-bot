"""Check ``config/config.json`` against the Web UI schemas.

The dashboard refuses to save a module or a global section that doesn't match
its schema (unknown keys, wrong types). This script runs the same check over
the whole file, so a hand-edited value or a key left over from an older
version shows up before a save trips on it.

Unknown keys can be removed from the dashboard ("Nettoyer la configuration").
Anything else is reported with its location so it can be fixed by hand.

Usage (from the repository root, or inside the container)::

    python scripts/check_config.py                     # checks config/config.json
    python scripts/check_config.py path/to/config.json

Exits 1 when something doesn't validate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.generate_config_example import _import_extensions  # noqa: E402


def check(data: dict) -> list[str]:
    """Every validation problem in *data*, as ``"location: field: message"`` lines."""
    from src.webui import schemas

    problems: list[str] = []
    for section, value in sorted((data.get("config") or {}).items()):
        model = schemas.section_model(section)
        if model is None:
            continue  # not editable from the dashboard
        for error in schemas.validation_errors(model, value, module=False):
            problems.append(f"config.{section}: {error}")
    for server_id, server in sorted((data.get("servers") or {}).items()):
        if not isinstance(server, dict):
            continue
        for module_name, value in sorted(server.items()):
            model = schemas.module_model(module_name)
            if model is None:
                continue  # not a schema-backed module (e.g. the public dashboard token)
            for error in schemas.validation_errors(model, value, module=True):
                problems.append(f"servers.{server_id}.{module_name}: {error}")
    return problems


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/config.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    failures = _import_extensions()
    for failure in failures:
        print(f"warning: could not import {failure} (its schema is not checked)", file=sys.stderr)
    problems = check(data)
    for problem in problems:
        print(problem)
    if problems:
        raise SystemExit(f"{len(problems)} problème(s) dans {path}")
    print(f"{path} : OK")


if __name__ == "__main__":
    main()
