"""Drafts built from forum posts, défi answers and /demande."""

import pytest
from confrerie_fixtures import DEFI_OPTIONS, OEUVRES_SCHEMA

from features.confrerie import notion_props as np
from features.confrerie.drafts import (
    SOURCE_DEFI,
    Draft,
    build_work_properties,
    defi_draft_id,
    looks_like_answer,
    match_defi,
    resolve_author,
    split_list,
    tags_to_options,
    textes_draft_id,
    title_from_message,
)
from src.core.errors import ValidationError


@pytest.mark.parametrize(
    ("thread_name", "expected"),
    [
        ("Défi 12 - questionnaire", "Défi 12 : Questionnaire Personnage"),
        ("DEFI 1 : lipogramme", "Défi 01: Lipogramme en E"),
        ("Défi n°17", "Défi 17 : Traduction créative"),
        ("SBL26 — l'été des plumes", "SBL26"),
        ("[WBL24] hiver", "WBL24"),
        ("Défi 99 : inconnu", None),
        ("Discussion générale", None),
        ("SBL2", None),  # must not match SBL24 / SBL26 on a prefix
    ],
)
def test_match_defi(thread_name, expected):
    assert match_defi(thread_name, DEFI_OPTIONS) == expected


def test_answers_are_told_apart_from_comments():
    assert not looks_like_answer("Bravo, super défi !", has_attachment=False)
    assert looks_like_answer("", has_attachment=True)
    assert looks_like_answer("Mon texte : https://pluminium.fr/abc", has_attachment=False)
    assert looks_like_answer("x" * 300, has_attachment=False)


def test_title_from_message():
    assert title_from_message("# **Le phare**\n\nIl était une fois…", "fb") == "Le phare"
    assert title_from_message("", "Défi 12 — Mahé") == "Défi 12 — Mahé"
    assert title_from_message("x" * 150, "fb") == "fb"


def test_resolve_author_uses_the_configured_map():
    assert resolve_author(42, "mahe_du_92", {"42": "Mahé"}) == ("Mahé", True)
    assert resolve_author(7, "inconnu", {"42": "Mahé"}) == ("inconnu", False)


def test_forum_tags_map_onto_notion_options():
    assert tags_to_options(["fantasy", "Spoiler", "Fantasy"], ["Fantasy", "Horreur"]) == ["Fantasy"]


def test_draft_ids_dedupe_per_thread_and_member():
    assert textes_draft_id(1) == "textes-1"
    assert defi_draft_id(1, 2) == "defi-1-2"
    assert defi_draft_id(1, 2) != defi_draft_id(1, 3)


def test_draft_roundtrips_through_a_mongo_document():
    draft = Draft(id="defi-1-2", source=SOURCE_DEFI, guild_id="9", submitter_id=2, title="T")
    doc = draft.to_doc()
    assert doc["_id"] == "defi-1-2" and "id" not in doc
    assert Draft.from_doc({**doc, "legacy_field": 1}) == draft


def test_work_properties_only_target_existing_columns():
    draft = Draft(
        id="defi-1-2",
        source=SOURCE_DEFI,
        guild_id="9",
        submitter_id=2,
        title="Grief",
        authors=["Mahé"],
        types=["Défi"],
        genres=["Fantasy"],
        defi="Défi 12 : Questionnaire Personnage",
        link="https://discord.com/channels/9/1/3",
        link_label="Réponse Discord",
    )
    properties = build_work_properties(draft)
    _, dropped = np.filter_to_schema(properties, OEUVRES_SCHEMA)
    assert dropped == []
    assert properties["Lien / Fichier"]["files"][0]["external"]["url"].endswith("/1/3")
    assert properties["Défi"] == {"select": {"name": "Défi 12 : Questionnaire Personnage"}}


def test_canonical_choices_strict_and_lenient():
    options = ["Fantasy", "Horreur"]
    assert np.canonical_choices(["fantasy", ""], options, "Genre", strict=True) == ["Fantasy"]
    with pytest.raises(ValidationError):
        np.canonical_choices(["Cosy"], options, "Genre", strict=True)
    # The owner may introduce a new value.
    assert np.canonical_choices(["Cosy"], options, "Genre", strict=False) == ["Cosy"]


def test_split_list():
    assert split_list("Mahé, Clotilde,, Mahé ") == ["Mahé", "Clotilde"]
