"""Validate ``/editeur`` input and shape it into Notion properties.

Column names mirror the live "Les Éditeurs" data source. Choice values are
checked against the options configured in Notion (when the schema is known)
because Notion silently creates any unknown select option — a typo'd genre
would otherwise become a near-duplicate tag in the database.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.errors import ValidationError

from . import notion_props as np

COL_NAME = "Nom"
COL_GENRES = "Genre(s)"
COL_PUBLICS = "Publics"
COL_GROUPE = "Groupe éditorial"
COL_SITE = "Site"
COL_DATE = "Date création"
COL_LIGNE = "Ligne éditoriale"
COL_COMMENTAIRE = "Commentaire"
COL_FONDATEURS = "Fondateur(s)"
COL_EXEMPLES = "Exemples d'auteureurices publié.es"


@dataclass
class EditorInput:
    """One ``/editeur`` submission, slash options plus modal fields."""

    name: str
    genres: list[str]
    publics: list[str]
    groupe: str = ""
    site: str = ""
    date: str = ""
    ligne_editoriale: str = ""
    commentaire: str = ""
    fondateurs: str = ""
    exemples: str = ""


def validate_editor(
    *,
    name: str,
    genres: list[str],
    publics: list[str],
    groupe: str = "",
    site: str = "",
    date: str = "",
    schema: dict[str, Any] | None = None,
) -> EditorInput:
    """Normalise the slash-command options; raise ``ValidationError`` on bad input."""
    schema = schema or {}
    name = name.strip()
    if not name:
        raise ValidationError("Le nom de l'éditeur est obligatoire.")

    genres = np.canonical_choices(
        genres, np.schema_options(schema, COL_GENRES), "Genre", strict=True
    )
    if not genres:
        raise ValidationError("Au moins un genre est obligatoire.")
    publics = np.canonical_choices(
        publics, np.schema_options(schema, COL_PUBLICS), "Public", strict=True
    )
    if not publics:
        raise ValidationError("Au moins un public est obligatoire.")

    groupe = groupe.strip()
    if groupe:
        groupe = np.canonical_choices(
            [groupe], np.schema_options(schema, COL_GROUPE), "Groupe", strict=True
        )[0]

    date = date.strip()
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as e:
            raise ValidationError("La date doit être au format AAAA-MM-JJ.") from e

    site = site.strip()
    if site:
        if " " in site:
            raise ValidationError("Le site web ne doit pas contenir d'espace.")
        if not site.startswith(("http://", "https://")):
            site = f"https://{site}"

    return EditorInput(
        name=name, genres=genres, publics=publics, groupe=groupe, site=site, date=date
    )


def build_editor_properties(editor: EditorInput) -> dict[str, Any]:
    """Notion property payload for ``editor``; empty optional fields are omitted."""
    properties: dict[str, Any] = {
        COL_NAME: np.title_value(editor.name),
        COL_GENRES: np.multi_select_value(editor.genres),
        COL_PUBLICS: np.multi_select_value(editor.publics),
    }
    if editor.groupe:
        properties[COL_GROUPE] = np.select_value(editor.groupe)
    if editor.site:
        properties[COL_SITE] = {"url": editor.site}
    if editor.date:
        properties[COL_DATE] = {"date": {"start": editor.date}}
    for column, value in (
        (COL_LIGNE, editor.ligne_editoriale),
        (COL_COMMENTAIRE, editor.commentaire),
        (COL_FONDATEURS, editor.fondateurs),
        (COL_EXEMPLES, editor.exemples),
    ):
        if value.strip():
            properties[column] = np.rich_text_value(value.strip())
    return properties
