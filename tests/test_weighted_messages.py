"""Tests for the merged ``[{"text", "weight"}]`` message-list data model.

Covers the runtime picker (object entries, legacy-string tolerance, degenerate
weights) and the schemas no longer referencing sibling weight keys.
"""

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
