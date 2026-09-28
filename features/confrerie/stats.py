"""Aggregate the Œuvres data source into the recap statistics (no Discord imports).

Rows with a ``Parent item`` are parts of a larger work (a saga's tomes, a
novel's chapters): they count as défi participations when they carry a défi,
but not as separate œuvres, so a ten-chapter novel doesn't outweigh ten
finished short stories.

Défis come in two families: numbered prompts ("Défi 12 : Questionnaire
Personnage") and recurring series ("SBL25", "WBL24"). The series gather far
more entries, so they are ranked separately instead of crowding the numbered
défis out of the top list.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import notion_props as np

COL_TITLE = "Titre"
COL_AUTHOR = "Auteur"
COL_DEFI = "Défi"
COL_TYPE = "Type"
COL_GENRE = "Genre"
COL_STATUS = "Avancement"
COL_PARENT = "Parent item"
COL_UPDATE = "Update"
COL_UPDATE_NOTE = "Note de mise à jour"
COL_FILES = "Lien / Fichier"
COL_LAST_PUBLISHED = "Dernière publication le"

IN_PROGRESS_STATUSES = ("En cours", "En relecture")

_DEFI_NUMBER = re.compile(r"^\s*d[ée]fi\s*0*(\d+)", re.IGNORECASE)


def defi_number(name: str) -> int | None:
    """Number of a "Défi NN : …" option, None for series like "SBL25"."""
    match = _DEFI_NUMBER.match(name)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class AuthorCount:
    name: str
    works: int
    defis: int


@dataclass(frozen=True)
class WorkInProgress:
    title: str
    status: str
    authors: tuple[str, ...]


@dataclass
class ConfrerieStats:
    total_works: int = 0
    total_defi_entries: int = 0
    authors: list[AuthorCount] = field(default_factory=list)
    numbered_defis: list[tuple[str, int]] = field(default_factory=list)
    series: list[tuple[str, int]] = field(default_factory=list)
    latest_defi: tuple[str, int] | None = None
    in_progress: list[WorkInProgress] = field(default_factory=list)

    @property
    def author_count(self) -> int:
        return len(self.authors)


def aggregate(pages: Iterable[dict[str, Any]], defi_options: Iterable[str] = ()) -> ConfrerieStats:
    """Compute the recap statistics from every page of the Œuvres data source.

    ``defi_options`` (from the schema) lets a freshly announced défi appear as
    the latest one before anybody has answered it. Every list is sorted with a
    deterministic tie-break so an unchanged database renders an identical
    embed — the recap refresher relies on that to skip no-op edits.
    """
    works: Counter[str] = Counter()
    defi_entries: Counter[str] = Counter()
    per_defi: Counter[str] = Counter()
    total_works = 0
    total_defi_entries = 0
    in_progress: list[WorkInProgress] = []

    for page in pages:
        authors = np.multi_select_names(page, COL_AUTHOR)
        is_part = bool(np.relation_ids(page, COL_PARENT))
        defi = np.select_name(page, COL_DEFI)

        if defi:
            total_defi_entries += 1
            per_defi[defi] += 1
            defi_entries.update(authors)

        if is_part:
            continue
        total_works += 1
        works.update(authors)

        status = np.select_name(page, COL_STATUS)
        if status in IN_PROGRESS_STATUSES:
            title = np.text(page, COL_TITLE) or "Sans titre"
            in_progress.append(WorkInProgress(title, status, tuple(authors)))

    names = set(works) | set(defi_entries)
    authors_sorted = sorted(
        (AuthorCount(n, works[n], defi_entries[n]) for n in names),
        key=lambda a: (-a.works, -a.defis, a.name.casefold()),
    )

    numbered = [(name, n) for name, n in per_defi.items() if defi_number(name) is not None]
    series = [(name, n) for name, n in per_defi.items() if defi_number(name) is None]
    numbered.sort(key=lambda item: (-item[1], defi_number(item[0]) or 0))
    series.sort(key=lambda item: (-item[1], item[0].casefold()))

    candidates = {name for name in (*per_defi, *defi_options) if defi_number(name) is not None}
    latest_defi = None
    if candidates:
        latest = max(candidates, key=lambda name: defi_number(name) or 0)
        latest_defi = (latest, per_defi[latest])

    in_progress.sort(key=lambda w: (IN_PROGRESS_STATUSES.index(w.status), w.title.casefold()))

    return ConfrerieStats(
        total_works=total_works,
        total_defi_entries=total_defi_entries,
        authors=authors_sorted,
        numbered_defis=numbered,
        series=series,
        latest_defi=latest_defi,
        in_progress=in_progress,
    )
