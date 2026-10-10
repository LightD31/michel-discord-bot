"""When Michel answers a message that addresses him — pure trigger rules and cooldown.

Discord grants message content for messages that mention the bot (and the bot
also holds the Message Content intent), but answering is opt-in per guild and
deliberately narrow: only humans, only when addressed, never on mass pings,
and at most once per cooldown window per user.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from features.ai.prompt import strip_user_mention

_ROLE_MENTION = re.compile(r"<@&(\d+)>")

# Answers Michel posted recently; a reply to one of them continues the thread.
_RECENT_ANSWERS_CAP = 1000


@dataclass(frozen=True)
class MentionFacts:
    """What the extension extracts from a ``MessageCreate`` to decide."""

    content: str
    bot_id: str
    author_is_bot: bool = False
    is_webhook: bool = False
    mentions_everyone: bool = False
    replies_to_answer: bool = False
    # The bot's own managed role: Discord's autocomplete often inserts it
    # instead of the user when someone types "@Michel".
    bot_role_ids: frozenset[str] = frozenset()


def mention_question(facts: MentionFacts) -> str | None:
    """The text to answer, or ``None`` when Michel should stay quiet.

    Triggers on an explicit ``<@bot>`` mention, or on a reply to one of
    Michel's AI answers (so a conversation can continue without re-pinging).
    """
    if facts.author_is_bot or facts.is_webhook:
        return None
    if facts.mentions_everyone:
        return None
    roles = set(_ROLE_MENTION.findall(facts.content))
    if roles - facts.bot_role_ids:
        return None  # a role ping is aimed at people, not at Michel
    mentioned = bool(roles) or (
        re.search(rf"<@!?{re.escape(facts.bot_id)}>", facts.content) is not None
    )
    if not mentioned and not facts.replies_to_answer:
        return None
    question = strip_user_mention(facts.content, facts.bot_id)
    for role_id in roles:
        question = question.replace(f"<@&{role_id}>", " ")
    question = re.sub(r"[ \t]{2,}", " ", question).strip()
    return question or None


class Cooldown:
    """Per-user cooldown. ``check`` says whether to answer and whether to signal a refusal."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._warned: set[str] = set()

    def check(self, user_id: str, seconds: float, now: float | None = None) -> tuple[bool, bool]:
        """Returns ``(allowed, warn)``; *warn* is True once per blocked window."""
        now = time.monotonic() if now is None else now
        last = self._last.get(user_id)
        if last is not None and now - last < seconds:
            warn = user_id not in self._warned
            self._warned.add(user_id)
            return False, warn
        self._last[user_id] = now
        self._warned.discard(user_id)
        if len(self._last) > 5000:  # forget users idle for a long time
            cutoff = now - 3600
            self._last = {u: t for u, t in self._last.items() if t >= cutoff}
        return True, False


class RecentAnswers:
    """Bounded set of message ids Michel posted as AI answers."""

    def __init__(self, cap: int = _RECENT_ANSWERS_CAP) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._cap = cap

    def add(self, *message_ids: str) -> None:
        for message_id in message_ids:
            self._ids[str(message_id)] = None
            self._ids.move_to_end(str(message_id))
        while len(self._ids) > self._cap:
            self._ids.popitem(last=False)

    def __contains__(self, message_id: object) -> bool:
        return str(message_id) in self._ids
