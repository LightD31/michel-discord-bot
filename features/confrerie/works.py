"""Flatten an Œuvres page into the fields the Discord embeds show."""

from dataclasses import dataclass
from typing import Any

from . import notion_props as np
from .stats import (
    COL_AUTHOR,
    COL_DEFI,
    COL_FILES,
    COL_GENRE,
    COL_LAST_PUBLISHED,
    COL_STATUS,
    COL_TITLE,
    COL_TYPE,
    COL_UPDATE_NOTE,
)


@dataclass(frozen=True)
class Work:
    page_id: str
    title: str
    authors: tuple[str, ...]
    types: tuple[str, ...]
    genres: tuple[str, ...]
    defi: str | None
    status: str | None
    last_published: str | None
    update_note: str
    links: tuple[tuple[str, str], ...]
    url: str

    @property
    def type_genre(self) -> str:
        """Summary such as ``Nouvelle — Fantasy, Horreur`` (empty when both are)."""
        types = ", ".join(self.types)
        genres = ", ".join(self.genres)
        return " — ".join(part for part in (types, genres) if part)


def parse_work(page: dict[str, Any]) -> Work:
    return Work(
        page_id=page.get("id", ""),
        title=np.text(page, COL_TITLE) or "Sans titre",
        authors=tuple(np.multi_select_names(page, COL_AUTHOR)),
        types=tuple(np.multi_select_names(page, COL_TYPE)),
        genres=tuple(np.multi_select_names(page, COL_GENRE)),
        defi=np.select_name(page, COL_DEFI),
        status=np.select_name(page, COL_STATUS),
        last_published=np.date_start(page, COL_LAST_PUBLISHED),
        update_note=np.text(page, COL_UPDATE_NOTE),
        links=tuple(np.external_links(page, COL_FILES)),
        url=np.page_url(page),
    )
