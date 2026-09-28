"""Read Notion page properties and build property payloads (no Discord imports).

Every reader is tolerant of the property's actual type: a column the owner
turns from ``select`` into ``multi_select`` (as happened with ``Type``) keeps
rendering instead of silently coming back empty.
"""

from collections.abc import Iterable
from typing import Any

from src.core.errors import ValidationError

# Notion caps a single rich_text / title text object at 2000 characters.
NOTION_TEXT_LIMIT = 2000


def _prop(page: dict[str, Any], name: str) -> dict[str, Any]:
    value = page.get("properties", {}).get(name)
    return value if isinstance(value, dict) else {}


def plain_text(segments: Iterable[dict[str, Any]] | None) -> str:
    """Join every segment of a rich-text array.

    Notion splits rich text at each formatting change (bold, italic, link…), so
    reading only the first segment truncates at the first styled word.
    """
    return "".join(seg.get("plain_text", "") for seg in segments or [])


def text(page: dict[str, Any], name: str) -> str:
    """Plain text of a ``title`` or ``rich_text`` property ("" when absent)."""
    prop = _prop(page, name)
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return plain_text(prop.get(kind)).strip()
    # Untyped payloads (older fixtures, partial objects) — try both shapes.
    return plain_text(prop.get("title") or prop.get("rich_text")).strip()


def multi_select_names(page: dict[str, Any], name: str) -> list[str]:
    """Option names of a multi-select, or of a select/status as a 1-item list."""
    prop = _prop(page, name)
    kind = prop.get("type")
    if kind is None:
        kind = next((k for k in ("multi_select", "select", "status") if k in prop), None)
    value = prop.get(kind) if kind else None
    if kind == "multi_select":
        return [opt["name"] for opt in value or [] if opt.get("name")]
    if isinstance(value, dict) and value.get("name"):
        return [value["name"]]
    return []


def select_name(page: dict[str, Any], name: str) -> str | None:
    """First option name of a select, status or multi-select property."""
    names = multi_select_names(page, name)
    return names[0] if names else None


def relation_ids(page: dict[str, Any], name: str) -> list[str]:
    """Ids of the pages a ``relation`` property points to."""
    return [rel["id"] for rel in _prop(page, name).get("relation") or [] if rel.get("id")]


def checkbox(page: dict[str, Any], name: str) -> bool:
    return bool(_prop(page, name).get("checkbox"))


def url_value(page: dict[str, Any], name: str) -> str:
    """Value of a ``url`` property ("" when unset)."""
    return _prop(page, name).get("url") or ""


def date_start(page: dict[str, Any], name: str) -> str | None:
    """ISO start of a ``date`` property."""
    value = _prop(page, name).get("date")
    return value.get("start") if isinstance(value, dict) else None


def external_links(page: dict[str, Any], name: str) -> list[tuple[str, str]]:
    """``(label, url)`` for each external entry of a ``files`` property.

    Notion-hosted uploads are skipped: their signed URLs expire after an hour,
    so posting them in Discord would leave dead links behind.
    """
    links: list[tuple[str, str]] = []
    for entry in _prop(page, name).get("files") or []:
        external = entry.get("external")
        if external and external.get("url"):
            links.append((entry.get("name") or "Lien", external["url"]))
    return links


def page_url(page: dict[str, Any]) -> str:
    """Public URL when the page is published, its workspace URL otherwise."""
    return page.get("public_url") or page.get("url") or ""


def schema_options(schema: dict[str, Any], name: str) -> list[str]:
    """Option names configured on a select / multi-select / status column."""
    prop = schema.get(name) or {}
    kind = prop.get("type")
    options = (prop.get(kind) or {}).get("options", []) if kind else []
    return [opt["name"] for opt in options if opt.get("name")]


def canonical_option(value: str, options: list[str]) -> str | None:
    """Return the configured option matching ``value`` case-insensitively."""
    wanted = value.strip().casefold()
    return next((opt for opt in options if opt.casefold() == wanted), None)


def dedupe(values: Iterable[str]) -> list[str]:
    """Drop blanks and repeats, keeping first-seen order."""
    seen: dict[str, None] = {}
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen[value] = None
    return list(seen)


def canonical_choices(
    values: Iterable[str], options: list[str], label: str, *, strict: bool
) -> list[str]:
    """Map values onto configured options (case-insensitive).

    ``strict`` rejects unknown values — members pick from autocomplete, and
    Notion would silently create a new option for a typo. The owner editing a
    draft is not strict: a new author or a new défi is legitimate.
    """
    result: list[str] = []
    for value in dedupe(values):
        match = canonical_option(value, options) if options else value
        if match is None:
            if strict:
                raise ValidationError(
                    f"{label} inconnu·e : « {value} ». Choisissez une valeur proposée."
                )
            match = value
        if match not in result:
            result.append(match)
    return result


# ── Property payload builders ────────────────────────────────────────────


def title_value(value: str) -> dict[str, Any]:
    return {"title": [{"text": {"content": value[:NOTION_TEXT_LIMIT]}}]}


def rich_text_value(value: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": value[:NOTION_TEXT_LIMIT]}}]}


def select_value(value: str) -> dict[str, Any]:
    return {"select": {"name": value}}


def multi_select_value(values: Iterable[str]) -> dict[str, Any]:
    return {"multi_select": [{"name": v} for v in dedupe(values)]}


def external_files_value(links: Iterable[tuple[str, str]]) -> dict[str, Any]:
    return {
        "files": [
            {"name": label[:100], "type": "external", "external": {"url": url}}
            for label, url in links
        ]
    }


def filter_to_schema(
    properties: dict[str, Any], schema: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Split ``properties`` into those the data source has and those it lacks.

    Notion rejects a whole page create when a single property name is unknown,
    so callers drop the unknown ones (and log them) instead of failing. An
    empty ``schema`` — lookup unavailable — keeps everything.
    """
    if not schema:
        return dict(properties), []
    kept = {k: v for k, v in properties.items() if k in schema}
    dropped = [k for k in properties if k not in schema]
    return kept, dropped
