"""The save bar, Ctrl+S and a module's history in a real browser (C5).

Same app as ``test_spa_smoke.py`` (no bot, no MongoDB), whose fixtures this
reuses; the history the page reads is answered by ``page.route``.
"""

import json

import pytest

pytest.importorskip("playwright.sync_api")

import test_spa_smoke as smoke  # noqa: E402

GUILD, MODULE = smoke.GUILD, smoke.MODULE
dashboard = smoke.dashboard
page = smoke.page

pytestmark = pytest.mark.browser


def stored(dashboard):
    return json.loads(dashboard["config"].read_text(encoding="utf-8"))["servers"][GUILD][MODULE]


def test_the_bar_counts_the_fields_and_ctrl_s_saves(page, dashboard):
    bar = page.locator("#save-bar")
    assert not bar.is_visible()
    page.get_by_label("Message d'accueil").fill("Bonjour")
    bar.wait_for()
    assert "1 champ modifié" in bar.inner_text()
    page.get_by_label("Nombre de tours").fill("5")
    assert "2 champs modifiés" in bar.inner_text()
    page.keyboard.press("Control+s")
    page.locator("#toasts .toast", has_text="Configuration sauvegardée").first.wait_for()
    bar.wait_for(state="hidden")
    assert stored(dashboard)["greeting"] == "Bonjour"


def test_annuler_puts_the_saved_settings_back(page, dashboard):
    page.get_by_label("Message d'accueil").fill("Oups")
    page.locator("#save-bar").get_by_role("button", name="Annuler").click()
    page.locator("#confirm-ok").click()
    page.locator("#save-bar").wait_for(state="hidden")
    assert page.get_by_label("Message d'accueil").input_value() == "Salut"
    assert stored(dashboard)["greeting"] == "Salut"


def test_the_history_shows_what_changed_and_restores(page):
    restored = []
    version = {
        "id": "ab" * 12,
        "at": "2026-11-20T10:00:00+00:00",
        "settings": {"enabled": True, "greeting": "<b>Avant</b>", "rounds": 3},
        "changed": ["greeting"],
    }
    page.route(
        "**/api/servers/*/modules/*/history",
        lambda route: route.fulfill(json={"versions": [version]}),
    )
    page.route(
        "**/api/servers/*/modules/*/history/*/restore",
        lambda route: restored.append(route.request.url) or route.fulfill(json={"status": "ok"}),
    )
    page.get_by_role("button", name="🕘 Historique").click()
    modal = page.locator("#modal-overlay")
    modal.get_by_text("Message d'accueil").wait_for()
    # The old value is text, not markup.
    assert modal.locator(".history-before").inner_text() == "<b>Avant</b>"
    assert modal.locator(".history-after").inner_text() == "Salut"
    modal.get_by_role("button", name="↩ Restaurer").click()
    page.locator("#confirm-ok").click()
    page.locator("#toasts .toast", has_text="Version restaurée").first.wait_for()
    assert restored and restored[0].endswith(f"/{version['id']}/restore")
    assert f"/servers/{GUILD}/modules/{MODULE}/" in restored[0]
