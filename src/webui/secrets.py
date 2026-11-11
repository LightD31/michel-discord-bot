"""Masking of secret config fields in API responses.

Fields declared with ``secret=True`` in a Pydantic schema (see
:mod:`src.webui.schemas`) are replaced by :data:`SECRET_PLACEHOLDER` before a
config dict leaves the API. The SPA round-trips form values verbatim, so on
save the placeholder comes back and :func:`restore_section` swaps the real
value back in — secrets are effectively write-only from the dashboard.

Schema dicts are read lazily from :mod:`src.webui.schemas` (PEP 562
attributes) so masking always reflects every module registered at call time.
"""

from typing import Any

SECRET_PLACEHOLDER = "••••••••"


def secret_field_names(schema: dict[str, Any] | None) -> set[str]:
    """Field names flagged ``secret`` in a module/section schema dict."""
    if not schema:
        return set()
    fields = schema.get("fields") or {}
    return {name for name, meta in fields.items() if isinstance(meta, dict) and meta.get("secret")}


def mask_section(data: Any, schema: dict[str, Any] | None) -> Any:
    """Return a copy of *data* with non-empty secret fields replaced by the placeholder."""
    if not isinstance(data, dict):
        return data
    secret_names = secret_field_names(schema)
    if not secret_names:
        return dict(data)
    masked = dict(data)
    for name in secret_names:
        value = masked.get(name)
        if isinstance(value, str) and value:
            masked[name] = SECRET_PLACEHOLDER
    return masked


class SecretRebindError(ValueError):
    """A masked secret came back while the place it is sent to changed.

    The message is user-facing (French) and names the field to fill in again.
    """


def _setting(section: dict[str, Any], name: str) -> str:
    value = section.get(name)
    return "" if value is None else str(value).strip()


def restore_section(
    incoming: dict[str, Any], current: Any, schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Return *incoming* with placeholder secrets replaced by their *current* values.

    A secret field whose submitted value is the untouched placeholder means
    "keep the existing secret"; an empty/missing field still clears it.

    Raises :class:`SecretRebindError` when a placeholder comes back for a
    secret whose ``sentTo`` fields (its URL, host, port…) changed: the stored
    value is only ever sent where it was entered for, so moving it means
    typing it again.
    """
    restored = dict(incoming)
    current_dict = current if isinstance(current, dict) else {}
    fields = (schema or {}).get("fields") or {}
    for name in secret_field_names(schema):
        if restored.get(name) != SECRET_PLACEHOLDER:
            continue
        meta = fields.get(name) or {}
        moved = [
            target
            for target in meta.get("sentTo") or ()
            if _setting(restored, target) != _setting(current_dict, target)
        ]
        if moved:
            label = meta.get("label") or name
            raise SecretRebindError(
                f"Saisissez à nouveau « {label} » : l'adresse à laquelle il est envoyé a changé."
            )
        restored[name] = current_dict.get(name, "")
    return restored


def mask_full_config(data: dict[str, Any]) -> dict[str, Any]:
    """Mask every secret in a full ``{"config": ..., "servers": ...}`` dict.

    Global sections are masked per ``GLOBAL_CONFIG_SCHEMAS``; per-guild module
    configs per ``MODULE_SCHEMAS``. Unknown sections/modules pass through
    unchanged (their fields are not known to be secret).
    """
    from src.webui import schemas

    section_schemas = schemas.GLOBAL_CONFIG_SCHEMAS
    module_schemas = schemas.MODULE_SCHEMAS

    masked: dict[str, Any] = dict(data)

    global_config = data.get("config")
    if isinstance(global_config, dict):
        masked["config"] = {
            section: mask_section(section_data, section_schemas.get(section))
            for section, section_data in global_config.items()
        }

    servers = data.get("servers")
    if isinstance(servers, dict):
        masked["servers"] = {
            server_id: {
                module: mask_section(module_data, module_schemas.get(module))
                for module, module_data in server_config.items()
            }
            if isinstance(server_config, dict)
            else server_config
            for server_id, server_config in servers.items()
        }

    return masked


def mask_server_config(server_config: Any) -> Any:
    """Mask secret module fields in a single per-guild config dict."""
    if not isinstance(server_config, dict):
        return server_config
    from src.webui import schemas

    module_schemas = schemas.MODULE_SCHEMAS
    return {
        module: mask_section(module_data, module_schemas.get(module))
        for module, module_data in server_config.items()
    }
