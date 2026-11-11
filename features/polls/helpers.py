"""Pure (Discord-agnostic) helpers for poll logic."""

import re
from collections.abc import Mapping, Sequence

from features.polls.constants import DEFAULT_POLL_EMOJIS, POLL_EMOJIS

MAX_POLL_OPTIONS = 10

# The emojis a reaction poll's option paragraphs start with.
_OPTION_EMOJIS = frozenset(POLL_EMOJIS) | frozenset(DEFAULT_POLL_EMOJIS)
# The tally a previous render appended to an option: " : 2/5 votes" (bold or
# not), then the voters on the next line.
_VOTE_TALLY = re.compile(r" : (?:\*\*)?\d+/\d+ votes.*\Z", re.DOTALL)
_FOOTER_PREFIX = "Créé par "


def validate_poll_options(options: list[str]) -> bool:
    """True when the option count fits within the emoji pool."""
    return len(options) <= MAX_POLL_OPTIONS


def parse_poll_author_id(footer_text: str) -> str | None:
    """Extract the author ID from a poll embed footer like
    ``"Créé par <name> (ID: <id>)"``."""
    if not footer_text or len(footer_text.split(" ")) < 5:
        return None
    return footer_text.split(" ")[4].rstrip(")")


def options_description(options: list[str], emojis: list[str]) -> str:
    """A reaction poll's body: one ``emoji option`` paragraph per option."""
    return "\n\n".join(f"{emoji} {option}" for emoji, option in zip(emojis, options, strict=False))


def poll_block_emoji(block: str) -> str | None:
    """The option emoji a reaction-poll paragraph starts with, or None for any other paragraph."""
    text = block.removeprefix("**")
    emoji = text.split(" ", 1)[0]
    return emoji if emoji in _OPTION_EMOJIS else None


def poll_option_emojis(description: str | None) -> set[str]:
    """Every option emoji the paragraphs of a reaction poll's description start with."""
    blocks = (description or "").split("\n\n")
    return {emoji for emoji in map(poll_block_emoji, blocks) if emoji}


def poll_option_label(block: str) -> str:
    """An option paragraph's ``emoji option`` text, without the tally a previous render added.

    Only the tally is removed, so an option containing ``:`` or ``**`` keeps its text.
    """
    if block.startswith("**") and block.endswith("**") and _VOTE_TALLY.search(block[2:-2]):
        block = block[2:-2]
    return _VOTE_TALLY.sub("", block)


def is_reaction_poll_text(footer_text: str | None, description: str | None) -> bool:
    """Whether an embed's footer and body are those ``/poll`` writes.

    The footer names the author (``Créé par <name> (ID: <id>)``) and the first
    paragraph starts with an option emoji. Other embeds sharing the poll colour
    (RSS posts, role menus, giveaways, button polls) don't match both.
    """
    if not footer_text or not footer_text.startswith(_FOOTER_PREFIX):
        return False
    if not (parse_poll_author_id(footer_text) or "").isdigit():
        return False
    first_block = (description or "").split("\n\n", 1)[0]
    return poll_block_emoji(first_block) is not None


def render_reaction_poll(
    description: str, voters: Mapping[str, Sequence[str]], participant_count: int
) -> str:
    """Re-render a reaction poll's description with each option's votes and voters.

    *voters* maps an option emoji to the names of those who reacted with it.
    The most voted options are bolded. Paragraphs that aren't options (the
    auto-close note) are kept as they are.
    """
    blocks = description.split("\n\n") if description else []
    emojis = [poll_block_emoji(block) for block in blocks]
    top = max((len(voters.get(emoji, ())) for emoji in emojis if emoji), default=0)
    rendered = []
    for block, emoji in zip(blocks, emojis, strict=True):
        if emoji is None:
            rendered.append(block)
            continue
        option = poll_option_label(block)
        names = voters.get(emoji, ())
        if not names:
            rendered.append(option)
            continue
        tally = f"{len(names)}/{participant_count} votes"
        names_text = ", ".join(names)
        if len(names) == top:
            rendered.append(f"**{option} : {tally}\n({names_text})**")
        else:
            rendered.append(f"{option} : **{tally}**\n({names_text})")
    return "\n\n".join(rendered)


__all__ = [
    "MAX_POLL_OPTIONS",
    "is_reaction_poll_text",
    "options_description",
    "parse_poll_author_id",
    "poll_block_emoji",
    "poll_option_emojis",
    "poll_option_label",
    "render_reaction_poll",
    "validate_poll_options",
]
