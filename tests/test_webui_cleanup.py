"""Regression tests: weight keys must be first-class schema fields.

The config cleanup flagged ``*MessageWeights`` keys as orphans: weights are
stored under their own config key but only appeared in schemas as
``weightField`` metadata on the messagelist field. The registry now
materializes those references as hidden fields, so every schema consumer
(cleanup keep-list, previews, …) sees them as module-owned.
"""

from src.webui.schemas import SchemaBase, _fields_of, ui


class _DemoSchema(SchemaBase):
    __label__ = "Demo"

    messages: list[str] | None = ui("Messages", "messagelist", weight_field="messageWeights")
    channelId: str | None = ui("Salon", "channel")


def test_weight_field_reference_becomes_hidden_field():
    fields = _fields_of(_DemoSchema)
    assert set(fields) == {"messages", "channelId", "messageWeights"}
    weights = fields["messageWeights"]
    assert weights["hidden"] is True
    assert weights["type"] == "list:number"


class _ExplicitWeights(SchemaBase):
    __label__ = "Demo"

    messages: list[str] | None = ui("Messages", "messagelist", weight_field="messageWeights")
    messageWeights: list[float] | None = ui("Poids", "list:number")


def test_explicitly_declared_weight_field_is_not_overridden():
    fields = _fields_of(_ExplicitWeights)
    assert fields["messageWeights"]["label"] == "Poids"
    assert "hidden" not in fields["messageWeights"]


def test_real_schemas_expose_the_flagged_weight_keys():
    """The five keys the cleanup dry-run wanted to delete must be fields now."""
    # Importing the extensions registers their @register_module schemas.
    import extensions.birthday  # noqa: F401
    import extensions.welcome  # noqa: F401
    import extensions.xp._common  # noqa: F401
    from src.webui import schemas as schemas_module

    module_schemas = schemas_module.MODULE_SCHEMAS
    expectations = {
        "moduleWelcome": {"welcomeMessageWeights", "leaveMessageWeights"},
        "moduleBirthday": {"birthdayMessageWeights"},
        "moduleXp": {"levelUpMessageWeights"},
    }
    for module_name, weight_keys in expectations.items():
        fields = module_schemas[module_name]["fields"]
        # Same keep-list expression as /api/cleanup-config
        allowed = set(fields.keys()) | {"enabled"}
        missing = weight_keys - allowed
        assert not missing, f"{module_name}: cleanup would delete {missing}"
        for key in weight_keys:
            assert fields[key].get("hidden") is True
