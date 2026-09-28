"""Reading Notion properties the way the live Confrérie schema actually stores them."""

from confrerie_fixtures import oeuvre, rich

from features.confrerie import notion_props as np
from features.confrerie.works import parse_work


def test_plain_text_joins_every_formatting_segment():
    # Notion splits "Texte **dispo** sur Pluminium" into three segments; the
    # old code kept only the first and cut the update note mid-sentence.
    page = oeuvre("p1", "Titre", note=("Texte ", "dispo", " sur Pluminium !"))
    assert np.text(page, "Note de mise à jour") == "Texte dispo sur Pluminium !"


def test_type_is_read_as_multi_select():
    page = oeuvre("p1", "Grief", types=("Défi", "Nouvelle"), genres=("Fantasy",))
    work = parse_work(page)
    assert work.types == ("Défi", "Nouvelle")
    assert work.type_genre == "Défi, Nouvelle — Fantasy"


def test_select_readers_tolerate_the_other_select_kinds():
    page = {
        "properties": {
            "Single": {"type": "select", "select": {"name": "A"}},
            "Multi": {"type": "multi_select", "multi_select": [{"name": "B"}, {"name": "C"}]},
            "Status": {"type": "status", "status": {"name": "En cours"}},
            "Empty": {"type": "select", "select": None},
        }
    }
    assert np.multi_select_names(page, "Single") == ["A"]
    assert np.select_name(page, "Multi") == "B"
    assert np.select_name(page, "Status") == "En cours"
    assert np.select_name(page, "Empty") is None
    assert np.select_name(page, "Missing") is None


def test_external_links_skip_expiring_notion_uploads():
    page = oeuvre(
        "p1",
        "Titre",
        files=(
            {"name": "V1", "type": "external", "external": {"url": "https://ex.org/v1"}},
            {"name": "scan.pdf", "type": "file", "file": {"url": "https://s3/signed"}},
        ),
    )
    assert np.external_links(page, "Lien / Fichier") == [("V1", "https://ex.org/v1")]


def test_page_url_falls_back_to_workspace_url_when_not_published():
    assert np.page_url(oeuvre("abc", "T")).startswith("https://confrerie.notion.site/")
    assert np.page_url(oeuvre("abc", "T", public=False)) == "https://www.notion.so/abc"


def test_title_reads_multi_segment_titles():
    page = {
        "properties": {"Titre": {"type": "title", "title": rich("La charité ", "des carnassiers")}}
    }
    assert np.text(page, "Titre") == "La charité des carnassiers"


def test_schema_options_and_canonical_match():
    schema = {
        "Genre(s)": {
            "type": "multi_select",
            "multi_select": {"options": [{"name": "Science-Fiction"}]},
        }
    }
    options = np.schema_options(schema, "Genre(s)")
    assert options == ["Science-Fiction"]
    assert np.canonical_option("science-fiction ", options) == "Science-Fiction"
    assert np.canonical_option("SF", options) is None
    assert np.schema_options(schema, "Missing") == []


def test_dedupe_keeps_order_and_drops_blanks():
    assert np.dedupe(["Fantasy", "", "Horreur", "Fantasy", "  "]) == ["Fantasy", "Horreur"]


def test_filter_to_schema_drops_unknown_columns():
    kept, dropped = np.filter_to_schema({"Nom": 1, "Note": 2}, {"Nom": {}})
    assert kept == {"Nom": 1}
    assert dropped == ["Note"]
    # Schema unavailable: keep everything rather than silently emptying the page.
    assert np.filter_to_schema({"Nom": 1}, {}) == ({"Nom": 1}, [])
