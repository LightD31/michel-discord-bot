"""Tests for the /journa channel link helpers.

The reminder used to carry a hardcoded Discord URL, then a free-text URL field;
it is now a channel picker, so the link is built from the guild + channel ids.
"""

from features.coloc.constants import ReminderType, discord_channel_link, format_journa_link


def test_discord_channel_link_builds_a_jump_link():
    assert (
        discord_channel_link("138283154589876224", "808432657838768168")
        == "https://discord.com/channels/138283154589876224/808432657838768168"
    )


def test_discord_channel_link_accepts_ints():
    assert discord_channel_link(1, 2) == "https://discord.com/channels/1/2"


def test_discord_channel_link_is_empty_without_both_ids():
    assert discord_channel_link("", "808432657838768168") == ""
    assert discord_channel_link("138283154589876224", None) == ""
    assert discord_channel_link(None, None) == ""


def test_format_journa_link_falls_back_to_plain_code():
    assert format_journa_link("https://discord.com/channels/1/2") == (
        "[/journa](https://discord.com/channels/1/2)"
    )
    assert format_journa_link("") == "`/journa`"


def test_reminder_templates_render_with_the_link():
    from features.coloc.constants import get_reminder_message

    for reminder_type in (ReminderType.NORMAL, ReminderType.HARDCORE):
        for template in get_reminder_message(reminder_type):
            rendered = template.format(
                journa=format_journa_link("https://discord.com/channels/1/2")
            )
            assert "https://discord.com/channels/1/2" in rendered
            assert "{journa}" not in rendered
