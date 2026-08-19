"""Tests for the ``/journa`` link rendered inside the reminder messages.

The reminder targets are free-text Discord URLs configured per guild (the
``/journa`` channel lives on another server than the bot's own), so the link
comes straight from the config and the message must keep carrying it.
"""

from features.coloc.constants import ReminderType, format_journa_link, get_reminder_message

JOURNA_LINK = "https://discord.com/channels/138283154589876224/808432657838768168"


def test_format_journa_link_renders_a_markdown_link():
    assert format_journa_link(JOURNA_LINK) == f"[/journa]({JOURNA_LINK})"


def test_format_journa_link_falls_back_to_plain_code():
    assert format_journa_link("") == "`/journa`"
    assert format_journa_link(None) == "`/journa`"


def test_reminder_templates_render_with_the_link():
    for reminder_type in (ReminderType.NORMAL, ReminderType.HARDCORE):
        for template in get_reminder_message(reminder_type):
            rendered = template.format(journa=format_journa_link(JOURNA_LINK))
            assert JOURNA_LINK in rendered
            assert "{journa}" not in rendered
