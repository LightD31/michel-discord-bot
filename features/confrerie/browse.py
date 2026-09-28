"""Read-side helpers for ``/texte``, ``/auteur`` and ``/outil`` (no Discord imports).

Column names mirror the live « Répertoire des membres » and « Boîte à outils »
data sources.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import notion_props as np
from .stats import COL_AUTHOR, COL_DEFI, COL_PARENT
from .works import Work, parse_work

# Répertoire des membres: one row per (member, link).
COL_MEMBER_LABEL = "Nom"
COL_MEMBER = "Membre"
COL_MEMBER_URL = "URL"
COL_MEMBER_SITE = "Site"

# Boîte à outils.
COL_TOOL_NAME = "Nom de la ressource"
COL_TOOL_URL = "Lien"
COL_TOOL_THEME = "Thème"
COL_TOOL_DESCRIPTION = "Description"
COL_TOOL_REVIEW = "Avis de la Confrérie"
COL_TOOL_TESTED = "Testé"

LATEST_WORKS = 5


def top_level_works(pages: Iterable[dict[str, Any]]) -> list[Work]:
    """Every œuvre that isn't a part (chapter, tome) of another one."""
    return [parse_work(p) for p in pages if not np.relation_ids(p, COL_PARENT)]


def search_works(works: Iterable[Work], query: str, limit: int = 25) -> list[Work]:
    """Titles containing ``query`` (case-insensitive), prefix matches first."""
    wanted = query.casefold().strip()
    matches = [w for w in works if wanted in w.title.casefold()]
    matches.sort(key=lambda w: (not w.title.casefold().startswith(wanted), w.title.casefold()))
    return matches[:limit]


def find_work(works: Iterable[Work], key: str) -> Work | None:
    """Resolve an autocomplete value (page id) or a typed title."""
    works = list(works)
    for work in works:
        if work.page_id == key:
            return work
    wanted = key.casefold().strip()
    exact = [w for w in works if w.title.casefold() == wanted]
    if exact:
        return exact[0]
    partial = search_works(works, key, limit=1)
    return partial[0] if partial else None


@dataclass(frozen=True)
class AuthorProfile:
    name: str
    works: int
    defis: int
    latest: tuple[Work, ...]


def _sort_key_latest(work: Work) -> tuple[str, str]:
    # ISO dates sort lexically; undated works go last, then by title.
    return (work.last_published or "", work.title.casefold())


def author_profile(pages: Iterable[dict[str, Any]], name: str) -> AuthorProfile | None:
    """Counts and most recent works of the author ``name`` (case-insensitive)."""
    wanted = name.casefold().strip()
    works: list[Work] = []
    defis = 0
    canonical = None
    for page in pages:
        authors = np.multi_select_names(page, COL_AUTHOR)
        match = next((a for a in authors if a.casefold() == wanted), None)
        if match is None:
            continue
        canonical = canonical or match
        if np.select_name(page, COL_DEFI):
            defis += 1
        if not np.relation_ids(page, COL_PARENT):
            works.append(parse_work(page))
    if canonical is None:
        return None
    dated = sorted((w for w in works if w.last_published), key=_sort_key_latest, reverse=True)
    undated = sorted((w for w in works if not w.last_published), key=lambda w: w.title.casefold())
    return AuthorProfile(
        name=canonical,
        works=len(works),
        defis=defis,
        latest=tuple([*dated, *undated][:LATEST_WORKS]),
    )


@dataclass(frozen=True)
class MemberLink:
    label: str
    site: str
    url: str


def member_links(pages: Iterable[dict[str, Any]], name: str) -> list[MemberLink]:
    """Links listed for ``name`` in the Répertoire des membres."""
    wanted = name.casefold().strip()
    links = []
    for page in pages:
        member = np.select_name(page, COL_MEMBER) or ""
        url = np.url_value(page, COL_MEMBER_URL)
        if member.casefold() == wanted and url:
            links.append(
                MemberLink(
                    label=np.text(page, COL_MEMBER_LABEL),
                    site=np.select_name(page, COL_MEMBER_SITE) or "",
                    url=url,
                )
            )
    links.sort(key=lambda link: (link.site.casefold(), link.label.casefold()))
    return links


@dataclass(frozen=True)
class Tool:
    name: str
    url: str
    themes: tuple[str, ...]
    description: str
    review: str
    tested: bool


def parse_tool(page: dict[str, Any]) -> Tool:
    return Tool(
        name=np.text(page, COL_TOOL_NAME) or "Sans nom",
        url=np.url_value(page, COL_TOOL_URL),
        themes=tuple(np.multi_select_names(page, COL_TOOL_THEME)),
        description=np.text(page, COL_TOOL_DESCRIPTION),
        review=np.text(page, COL_TOOL_REVIEW),
        tested=np.checkbox(page, COL_TOOL_TESTED),
    )


def filter_tools(tools: Iterable[Tool], theme: str = "", query: str = "") -> list[Tool]:
    """Tools tagged ``theme`` whose name or description contains ``query``."""
    theme_cf = theme.casefold().strip()
    query_cf = query.casefold().strip()
    result = [
        tool
        for tool in tools
        if (not theme_cf or any(t.casefold() == theme_cf for t in tool.themes))
        and (
            not query_cf
            or query_cf in tool.name.casefold()
            or query_cf in tool.description.casefold()
        )
    ]
    result.sort(key=lambda tool: (not tool.tested, tool.name.casefold()))
    return result
