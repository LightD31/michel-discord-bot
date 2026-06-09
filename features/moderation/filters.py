"""Pure text filters for automod — no Discord or DB imports."""

import re

# discord.gg/... , discord.com/invite/... , discordapp.com/invite/... , discord.me/...
INVITE_RE = re.compile(
    r"(?:discord\.gg|discord(?:app)?\.com/invite|discord\.me)/\S+", re.IGNORECASE
)


def contains_invite(content: str) -> bool:
    """True if *content* contains a Discord invite link."""
    return bool(INVITE_RE.search(content or ""))


def match_banned_word(content: str, words: list[str]) -> str | None:
    """Return the first banned word present in *content* (word-boundary), else None."""
    low = (content or "").lower()
    for word in words:
        w = str(word).strip().lower()
        if not w:
            continue
        if re.search(rf"\b{re.escape(w)}\b", low):
            return str(word)
    return None
