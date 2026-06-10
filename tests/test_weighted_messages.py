"""Tests for the merged ``[{"text", "weight"}]`` message-list data model.

Covers the runtime picker (object entries, legacy-string tolerance, degenerate
weights), the startup migration from parallel arrays, and the schemas no
longer referencing sibling weight keys.
"""

import pytest

from src.core.migrations import _merge_weighted_message_lists
from src.core.text import pick_weighted_message

# ── Runtime picker ──────────────────────────────────────────────────────


def test_picker_uses_object_entries():
    config = {"msgs": [{"text": "salut {name}", "weight": 3}]}
    assert pick_weighted_message(config, "msgs", "défaut", name="Michel") == "salut Michel"


def test_picker_falls_back_to_default():
    assert pick_weighted_message({}, "msgs", "défaut {x}", x=1) == "défaut 1"
    assert pick_weighted_message({"msgs": []}, "msgs", "défaut") == "défaut"
    # Entries without usable text are skipped entirely
    assert pick_weighted_message({"msgs": [{"weight": 2}, 42]}, "msgs", "défaut") == "défaut"


def test_picker_tolerates_legacy_strings_and_bad_weights():
    config = {"msgs": ["legacy", {"text": "objet", "weight": "pas-un-nombre"}]}
    for _ in range(20):
        assert pick_weighted_message(config, "msgs", "défaut") in ("legacy", "objet")


def test_picker_respects_zero_weight():
    config = {"msgs": [{"text": "jamais", "weight": 0}, {"text": "toujours", "weight": 5}]}
    for _ in range(50):
        assert pick_weighted_message(config, "msgs", "défaut") == "toujours"


def test_picker_survives_all_zero_weights():
    # random.choices raises on an all-zero total; the picker must not.
    config = {"msgs": [{"text": "a", "weight": 0}, {"text": "b", "weight": 0}]}
    assert pick_weighted_message(config, "msgs", "défaut") in ("a", "b")


# ── Startup migration ───────────────────────────────────────────────────


def _data(module: str, **module_cfg) -> dict:
    return {"servers": {"123": {module: module_cfg}}}


def test_migration_zips_messages_with_weights():
    data = _data(
        "moduleWelcome",
        welcomeMessageList=["a", "b"],
        welcomeMessageWeights=[2, 5],
    )
    assert _merge_weighted_message_lists(data) is True
    cfg = data["servers"]["123"]["moduleWelcome"]
    assert cfg["welcomeMessageList"] == [
        {"text": "a", "weight": 2},
        {"text": "b", "weight": 5},
    ]
    assert "welcomeMessageWeights" not in cfg


@pytest.mark.parametrize(
    ("weights", "expected"),
    [
        ([2], [2, 1]),  # missing weights padded with 1
        ([2, 5, 9], [2, 5]),  # surplus weights dropped
        (["x", None], [1, 1]),  # non-numeric weights reset to 1
        (None, [1, 1]),  # weights key absent entirely
    ],
)
def test_migration_handles_mismatched_weights(weights, expected):
    module_cfg = {"birthdayMessageList": ["a", "b"]}
    if weights is not None:
        module_cfg["birthdayMessageWeights"] = weights
    data = {"servers": {"123": {"moduleBirthday": module_cfg}}}
    assert _merge_weighted_message_lists(data) is True
    merged = data["servers"]["123"]["moduleBirthday"]["birthdayMessageList"]
    assert [m["weight"] for m in merged] == expected


def test_migration_is_idempotent():
    data = _data(
        "moduleXp",
        levelUpMessageList=["gg {mention}"],
        levelUpMessageWeights=[1],
    )
    assert _merge_weighted_message_lists(data) is True
    after_first = data["servers"]["123"]["moduleXp"]["levelUpMessageList"]
    assert _merge_weighted_message_lists(data) is False
    assert data["servers"]["123"]["moduleXp"]["levelUpMessageList"] == after_first


def test_migration_drops_orphan_weights_key():
    data = _data("moduleWelcome", welcomeMessageWeights=[1, 2])
    assert _merge_weighted_message_lists(data) is True
    assert "welcomeMessageWeights" not in data["servers"]["123"]["moduleWelcome"]


def test_migration_ignores_untracked_modules_and_bad_shapes():
    data = {"servers": {"123": {"moduleFeur": {"x": 1}, "moduleWelcome": "oops"}}}
    assert _merge_weighted_message_lists(data) is False


# ── Schemas ─────────────────────────────────────────────────────────────


def test_schemas_no_longer_reference_weight_keys():
    # Importing the extensions registers their @register_module schemas.
    import extensions.birthday  # noqa: F401
    import extensions.welcome  # noqa: F401
    import extensions.xp._common  # noqa: F401
    from src.webui import schemas as schemas_module

    module_schemas = schemas_module.MODULE_SCHEMAS
    for module, list_field in [
        ("moduleWelcome", "welcomeMessageList"),
        ("moduleBirthday", "birthdayMessageList"),
        ("moduleXp", "levelUpMessageList"),
    ]:
        fields = module_schemas[module]["fields"]
        assert "weightField" not in fields[list_field]
        assert not any(k.endswith("MessageWeights") for k in fields)
        # Defaults follow the merged shape
        default = fields[list_field].get("default")
        assert default and all(isinstance(m, dict) and "text" in m for m in default)
