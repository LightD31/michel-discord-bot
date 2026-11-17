"""List and map fields are typed with what the SPA writes (Q03).

CLAUDE.md: "Type what the SPA writes back into its markup". A ``list[Any]`` or
``dict[str, dict]`` let a string or an unknown key through the save route and
into the widget's ``value="…"`` attributes and the extension's code.
"""

import ast

import pytest
from source_scan import parse, python_files, rel

from extensions.customcommands._common import CustomCommandsConfig
from extensions.embedmanager import EmbedManagerConfig
from extensions.rss._common import RssConfig
from extensions.twitch._common import TwitchConfig
from extensions.vlrgg._common import VlrggConfig
from extensions.youtube import YoutubeConfig
from src.integrations.llm import SUPPORTED_APIS
from src.webui.schemas import AiModelEntry, AISection, validation_errors


def errors(model, value):
    return validation_errors(model, value, module=True)


# (model, field, what the SPA writes, a few saves it must refuse)
CASES = [
    (
        CustomCommandsConfig,
        "commands",
        [{"name": "gif", "description": "", "responses": ["https://x/y.gif"]}],
        [["gif"], [{"name": "gif", "responses": "a"}], [{"name": "", "responses": []}]],
    ),
    (
        EmbedManagerConfig,
        "embeds",
        [{"title": "Liens", "color": "3498db", "links": [{"title": "Site", "url": "https://x"}]}],
        [["x"], [{"title": "t", "color": '" onfocus="x'}], [{"title": "t", "links": ["u"]}]],
    ),
    (
        YoutubeConfig,
        "youtubeChannelList",
        {"@chaine": {"shorts": True, "live": False, "vod": True, "label": "Chaîne"}},
        [{"@chaine": "x"}, {"@chaine": {"shorts": "oui"}}, {"@chaine": {"other": 1}}],
    ),
    (
        RssConfig,
        "rssFeeds",
        {
            "news": {
                "url": "https://example.com/feed",
                "label": "News",
                "channel_id": "10",
                "mode": "embed",
                "embed_color": "ff0000",
                "embed_show_image": True,
            }
        },
        [{"news": "x"}, {"news": {"url": "u", "mode": "html"}}, {"news": {"label": "no url"}}],
    ),
    (
        TwitchConfig,
        "twitchStreamerList",
        {"zerator": {"twitchNotificationChannelId": "10", "notifyStreamStart": True}},
        [{"zerator": "x"}, {"zerator": {"notifyStreamStart": "peut-être"}}, {"zerator": {"x": 1}}],
    ),
    (
        VlrggConfig,
        "teams",
        [{"name": "KC", "vlrTeamId": "8877", "channelId": "10", "pin": True}],
        [["KC"], [{"vlrTeamId": "1"}], [{"name": "KC", "pin": "x"}]],
    ),
]


@pytest.mark.parametrize(
    ("model", "field", "valid", "invalid"), CASES, ids=lambda c: getattr(c, "__name__", None)
)
def test_the_spa_shape_is_accepted_and_others_refused(model, field, valid, invalid):
    assert [e for e in errors(model, {field: valid}) if e.startswith(field)] == []
    for value in invalid:
        assert [e for e in errors(model, {field: value}) if e.startswith(field)], value


def test_ai_models_name_a_supported_api():
    entry = {
        "provider": "gpt",
        "api": "openrouter",
        "model_id": "openai/gpt",
        "display_name": "GPT",
    }
    assert validation_errors(AISection, {"models": [entry]}, module=False) == []
    assert validation_errors(AISection, {"models": [{**entry, "api": "other"}]}, module=False)
    assert validation_errors(AISection, {"models": ["openai/gpt"]}, module=False)
    assert set(AiModelEntry.model_fields["api"].annotation.__args__) == set(SUPPORTED_APIS)


# ── No new untyped field ────────────────────────────────────────────────

FIELD_HELPERS = {"ui", "secret_field"}


def _untyped(annotation: ast.expr) -> bool:
    """``Any``, a bare ``dict`` / ``list``, or a container of those."""
    if isinstance(annotation, ast.Name):
        return annotation.id in {"Any", "dict", "list", "object"}
    if isinstance(annotation, ast.Subscript):
        inner = annotation.slice
        parts = inner.elts if isinstance(inner, ast.Tuple) else [inner]
        return any(_untyped(part) for part in parts)
    if isinstance(annotation, ast.BinOp):  # X | None
        return _untyped(annotation.left) or _untyped(annotation.right)
    return False


def test_no_schema_field_is_untyped():
    offenders = []
    for path in python_files("extensions", "src/webui"):
        tree, imports, _ = parse(path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call)):
                continue
            helper = (imports.resolve(node.value.func) or "").rpartition(".")[2]
            if helper in FIELD_HELPERS and _untyped(node.annotation):
                offenders.append(f"{rel(path)}:{node.lineno}: {ast.unparse(node.annotation)}")
    assert not offenders, "type the entries with a model:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "annotation", ["list[Any]", "dict[str, dict]", "dict[str, Any]", "list[dict] | None", "dict"]
)
def test_the_scan_catches(annotation):
    assert _untyped(ast.parse(annotation, mode="eval").body)


@pytest.mark.parametrize(
    "annotation", ["list[str]", "dict[str, str]", "list[WeightedMessage]", "str | None"]
)
def test_the_scan_accepts_typed_fields(annotation):
    assert not _untyped(ast.parse(annotation, mode="eval").body)
