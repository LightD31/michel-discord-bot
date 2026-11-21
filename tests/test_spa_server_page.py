"""The server page and the first-steps wizard in a real browser (C1, C2).

Same app as ``test_spa_smoke.py`` (no bot, no MongoDB), whose fixtures this
reuses: the modules filed under Actifs / Disponibles, the search, hiding the
"Pour bien démarrer" list, and a pack applied from the wizard.
"""

import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_spa_smoke as smoke  # noqa: E402

import extensions.feur  # noqa: E402, F401 — registers moduleFeur, in the "Entre potes" pack
import extensions.xp._common  # noqa: E402, F401 — registers moduleXp, in the "Entre potes" pack

GUILD = smoke.GUILD
toasts = smoke.toasts
# The smoke tests' fixtures: a browser, the app on a local port, a logged-in page.
browser = smoke.browser
dashboard = smoke.dashboard
page = smoke.page

pytestmark = pytest.mark.browser


def open_server(page, dashboard):
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/server/{GUILD}")
    page.wait_for_selector("#module-browser .module-card")


def test_modules_are_filed_and_searched(page, dashboard):
    open_server(page, dashboard)
    browser_ = page.locator("#module-browser")
    assert browser_.get_by_text("Module de test").is_visible()  # on: under Actifs
    page.get_by_role("button", name="Disponibles").click()
    assert not browser_.get_by_text("Module de test").is_visible()
    page.get_by_label("Chercher un module").fill("module de TEST")
    assert browser_.get_by_text("Module de test").is_visible()  # a search looks everywhere
    page.get_by_label("Chercher un module").fill("introuvable")
    assert browser_.get_by_text("Aucun module ne correspond").is_visible()


def test_the_first_steps_list_can_be_hidden_and_brought_back(page, dashboard):
    open_server(page, dashboard)
    card = page.locator("#onboarding-card")
    card.wait_for()
    # No bot: Michel's rights can't be read, so that step says why.
    assert card.get_by_text("Bot hors ligne").is_visible()
    card.get_by_role("button", name="Masquer").click()
    page.get_by_role("button", name="réafficher").wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert stored["servers"][GUILD]["onboardingDismissed"] is True
    page.get_by_role("button", name="réafficher").click()
    page.locator("#onboarding-card").wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert "onboardingDismissed" not in stored["servers"][GUILD]


def test_a_pack_from_the_wizard_switches_its_modules_on(page, dashboard):
    # The smoke server has a module on already: the list's "Lancer l'assistant"
    # is gone, and the wizard is reached by its link.
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/setup/{GUILD}")
    page.get_by_role("radio", name="Entre potes").click()
    page.get_by_role("button", name="Continuer →").click()
    page.get_by_role("button", name="Activer ces modules").click()
    page.get_by_text("Vérifie le message que reçoit chaque nouveau membre.").wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))["servers"][GUILD]
    assert stored["moduleXp"] == {"enabled": True}
    assert stored["moduleFeur"] == {"enabled": True}
    assert "moduleWelcome" not in stored  # its channel is required, and none was chosen
    assert any("activé" in t for t in toasts(page))
    page.get_by_role("button", name="Continuer →").click()
    page.get_by_role("button", name="Terminer").click()
    page.wait_for_selector("#module-browser")
    assert page.url.endswith(f"#/server/{GUILD}")


def test_sur_mesure_opens_the_whole_catalogue(page, dashboard):
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/setup/{GUILD}")
    page.get_by_role("radio", name="Sur mesure").click()
    page.get_by_role("button", name="Continuer →").click()
    page.wait_for_selector("#module-browser")
    assert page.get_by_role("button", name="Disponibles").get_attribute("aria-pressed") == "true"
