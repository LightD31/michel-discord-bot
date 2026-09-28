"""Recap statistics aggregated from the Œuvres data source."""

from confrerie_fixtures import DEFI_OPTIONS, oeuvre

from features.confrerie.stats import AuthorCount, WorkInProgress, aggregate, defi_number


def test_defi_number_parses_numbered_defis_only():
    assert defi_number("Défi 01: Lipogramme en E") == 1
    assert defi_number("Défi 12 : Questionnaire Personnage") == 12
    assert defi_number("SBL26") is None
    assert defi_number("WBL24") is None


def _pages():
    return [
        oeuvre("1", "Saga", auteurs=("Mahé",), types=("Saga",), avancement="En cours"),
        oeuvre("2", "Tome 1", auteurs=("Mahé",), parent=True),
        oeuvre("3", "Grief", auteurs=("Mahé",), defi="Défi 12 : Questionnaire Personnage"),
        oeuvre("4", "Abel", auteurs=("Clotilde",), defi="Défi 12 : Questionnaire Personnage"),
        oeuvre("5", "Pitch", auteurs=("Clotilde",), defi="SBL24"),
        oeuvre("6", "Pitch 2", auteurs=("Clotilde",), defi="SBL24"),
        oeuvre("7", "Pitch 3", auteurs=("Akibo",), defi="SBL24"),
        oeuvre("8", "Chapitre", auteurs=("Akibo",), defi="Défi 01: Lipogramme en E", parent=True),
        oeuvre("9", "Relu", auteurs=("Akibo",), avancement="En relecture"),
        oeuvre("10", "Brouillon", auteurs=("Akibo",), avancement="En cours"),
    ]


def test_parts_count_as_defi_entries_but_not_as_works():
    stats = aggregate(_pages())
    assert stats.total_works == 8  # pages 2 and 8 are parts
    assert stats.total_defi_entries == 6  # includes the défi chapter (page 8)


def test_authors_ranked_by_works_then_defis_then_name():
    assert aggregate(_pages()).authors == [
        AuthorCount("Clotilde", works=3, defis=3),
        AuthorCount("Akibo", works=3, defis=2),
        AuthorCount("Mahé", works=2, defis=1),
    ]


def test_series_are_ranked_apart_from_numbered_defis():
    stats = aggregate(_pages())
    assert stats.numbered_defis == [
        ("Défi 12 : Questionnaire Personnage", 2),
        ("Défi 01: Lipogramme en E", 1),
    ]
    assert stats.series == [("SBL24", 3)]


def test_latest_defi_includes_options_nobody_answered_yet():
    assert aggregate(_pages()).latest_defi == ("Défi 12 : Questionnaire Personnage", 2)
    assert aggregate(_pages(), DEFI_OPTIONS).latest_defi == ("Défi 17 : Traduction créative", 0)


def test_in_progress_lists_top_level_works_by_status_then_title():
    assert aggregate(_pages()).in_progress == [
        WorkInProgress("Brouillon", "En cours", ("Akibo",)),
        WorkInProgress("Saga", "En cours", ("Mahé",)),
        WorkInProgress("Relu", "En relecture", ("Akibo",)),
    ]


def test_aggregate_is_order_independent():
    # The recap refresher skips the edit when the payload is unchanged, so the
    # same database returned in a different order must render the same stats.
    assert aggregate(_pages()) == aggregate(list(reversed(_pages())))


def test_empty_database():
    stats = aggregate([])
    assert stats.total_works == 0
    assert stats.authors == []
    assert stats.latest_defi is None
