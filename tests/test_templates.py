"""Templates server admins write are filled with {name} placeholders only."""

import time

import pytest

import extensions.rss._common  # noqa: F401 — registers the moduleRss schema
import extensions.xp._common  # noqa: F401 — registers the moduleXp schema
import extensions.youtube  # noqa: F401 — registers the moduleYoutube schema
from features.youtube.videos import DEFAULT_TEMPLATE, render_notification
from src.core.text import TemplateError, format_template, pick_weighted_message, template_problem
from src.webui import schemas

HOSTILE = [
    "{mention:>2000000000}",  # a gigabyte-wide padding: the process would be OOM-killed
    "{mention!r}",
    "{mention.__class__}",
    "{mention[0]}",
    "{}",
    "{mention:{mention}}",
]


@pytest.mark.parametrize("template", HOSTILE)
def test_format_template_refuses_anything_but_names(template):
    with pytest.raises(TemplateError):
        format_template(template, mention="<@1>")
    assert template_problem(template) is not None


def test_names_escapes_and_unknown_names():
    assert format_template("Salut {mention} {{ok}}", mention="<@1>") == "Salut <@1> {ok}"
    with pytest.raises(KeyError):
        format_template("{nope}")
    assert format_template("{nope} {mention}", keep_unknown=True, mention="x") == "{nope} x"
    assert template_problem("Salut {mention} {inconnu} }") is None  # sent as written, as before


def test_a_hostile_welcome_message_is_sent_as_written_at_once():
    config = {"list": [{"text": "{mention:>2000000000}", "weight": 1}]}
    started = time.monotonic()
    assert pick_weighted_message(config, "list", "x", mention="<@1>") == "{mention:>2000000000}"
    assert time.monotonic() - started < 1


def test_a_hostile_youtube_template_falls_back_to_the_link():
    assert render_notification(
        "{label:>2000000000}", video_id="abc", handle="h", label="l"
    ) == DEFAULT_TEMPLATE.format(video_id="abc")


def errors(module, value):
    return schemas.validation_errors(schemas.module_model(module), value, module=True)


def test_saves_refuse_a_format_spec_in_any_template():
    (error,) = errors("moduleXp", {"levelUpMessageList": [{"text": "{mention:>99999}"}]})
    assert error.startswith("levelUpMessageList.0.text:")
    (error,) = errors("moduleYoutube", {"youtubeNotificationTemplate": "{label!r}"})
    assert error.startswith("youtubeNotificationTemplate:")
    feeds = {"news": {"url": "https://x/feed", "template": "{title.__class__}"}}
    (error,) = errors("moduleRss", {"rssFeeds": feeds})
    assert error.startswith("rssFeeds:")


def test_saves_accept_plain_templates():
    assert errors("moduleXp", {"levelUpMessageList": [{"text": "Bravo {mention} :)"}]}) == []
    assert (
        errors("moduleRss", {"rssFeeds": {"news": {"url": "https://x", "template": "{title}"}}})
        == []
    )
