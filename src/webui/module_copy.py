"""A module's settings copied from another server (P35).

What can go from one guild to another: everything but what names one guild's
objects (channels, forums, roles, members, and the per-entry keys of the list
widgets that hold them) and the module's on/off switch, which stay as the
target has them. Secrets go only for the source's owner or an administrator
there: a ``MANAGE_GUILD`` co-admin can't read a server's secrets, so a copy
mustn't let them use those secrets on a server they run.
"""

from __future__ import annotations

import copy
from typing import Any

from src.webui.schemas import NESTED_CHANNEL_KEYS, NESTED_ROLE_KEYS
from src.webui.secrets import secret_field_names

# Field types whose whole value names a guild's objects.
GUILD_BOUND_TYPES = frozenset(
    {"channel", "forum", "channellist", "role", "rolelist", "member", "spotifymap", "discord2name"}
)


def _guild_bound(meta: dict[str, Any]) -> bool:
    kind = meta.get("type")
    if kind in GUILD_BOUND_TYPES:
        return True
    # A map whose keys or values are roles or members (XP rewards, LLDAP groups).
    return kind == "keyvaluemap" and bool(
        meta.get("roleUse") or meta.get("roleKeys") or meta.get("memberKeys")
    )


def _nested_keys(kind: str) -> tuple[str, ...]:
    return (*NESTED_CHANNEL_KEYS.get(kind, ()), *NESTED_ROLE_KEYS.get(kind, {}))


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def copied_settings(
    schema: dict[str, Any] | None,
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    with_secrets: bool,
) -> tuple[dict[str, Any], list[str]]:
    """*source*'s settings as they can go to the target server.

    Returns ``(settings, to_fill)``: the target's own channels, roles, members
    and switch stay; ``to_fill`` lists the labels of the fields the source had
    and the copy left for the target's admins to fill (its channels, roles…,
    and its secrets when they don't come along).
    """
    fields: dict[str, Any] = (schema or {}).get("fields") or {}
    secrets = secret_field_names(schema)
    out = copy.deepcopy(source)
    to_fill: list[str] = []

    def keep_target(name: str) -> None:
        if _has_value(source.get(name)) and not _has_value(target.get(name)):
            to_fill.append(str(fields.get(name, {}).get("label") or name))
        if name in target:
            out[name] = copy.deepcopy(target[name])
        else:
            out.pop(name, None)

    # A secret that stays the target's keeps where it is sent, or the copy would
    # point it at the source's host (``sent_to``, ``src/webui/secrets.py``).
    kept_with_secrets: set[str] = set()
    if not with_secrets:
        for name in secrets:
            kept_with_secrets.update(fields.get(name, {}).get("sentTo") or ())

    for name, meta in fields.items():
        if not isinstance(meta, dict):
            continue
        if (
            _guild_bound(meta)
            or (name in secrets and not with_secrets)
            or name in kept_with_secrets
        ):
            keep_target(name)
            continue
        nested = _nested_keys(str(meta.get("type") or ""))
        if nested and isinstance(out.get(name), dict | list):
            entries = out[name].values() if isinstance(out[name], dict) else out[name]
            emptied = False
            for entry in entries:
                if isinstance(entry, dict):
                    for key in nested:
                        if _has_value(entry.pop(key, None)):
                            emptied = True
            if emptied:
                to_fill.append(str(meta.get("label") or name))
    if "enabled" in target:
        out["enabled"] = target["enabled"]
    else:
        out.pop("enabled", None)
    return out, to_fill


def holds_secrets(schema: dict[str, Any] | None, settings: dict[str, Any]) -> bool:
    return any(_has_value(settings.get(name)) for name in secret_field_names(schema))


__all__ = ["GUILD_BOUND_TYPES", "copied_settings", "holds_secrets"]
