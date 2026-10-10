"""Text rendering for AI answers — pure functions producing Discord-sized chunks.

A comparison is posted as one or more messages: the answers ("body"), then a
footer on the *last* message carrying the vote status. Only the footer changes
while votes come in and when the vote closes, so only the last message is ever
edited; the body chunks are sized so the footer always fits next to them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# Discord's hard limit is 2000; keep headroom for the quote markers we add.
DISCORD_MESSAGE_LIMIT = 1900
# Room kept free on the last body chunk for the footer (≤ 5 result lines).
FOOTER_RESERVE = 500
MAX_QUESTION_CHARS = 300
_MAX_LABEL_CHARS = 60


# =============================================================================
# Splitting
# =============================================================================


def _find_split_point(text: str, limit: int) -> int:
    newline_idx = text[:limit].rfind("\n")
    if newline_idx > limit // 2:
        return newline_idx + 1
    space_idx = text[:limit].rfind(" ")
    if space_idx > limit - 100:
        return space_idx + 1
    return limit


def _split_long_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = _find_split_point(text, limit)
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks


def split_message(content: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split *content* into chunks ≤ *limit*: paragraphs, then lines, then words."""
    if len(content) <= limit:
        return [content]
    messages: list[str] = []
    current = ""
    for paragraph in content.split("\n\n"):
        if len(paragraph) > limit:
            if current:
                messages.append(current)
                current = ""
            messages.extend(_split_long_text(paragraph, limit))
        elif current and len(current) + len(paragraph) + 2 > limit:
            messages.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        messages.append(current)
    return messages


# =============================================================================
# Shared bits
# =============================================================================


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def question_header(asker_mention: str, question: str) -> str:
    return f"**{asker_mention} : {_truncate(question, MAX_QUESTION_CHARS)}**"


def quote(content: str) -> str:
    return "> " + content.replace("\n", "\n> ")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count > 1 else ''}"


# =============================================================================
# Tally
# =============================================================================


def tally(answer_keys: Sequence[str], votes: Mapping[str, str]) -> list[int]:
    """Vote count per answer position (votes map user id → answer key)."""
    counts = [0] * len(answer_keys)
    index = {key: i for i, key in enumerate(answer_keys)}
    for key in votes.values():
        i = index.get(key)
        if i is not None:
            counts[i] += 1
    return counts


def winners(counts: Sequence[int]) -> set[int]:
    """Positions with the most votes (ties included); empty when nobody voted."""
    best = max(counts, default=0)
    if best <= 0:
        return set()
    return {i for i, count in enumerate(counts) if count == best}


# =============================================================================
# Comparison
# =============================================================================


def render_comparison_body(asker_mention: str, question: str, answers: Sequence[str]) -> list[str]:
    """Body chunks for a comparison; the last one leaves room for the footer."""
    parts = [question_header(asker_mention, question)]
    parts.extend(f"**Réponse {i + 1}**\n{quote(text)}" for i, text in enumerate(answers))
    chunks = split_message("\n\n".join(parts))
    tail_limit = DISCORD_MESSAGE_LIMIT - FOOTER_RESERVE
    if len(chunks[-1]) > tail_limit:
        chunks = chunks[:-1] + split_message(chunks[-1], tail_limit)
    return chunks


def render_comparison_footer(
    labels: Sequence[str],
    counts: Sequence[int],
    *,
    closed: bool,
    closes_at_ts: int | None = None,
) -> str:
    """Vote status under the answers: open (voter count) or closed (models revealed)."""
    total = sum(counts)
    if not closed:
        deadline = f" — fin <t:{closes_at_ts}:R>" if closes_at_ts else ""
        return f"-# 🗳️ Votez pour la meilleure réponse{deadline} · {_plural(total, 'vote')}"
    best = winners(counts)
    lines = [f"**🔒 Vote terminé** · {_plural(total, 'vote')}"]
    for i, (label, count) in enumerate(zip(labels, counts, strict=False)):
        trophy = " 🏆" if i in best else ""
        name = _truncate(label, _MAX_LABEL_CHARS)
        lines.append(f"Réponse {i + 1} : **{name}** — {_plural(count, 'vote')}{trophy}")
    return "\n".join(lines)


def attach_footer(body_chunks: Sequence[str], footer: str) -> list[str]:
    """The messages to post: the body chunks with *footer* appended to the last one."""
    chunks = list(body_chunks) or [""]
    chunks[-1] = f"{chunks[-1]}\n\n{footer}".strip()
    return chunks


# =============================================================================
# Single answer
# =============================================================================


def render_single(
    content: str, *, header: str | None = None, footer: str | None = None
) -> list[str]:
    """Chunks for a single answer, with an optional header and small-text footer."""
    parts = [p for p in (header, content, f"-# {footer}" if footer else None) if p]
    return split_message("\n\n".join(parts))
