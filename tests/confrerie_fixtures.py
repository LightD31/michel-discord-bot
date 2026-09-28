"""Notion API payloads shaped like the live Confrérie data sources.

The property types mirror the real "Les Oeuvres/Défis" and "Les Éditeurs"
schemas (``Type`` is a multi-select, ``Avancement`` a status, …) — the
extension broke precisely where it assumed otherwise.
"""

from typing import Any


def rich(*parts: str) -> list[dict[str, Any]]:
    return [{"type": "text", "plain_text": p, "text": {"content": p}} for p in parts]


def oeuvre(
    page_id: str,
    titre: str,
    *,
    auteurs: tuple[str, ...] = (),
    defi: str | None = None,
    types: tuple[str, ...] = (),
    genres: tuple[str, ...] = (),
    avancement: str | None = None,
    parent: bool = False,
    note: tuple[str, ...] = (),
    files: tuple[dict[str, Any], ...] = (),
    public: bool = True,
) -> dict[str, Any]:
    compact = page_id.replace("-", "")
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{compact}",
        "public_url": f"https://confrerie.notion.site/{compact}" if public else None,
        "properties": {
            "Titre": {"type": "title", "title": rich(titre)},
            "Auteur": {"type": "multi_select", "multi_select": [{"name": a} for a in auteurs]},
            "Défi": {"type": "select", "select": {"name": defi} if defi else None},
            "Type": {"type": "multi_select", "multi_select": [{"name": t} for t in types]},
            "Genre": {"type": "multi_select", "multi_select": [{"name": g} for g in genres]},
            "Avancement": {
                "type": "status",
                "status": {"name": avancement} if avancement else None,
            },
            "Parent item": {
                "type": "relation",
                "relation": [{"id": "parent-page"}] if parent else [],
            },
            "Note de mise à jour": {"type": "rich_text", "rich_text": rich(*note)},
            "Lien / Fichier": {"type": "files", "files": list(files)},
            "Update": {"type": "checkbox", "checkbox": False},
        },
    }


def _options(*names: str) -> dict[str, Any]:
    return {"options": [{"id": str(i), "name": n} for i, n in enumerate(names)]}


EDITEURS_SCHEMA: dict[str, Any] = {
    "Nom": {"type": "title", "title": {}},
    "Ligne éditoriale": {"type": "rich_text", "rich_text": {}},
    "Groupe éditorial": {
        "type": "select",
        "select": _options("Editis", "Hachette", "Gallimard", "Indépendant"),
    },
    "Publics": {
        "type": "multi_select",
        "multi_select": _options("Ado", "Adulte", "New Adult", "Non précisé", "Young Adult"),
    },
    "Commentaire": {"type": "rich_text", "rich_text": {}},
    "Fondateur(s)": {"type": "rich_text", "rich_text": {}},
    "Exemples d'auteureurices publié.es": {"type": "rich_text", "rich_text": {}},
    "Date création": {"type": "date", "date": {}},
    "Site": {"type": "url", "url": {}},
    "Genre(s)": {
        "type": "multi_select",
        "multi_select": _options(
            "Art/Beaux livres",
            "BD/Manga/roman graphique",
            "Fantastique",
            "Fantasy",
            "Science-Fiction",
        ),
    },
}

DEFI_OPTIONS = (
    "Défi 14 : Écriture automatique",
    "Défi 12 : Questionnaire Personnage",
    "Défi 01: Lipogramme en E",
    "SBL24",
    "WBL24",
    "Défi 17 : Traduction créative",
    "SBL26",
)
