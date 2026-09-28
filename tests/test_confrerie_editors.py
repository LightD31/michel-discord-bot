"""`/editeur` validation and the Notion payload it produces."""

import pytest
from confrerie_fixtures import EDITEURS_SCHEMA

from features.confrerie import notion_props as np
from features.confrerie.editors import build_editor_properties, validate_editor
from src.core.errors import ValidationError


def _validate(**overrides):
    args = {
        "name": "Mnémos",
        "genres": ["fantasy", "Science-fiction", "Fantasy"],
        "publics": ["adulte", ""],
        "schema": EDITEURS_SCHEMA,
    }
    args.update(overrides)
    return validate_editor(**args)


def test_choices_are_mapped_to_configured_options_and_deduped():
    editor = _validate()
    assert editor.genres == ["Fantasy", "Science-Fiction"]
    assert editor.publics == ["Adulte"]


def test_unknown_option_is_rejected_instead_of_creating_a_new_tag():
    with pytest.raises(ValidationError, match="BD/Manga"):
        _validate(genres=["BD/Manga"])


def test_unknown_groupe_is_rejected():
    with pytest.raises(ValidationError):
        _validate(groupe="Madrigall")
    assert _validate(groupe="gallimard").groupe == "Gallimard"


def test_without_schema_values_are_kept_as_typed():
    editor = _validate(genres=["Roman"], schema={})
    assert editor.genres == ["Roman"]


def test_required_fields():
    with pytest.raises(ValidationError):
        _validate(name="  ")
    with pytest.raises(ValidationError):
        _validate(genres=["", ""])


def test_date_and_site_normalisation():
    with pytest.raises(ValidationError):
        _validate(date="12/05/2020")
    editor = _validate(date="2020-05-12", site="mnemos.com")
    assert editor.site == "https://mnemos.com"
    with pytest.raises(ValidationError):
        _validate(site="mnemos .com")


def test_properties_only_target_existing_columns():
    # Every column the builder writes must exist in the live Éditeurs schema:
    # Notion rejects the whole page when a single property name is unknown
    # (the old code wrote Note / Présentation / Taille, which don't exist).
    editor = _validate(groupe="Gallimard", site="https://mnemos.com", date="2020-05-12")
    editor.ligne_editoriale = "Imaginaire francophone"
    editor.fondateurs = "Frédéric Weil"
    editor.exemples = "Jean-Philippe Jaworski"
    editor.commentaire = "Très bon accueil"

    properties = build_editor_properties(editor)
    _, dropped = np.filter_to_schema(properties, EDITEURS_SCHEMA)
    assert dropped == []
    assert properties["Ligne éditoriale"]["rich_text"][0]["text"]["content"] == (
        "Imaginaire francophone"
    )
    assert properties["Genre(s)"]["multi_select"] == [
        {"name": "Fantasy"},
        {"name": "Science-Fiction"},
    ]


def test_empty_optional_fields_are_omitted():
    properties = build_editor_properties(_validate())
    assert set(properties) == {"Nom", "Genre(s)", "Publics"}
