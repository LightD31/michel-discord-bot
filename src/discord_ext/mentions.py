"""Who a message the bot sends may ping.

The bot's role holds Administrator (so Mention Everyone) on most servers, and
much of what it posts carries text someone else wrote: a member's input, a
feed title, an API's answer. Discord pings whatever the content mentions
unless the message says otherwise, so one ``@everyone`` in that text would
ping a whole server.

:func:`install` gives every message sent without ``allowed_mentions`` a
default that pings users and roles, never ``@everyone`` / ``@here``. A
message meant to ping everyone says so: :func:`everyone_if_written` for a
template a server's admins wrote, :func:`mentions_written_in` when the
rendered text also carries someone else's words.
"""

from __future__ import annotations

import copy
from typing import Any

from interactions import AllowedMentions, Permissions
from interactions.client.const import MISSING
from interactions.models.discord import message as message_module

from src.core.text import mentions_in
from src.discord_ext.channels import member_can

# For text that must ping no one (a member's input echoed back).
NO_PINGS = AllowedMentions.none()
# What a message sent without allowed_mentions may ping. A reply still pings
# the author of the message it answers, as Discord's own default does.
DEFAULT_MENTIONS: dict[str, Any] = {"parse": ["users", "roles"], "replied_user": True}

_installed = False


def install() -> None:
    """Apply :data:`DEFAULT_MENTIONS` to messages sent without ``allowed_mentions``.

    Wraps the library's payload helper, which every send, reply, edit and
    interaction response goes through. Calling it again does nothing.
    """
    global _installed
    if _installed:
        return
    original = message_module.process_allowed_mentions

    def with_default(allowed_mentions: Any) -> dict[str, Any] | None:
        if allowed_mentions is None or allowed_mentions is MISSING:
            return copy.deepcopy(DEFAULT_MENTIONS)
        return original(allowed_mentions)

    message_module.process_allowed_mentions = with_default
    _installed = True


def everyone_if_written(template: str) -> AllowedMentions:
    """Users and roles, plus ``@everyone`` / ``@here`` when *template* writes it.

    For a message rendered from a template a server's admins wrote, filled
    with values that can't ping anyone themselves (a member's mention, a level).
    """
    _, _, everyone = mentions_in(template)
    return AllowedMentions(parse=["users", "roles", *(["everyone"] if everyone else [])])


def mentions_written_in(template: str) -> AllowedMentions:
    """Let a message ping only what its configured *template* mentions.

    The rendered text also carries text someone else wrote (a feed title, a
    member's input): an ``@everyone`` in there must not ping a server the bot
    can mention everyone in.
    """
    users, roles, everyone = mentions_in(template)
    return AllowedMentions(
        parse=["everyone"] if everyone else [],
        users=[int(uid) for uid in users],
        roles=[int(rid) for rid in roles],
    )


def mentions_member_could_send(member: Any, channel: Any, text: str) -> AllowedMentions:
    """What *text*, posted by the bot for *member* in *channel*, may ping.

    What the member could ping writing it themselves: the users it names; the
    roles it names that anyone may mention; every role and ``@everyone`` /
    ``@here`` when they hold Mention Everyone in *channel*. The bot's own
    permissions are wider (it is an administrator on most servers).
    """
    users, roles, everyone = mentions_in(text)
    if member_can(member, channel, Permissions.MENTION_EVERYONE):
        allowed_roles = roles
    else:
        everyone = False
        guild = getattr(channel, "guild", None)
        get_role = getattr(guild, "get_role", None)
        allowed_roles = [
            rid
            for rid in roles
            if callable(get_role) and getattr(get_role(int(rid)), "mentionable", False)
        ]
    return AllowedMentions(
        parse=["everyone"] if everyone else [],
        users=[int(uid) for uid in users],
        roles=[int(rid) for rid in allowed_roles],
    )


__all__ = [
    "DEFAULT_MENTIONS",
    "NO_PINGS",
    "everyone_if_written",
    "install",
    "mentions_member_could_send",
    "mentions_written_in",
]
