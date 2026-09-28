"""Pending submissions awaiting the owner's approval (no Discord imports).

The Œuvres database is published on the web, so nothing a member submits goes
to Notion directly: forum posts, défi answers and ``/demande`` forms become
drafts in MongoDB, and the Notion page is created only when the owner approves.
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import notion_props as np
from .stats import COL_AUTHOR, COL_DEFI, COL_FILES, COL_GENRE, COL_TITLE, COL_TYPE

SOURCE_TEXTES = "textes"
SOURCE_DEFI = "defi"
SOURCE_DEMANDE = "demande"

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_APPROVED = "approved"
STATUS_DONE = "done"
STATUS_REJECTED = "rejected"

TITLE_MAX = 100
# A défi thread also collects comments ("Bravo !", questions…). Only a message
# that looks like an answer — long enough, or carrying a file or a link —
# becomes a draft.
MIN_ANSWER_CHARS = 300

_URL = re.compile(r"https?://\S+")
_DEFI_NUMBER = re.compile(r"d[ée]fi\s*n?[°o]?\s*0*(\d+)", re.IGNORECASE)
_MARKDOWN_EDGES = re.compile(r"^[#>*_~`\s-]+|[*_~`\s]+$")


@dataclass
class Draft:
    """One pending submission. ``id`` doubles as the dedupe key."""

    id: str
    source: str
    guild_id: str
    submitter_id: int
    title: str
    authors: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    defi: str | None = None
    link: str = ""
    link_label: str = "Lien"
    details: str = ""
    channel_id: int | None = None
    message_id: int | None = None
    unmapped_author: bool = False
    # Where the owner's review message was posted, to update it once handled.
    review_channel_id: int | None = None
    review_message_id: int | None = None
    status: str = STATUS_PENDING
    notion_url: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["_id"] = doc.pop("id")
        return doc

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "Draft":
        data = dict(doc)
        data["id"] = data.pop("_id")
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})


def textes_draft_id(thread_id: int) -> str:
    return f"textes-{thread_id}"


def defi_draft_id(thread_id: int, author_id: int) -> str:
    return f"defi-{thread_id}-{author_id}"


def demande_draft_id() -> str:
    return f"demande-{uuid.uuid4().hex}"


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def match_defi(thread_name: str, options: Iterable[str]) -> str | None:
    """The Défi option a défi-forum thread is about, or None.

    Numbered défis match on their number ("Défi 12 - questionnaire" →
    "Défi 12 : Questionnaire Personnage"); anything else (the SBL/WBL series)
    matches when the option's name appears in the thread title.
    """
    options = list(options)
    number = _DEFI_NUMBER.search(thread_name)
    if number:
        wanted = int(number.group(1))
        for option in options:
            found = _DEFI_NUMBER.search(option)
            if found and int(found.group(1)) == wanted:
                return option
        return None

    title = f" {_normalise(thread_name)} "
    candidates = [
        option
        for option in options
        if not _DEFI_NUMBER.search(option) and f" {_normalise(option)} " in title
    ]
    return max(candidates, key=len) if candidates else None


def looks_like_answer(content: str, *, has_attachment: bool) -> bool:
    return has_attachment or bool(_URL.search(content)) or len(content) >= MIN_ANSWER_CHARS


def title_from_message(content: str, fallback: str) -> str:
    """First line of the message when it reads like a title, else ``fallback``."""
    for line in content.splitlines():
        line = _MARKDOWN_EDGES.sub("", line).strip()
        if line:
            return line if len(line) <= TITLE_MAX else fallback
    return fallback


def resolve_author(user_id: int, display_name: str, author_map: dict[str, str]) -> tuple[str, bool]:
    """Notion author name for a Discord user; ``(display_name, False)`` if unmapped."""
    name = (author_map.get(str(user_id)) or "").strip()
    return (name, True) if name else (display_name, False)


def first_url(text: str) -> str:
    match = _URL.search(text)
    return match.group(0).rstrip(").,>") if match else ""


def tags_to_options(tags: Iterable[str], options: list[str]) -> list[str]:
    """Forum tag names that match a Notion option (canonical spelling)."""
    return np.dedupe(m for tag in tags if (m := np.canonical_option(tag, options)))


def split_list(value: str) -> list[str]:
    """Comma-separated modal input → list."""
    return np.dedupe(value.split(","))


def build_work_properties(draft: Draft) -> dict[str, Any]:
    """Notion properties for the Œuvres page created when ``draft`` is approved."""
    properties: dict[str, Any] = {COL_TITLE: np.title_value(draft.title)}
    if draft.authors:
        properties[COL_AUTHOR] = np.multi_select_value(draft.authors)
    if draft.types:
        properties[COL_TYPE] = np.multi_select_value(draft.types)
    if draft.genres:
        properties[COL_GENRE] = np.multi_select_value(draft.genres)
    if draft.defi:
        properties[COL_DEFI] = np.select_value(draft.defi)
    if draft.link:
        properties[COL_FILES] = np.external_files_value([(draft.link_label, draft.link)])
    return properties
