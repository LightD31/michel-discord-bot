"""Check ``config/config.json`` against the Web UI schemas.

The dashboard refuses to save a module or a global section that doesn't match
its schema (unknown keys, wrong types). This script runs the same check over
the whole file, so a hand-edited value or a key left over from an older
version shows up before a save trips on it.

Unknown keys can be removed from the dashboard ("Nettoyer la configuration").
Anything else is reported with its location so it can be fixed by hand. The
bot runs the same check at startup and logs what it finds as warnings.

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


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/config.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    failures = _import_extensions()
    for failure in failures:
        print(f"warning: could not import {failure} (its schema is not checked)", file=sys.stderr)
    from src.webui.schemas import config_problems

    problems = config_problems(data)
    for problem in problems:
        print(problem)
    if problems:
        raise SystemExit(f"{len(problems)} problème(s) dans {path}")
    print(f"{path} : OK")


if __name__ == "__main__":
    main()
