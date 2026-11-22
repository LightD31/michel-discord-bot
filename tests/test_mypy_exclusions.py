"""mypy checks every module (D4): no override skips one again.

The 64 extension modules mypy once skipped (``ignore_errors``) are all typed
now; a new module with errors gets them fixed, not a line in pyproject.toml.
"""

import tomllib

from source_scan import ROOT


def mypy_overrides() -> list[dict]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["mypy"].get("overrides", [])


def test_no_module_is_skipped():
    skipping = [o.get("module") for o in mypy_overrides() if o.get("ignore_errors")]
    assert skipping == [], f"fix the errors instead of skipping: {skipping}"


def test_no_extension_is_left_out_of_the_check():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = config["tool"]["mypy"].get("exclude", [])
    # Only the parked work-in-progress modules (extensions/_*) stay out.
    assert [p for p in excluded if "extensions" in p] == ["^extensions/_"]
    assert not [o for o in mypy_overrides() if o.get("follow_imports") == "skip"]
