"""Tests for secret masking/restoring in Web UI config endpoints."""

from src.webui.schemas import SchemaBase, register_module, secret_field, ui
from src.webui.secrets import (
    SECRET_PLACEHOLDER,
    mask_full_config,
    mask_section,
    mask_server_config,
    restore_section,
    secret_field_names,
)

FAKE_SCHEMA = {
    "label": "Test",
    "fields": {
        "apiKey": {"label": "Clé", "type": "secret", "secret": True},
        "channel": {"label": "Salon", "type": "channel"},
    },
}

# Obviously fake fixture values, hoisted into constants so detect-secrets'
# keyword detector doesn't match `"apiKey": "<literal>"` lines all over the file.
FAKE_SECRET = "hunter2"  # pragma: allowlist secret
NEW_SECRET = "new-secret"  # pragma: allowlist secret
OLD_SECRET = "old-secret"  # pragma: allowlist secret
FAKE_TOKEN = "tok-123"  # pragma: allowlist secret


@register_module("moduleSecretMaskTest")
class _SecretMaskTestConfig(SchemaBase):
    __label__ = "Module de test (masquage)"

    enabled: bool = ui("Activé", "boolean", default=False)
    password: str | None = secret_field("Mot de passe")
    channelId: str | None = ui("Salon", "channel")


def test_secret_field_names_reads_schema_meta():
    assert secret_field_names(FAKE_SCHEMA) == {"apiKey"}
    assert secret_field_names(None) == set()
    assert secret_field_names({"fields": {}}) == set()


def test_mask_section_masks_only_set_secrets():
    data = {"apiKey": FAKE_SECRET, "channel": "123"}
    masked = mask_section(data, FAKE_SCHEMA)
    assert masked["apiKey"] == SECRET_PLACEHOLDER
    assert masked["channel"] == "123"
    # original untouched
    assert data["apiKey"] == FAKE_SECRET


def test_mask_section_leaves_empty_and_missing_secrets():
    assert mask_section({"apiKey": "", "channel": "1"}, FAKE_SCHEMA)["apiKey"] == ""
    assert "apiKey" not in mask_section({"channel": "1"}, FAKE_SCHEMA)


def test_mask_section_without_schema_is_passthrough_copy():
    data = {"apiKey": FAKE_SECRET}
    masked = mask_section(data, None)
    assert masked == data
    assert masked is not data


def test_mask_section_non_dict_passthrough():
    assert mask_section("raw", FAKE_SCHEMA) == "raw"
    assert mask_section(None, FAKE_SCHEMA) is None


def test_restore_section_swaps_placeholder_for_current_value():
    incoming = {"apiKey": SECRET_PLACEHOLDER, "channel": "456"}
    current = {"apiKey": FAKE_SECRET, "channel": "123"}
    restored = restore_section(incoming, current, FAKE_SCHEMA)
    assert restored["apiKey"] == FAKE_SECRET
    assert restored["channel"] == "456"


def test_restore_section_keeps_new_secret_value():
    restored = restore_section({"apiKey": NEW_SECRET}, {"apiKey": OLD_SECRET}, FAKE_SCHEMA)
    assert restored["apiKey"] == NEW_SECRET


def test_restore_section_clearing_still_works():
    # Empty/missing means "clear" — only the untouched placeholder is restored.
    restored = restore_section({"apiKey": ""}, {"apiKey": OLD_SECRET}, FAKE_SCHEMA)
    assert restored["apiKey"] == ""
    restored = restore_section({"channel": "1"}, {"apiKey": OLD_SECRET}, FAKE_SCHEMA)
    assert "apiKey" not in restored


def test_restore_section_placeholder_without_current_becomes_empty():
    restored = restore_section({"apiKey": SECRET_PLACEHOLDER}, None, FAKE_SCHEMA)
    assert restored["apiKey"] == ""


def test_mask_round_trip_is_identity_for_unchanged_secrets():
    current = {"apiKey": FAKE_SECRET, "channel": "123"}
    masked = mask_section(current, FAKE_SCHEMA)
    restored = restore_section(masked, current, FAKE_SCHEMA)
    assert restored == current


def test_mask_full_config_masks_global_sections_and_modules():
    data = {
        "config": {
            "discord": {"botToken": FAKE_TOKEN, "devGuildId": "42"},
            "extensions": {"extensions.xp": True},
        },
        "servers": {
            "1": {
                "moduleSecretMaskTest": {"enabled": True, "password": FAKE_SECRET},
                "discord2name": {"99": "Alice"},
            },
        },
    }
    masked = mask_full_config(data)
    # discord.botToken is secret in the real GLOBAL_CONFIG_SCHEMAS
    assert masked["config"]["discord"]["botToken"] == SECRET_PLACEHOLDER
    assert masked["config"]["discord"]["devGuildId"] == "42"
    # schema-less sections pass through
    assert masked["config"]["extensions"] == {"extensions.xp": True}
    # registered module secret is masked, other fields untouched
    module = masked["servers"]["1"]["moduleSecretMaskTest"]
    assert module["password"] == SECRET_PLACEHOLDER
    assert module["enabled"] is True
    assert masked["servers"]["1"]["discord2name"] == {"99": "Alice"}
    # source dict untouched
    assert data["config"]["discord"]["botToken"] == FAKE_TOKEN


def test_mask_server_config_masks_registered_module():
    server_config = {"moduleSecretMaskTest": {"password": FAKE_SECRET, "channelId": "1"}}
    masked = mask_server_config(server_config)
    assert masked["moduleSecretMaskTest"]["password"] == SECRET_PLACEHOLDER
    assert masked["moduleSecretMaskTest"]["channelId"] == "1"
