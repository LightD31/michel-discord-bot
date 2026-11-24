"""``/rattrapage``: what a channel said since a member's last message, summed up by the AI (P20).

Pure parts only: which messages to read (since the member's last one, at
most :data:`MAX_MESSAGES`), the prompt that numbers them, and the answer's
``[n]`` references turned into links to those messages. The Discord layer
fetches the history, reserves the call and sends the summary.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.text import clip

MAX_MESSAGES = 200
MIN_MESSAGES = 5  # fewer than this: nothing worth a summary (and no call spent)
MESSAGE_CHARS = 400  # each message, as the provider reads it
SUMMARY_CHARS = 3500  # what the card shows at most
SUMMARY_TOKENS = 700
INSTRUCTIONS = (
    "Tu résumes pour {member} ce qui s'est dit dans le salon #{channel} du serveur {server} "
    "depuis son dernier message. Les messages sont numérotés [n], du plus ancien au plus "
    "récent. Écris en français, en tutoyant {member}, 3 à 8 puces courtes : les sujets, les "
    "décisions, les questions restées sans réponse, ce qui concerne {member}. Termine chaque "
    "puce par la référence du message qui compte le plus, comme [12]. N'invente rien, pas de "
    "titre, pas de mention @, pas de lien."
)
_REFERENCE = re.compile(r"\[(\d{1,3})\]")


@dataclass(frozen=True)
class ChannelMessage:
    id: str
    author_id: str
    author: str  # their display name
    content: str
    is_bot: bool = False


def since_last(messages: Sequence[ChannelMessage], member_id: str) -> list[ChannelMessage]:
    """The messages after *member_id*'s last one (oldest first), or all if they wrote none.

    *messages* are oldest first. Empty messages (an image alone, an embed)
    are left out, and so are the member's own.
    """
    start = 0
    for index, message in enumerate(messages):
        if message.author_id == member_id:
            start = index + 1
    return [m for m in messages[start:] if m.content.strip() and m.author_id != member_id]


def prompt(
    messages: Sequence[ChannelMessage],
    *,
    persona: str,
    member: str,
    channel: str,
    server: str,
) -> list[dict[str, str]]:
    """Michel's persona and the brief, then the numbered messages."""
    lines = []
    for number, message in enumerate(messages, start=1):
        text = " ".join(message.content.replace("@", "").split())
        lines.append(f"[{number}] {message.author} : {clip(text, MESSAGE_CHARS)}")
    brief = INSTRUCTIONS.format(member=member, channel=channel, server=server)
    return [
        {"role": "system", "content": f"{persona}\n\n{brief}"},
        {"role": "user", "content": "\n".join(lines)},
    ]


def with_links(summary: str, messages: Sequence[ChannelMessage], link: Callable[[str], str]) -> str:
    """The summary with each ``[n]`` a link to that message; a number that names none goes.

    ``@`` are dropped so the summary can't mention anyone, and it is clipped
    to :data:`SUMMARY_CHARS`.
    """
    text = summary.replace("@", "").strip()

    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(messages):
            return f"[↗]({link(messages[number - 1].id)})"
        return ""

    text = _REFERENCE.sub(replace, text)
    return clip("\n".join(line.rstrip() for line in text.splitlines()).strip(), SUMMARY_CHARS)


__all__ = [
    "INSTRUCTIONS",
    "MAX_MESSAGES",
    "MIN_MESSAGES",
    "SUMMARY_TOKENS",
    "ChannelMessage",
    "prompt",
    "since_last",
    "with_links",
]
