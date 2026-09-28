"""Helpers behind /texte, /auteur and /outil."""

from confrerie_fixtures import oeuvre, rich

from features.confrerie.browse import (
    author_profile,
    filter_tools,
    find_work,
    member_links,
    parse_tool,
    search_works,
    top_level_works,
)


def _dated(page: dict, day: str) -> dict:
    page["properties"]["Dernière publication le"] = {"type": "date", "date": {"start": day}}
    return page


PAGES = [
    _dated(oeuvre("a", "La charité des carnassiers", auteurs=("Mahé",)), "2024-03-01"),
    _dated(oeuvre("b", "Carnets", auteurs=("Mahé", "Clotilde")), "2025-01-10"),
    oeuvre("c", "Chapitre 3", auteurs=("Mahé",), parent=True, defi="SBL24"),
    oeuvre("d", "Grief", auteurs=("Mahé",), defi="Défi 12 : Questionnaire Personnage"),
    oeuvre("e", "Abel", auteurs=("Clotilde",)),
]


def test_top_level_works_skip_parts():
    assert [w.page_id for w in top_level_works(PAGES)] == ["a", "b", "d", "e"]


def test_search_ranks_prefix_matches_first():
    works = top_level_works(PAGES)
    assert [w.title for w in search_works(works, "car")] == [
        "Carnets",
        "La charité des carnassiers",
    ]
    assert len(search_works(works, "")) == 4


def test_find_work_by_id_exact_title_or_partial():
    works = top_level_works(PAGES)
    assert find_work(works, "d").title == "Grief"
    assert find_work(works, "abel").page_id == "e"
    assert find_work(works, "carnass").page_id == "a"
    assert find_work(works, "introuvable") is None


def test_author_profile_counts_and_latest_first():
    profile = author_profile(PAGES, "mahé")
    assert profile.name == "Mahé"
    assert profile.works == 3  # the chapter is a part, not a work
    assert profile.defis == 2  # …but its défi participation counts
    assert [w.title for w in profile.latest] == [
        "Carnets",
        "La charité des carnassiers",
        "Grief",
    ]
    assert author_profile(PAGES, "Personne") is None


def _member(label: str, member: str, site: str, url: str | None) -> dict:
    return {
        "properties": {
            "Nom": {"type": "title", "title": rich(label)},
            "Membre": {"type": "select", "select": {"name": member}},
            "Site": {"type": "select", "select": {"name": site}},
            "URL": {"type": "url", "url": url},
        }
    }


def test_member_links_match_case_insensitively_and_skip_empty_urls():
    rows = [
        _member("Profil", "Mahé", "Pluminium", "https://pluminium.fr/mahe"),
        _member("Insta", "mahé", "Instagram", "https://instagram.com/mahe"),
        _member("Vide", "Mahé", "Linktree", None),
        _member("Autre", "Clotilde", "Linktree", "https://linktr.ee/clo"),
    ]
    assert [(link.site, link.url) for link in member_links(rows, "Mahé")] == [
        ("Instagram", "https://instagram.com/mahe"),
        ("Pluminium", "https://pluminium.fr/mahe"),
    ]


def _tool(name: str, themes: tuple[str, ...], description: str = "", tested: bool = False):
    return {
        "properties": {
            "Nom de la ressource": {"type": "title", "title": rich(name)},
            "Lien": {"type": "url", "url": f"https://{name.lower()}.example"},
            "Thème": {"type": "multi_select", "multi_select": [{"name": t} for t in themes]},
            "Description": {"type": "rich_text", "rich_text": rich(description)},
            "Avis de la Confrérie": {"type": "rich_text", "rich_text": []},
            "Testé": {"type": "checkbox", "checkbox": tested},
        }
    }


def test_tools_filter_by_theme_and_query_tested_first():
    tools = [
        parse_tool(_tool("Inkarnate", ("Création de carte",), "Cartes fantasy")),
        parse_tool(_tool("Wonderdraft", ("Création de carte",), "Cartes détaillées", True)),
        parse_tool(_tool("CNRTL", ("Dictionnaire",), "Définitions et synonymes", True)),
    ]
    assert [t.name for t in filter_tools(tools, theme="création de carte")] == [
        "Wonderdraft",
        "Inkarnate",
    ]
    assert [t.name for t in filter_tools(tools, query="synonymes")] == ["CNRTL"]
    assert len(filter_tools(tools)) == 3
    assert tools[0].url == "https://inkarnate.example"
