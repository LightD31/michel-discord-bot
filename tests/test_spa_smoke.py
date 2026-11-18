"""The dashboard in a real browser: what the user sees when a save fails (F01-F03, S15).

The real app (``create_app``, no bot, no MongoDB) is served on a local port and
driven with Playwright's Chromium. Skipped when no browser can be launched:
CI installs one (``playwright install chromium``); elsewhere, point
``PLAYWRIGHT_CHROMIUM_PATH`` at a Chromium binary.
"""

import json
import logging
import os
import socket
import threading
import time
from typing import Annotated

import pytest
from pydantic import StringConstraints

playwright_sync = pytest.importorskip("playwright.sync_api")

from src.core import config as core_config  # noqa: E402
from src.core.config import config_store  # noqa: E402
from src.webui import schemas  # noqa: E402
from src.webui.auth import Session  # noqa: E402
from src.webui.context import COOKIE_NAME  # noqa: E402
from src.webui.schemas import SchemaBase, enabled_field, ui  # noqa: E402

pytestmark = pytest.mark.browser

GUILD = "123456789012345678"
MODULE = "moduleSmokeTest"


class SmokeConfig(SchemaBase):
    __label__ = "Module de test"
    __description__ = "Seulement pour le test du navigateur."
    __icon__ = "🧪"
    __category__ = "Test"

    enabled: bool = enabled_field()
    greeting: Annotated[str, StringConstraints(max_length=20)] | None = ui(
        "Message d'accueil", "string"
    )
    rounds: int = ui("Nombre de tours", "number", default=3, ge=1, le=10)
    notify: bool = ui("Prévenir les membres", "boolean", default=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def browser():
    try:
        with playwright_sync.sync_playwright() as pw:
            try:
                launched = pw.chromium.launch(
                    executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or None
                )
            except Exception as e:  # noqa: BLE001 — no browser here: skip, don't fail
                pytest.skip(f"no Chromium to drive: {e}")
            yield launched
            launched.close()
    except RuntimeError as e:  # an event loop already running in this thread
        pytest.skip(str(e))


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    import uvicorn

    from src.webui.app import create_app

    port = free_port()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "config": {
                    "discord": {"botToken": "x"},
                    "webui": {
                        "clientId": "1",
                        "clientSecret": "x",  # pragma: allowlist secret
                        "baseUrl": f"http://127.0.0.1:{port}",
                        # Without a bot, only a developer is an admin of anything.
                        "developerUserIds": ["42"],
                    },
                },
                "servers": {GUILD: {MODULE: {"enabled": True, "greeting": "Salut"}}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(config_path))
    config_store._data = None
    schemas.register_module(MODULE)(SmokeConfig)
    # routes/config.py took its copy of the schemas when first imported.
    import src.webui.routes.config as config_routes

    monkeypatch.setitem(config_routes.MODULE_SCHEMAS, MODULE, schemas.MODULE_SCHEMAS[MODULE])

    app = create_app(bot=None)
    ctx = app.state.webui
    session = Session(
        user_id="42",
        username="alice",
        avatar=None,
        guilds=[{"id": GUILD, "name": "Serveur test", "permissions": str(0x20)}],
        expires_at=time.time() + 3600,
    )
    ctx.oauth.sessions[session.token_hash] = session

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"port": port, "session": session, "oauth": ctx.oauth, "config": config_path}
    server.should_exit = True
    thread.join(timeout=5)
    schemas._MODULE_REGISTRY.pop(MODULE, None)
    config_store._data = None


@pytest.fixture
def page(browser, dashboard):
    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": COOKIE_NAME,
                "value": dashboard["session"].session_token,
                "url": f"http://127.0.0.1:{dashboard['port']}",
            }
        ]
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # The app asks with its own dialog: the browser's confirm() is never used.
    page.on("dialog", lambda d: errors.append(f"boîte native : {d.message}") or d.dismiss())
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/{MODULE}")
    page.wait_for_selector('[data-field="greeting"]')
    yield page
    assert errors == [], errors  # no uncaught script error along the way
    context.close()


def demote(dashboard):
    """No longer a developer, and no longer allowed to manage the guild."""
    config_store.mutate(lambda data: data["config"]["webui"].update(developerUserIds=[]))
    dashboard["session"].guilds = [{"id": GUILD, "permissions": "0"}]


def toasts(page) -> list[str]:
    return page.locator("#toasts .toast").all_inner_texts()


def save(page):
    page.get_by_role("button", name="💾 Sauvegarder").click()


def test_a_deep_link_opens_the_module_and_a_save_goes_through(page, dashboard):
    assert page.get_by_label("Message d'accueil").input_value() == "Salut"
    page.get_by_label("Message d'accueil").fill("Bonjour")
    assert page.locator(".unsaved-badge").is_visible()
    save(page)
    # Success, or a warning when the command sync couldn't run (no bot here).
    page.locator("#toasts .toast", has_text="Configuration sauvegardée").first.wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert stored["servers"][GUILD][MODULE]["greeting"] == "Bonjour"
    assert not page.locator(".unsaved-badge").is_visible()


def test_a_refused_save_says_so_instead_of_confirming(page, dashboard):
    """A 403 used to show "Configuration sauvegardée !" (F01)."""
    page.get_by_label("Message d'accueil").fill("Bonjour")
    demote(dashboard)
    save(page)
    page.locator("#toasts .toast.error").first.wait_for()
    texts = toasts(page)
    assert not any("sauvegardée" in t for t in texts), texts
    assert page.get_by_label("Message d'accueil").input_value() == "Bonjour"


def test_a_validation_error_is_readable(page):
    """FastAPI's list of errors used to show as "[object Object]" (F03)."""
    page.get_by_label("Message d'accueil").fill("x" * 40)
    save(page)
    page.locator("#toasts .toast.error").first.wait_for()
    texts = " ".join(toasts(page))
    assert "[object Object]" not in texts
    assert "greeting" in texts


def test_a_number_shows_its_bounds_and_one_outside_them_is_refused(page, dashboard):
    """A starboard threshold of -3 used to be saved with a success toast (Q2)."""
    rounds = page.get_by_label("Nombre de tours")
    assert rounds.get_attribute("min") == "1"
    assert rounds.get_attribute("max") == "10"
    assert page.locator(".form-range", has_text="Entre 1 et 10").is_visible()
    rounds.fill("-3")
    assert rounds.evaluate("el => el.matches(':invalid')")
    save(page)
    page.locator("#toasts .toast.error").first.wait_for()
    texts = " ".join(toasts(page))
    assert "rounds: doit être supérieur ou égal à 1" in texts, texts
    assert not any("sauvegardée" in t for t in toasts(page))
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert "rounds" not in stored["servers"][GUILD][MODULE]


def test_an_unset_switch_shows_its_default_and_keeps_it_on_save(page, dashboard):
    """An unset "yes" by default showed "no", and a save stored no (P2's anonymous messages)."""
    switch = page.get_by_label("Prévenir les membres")
    assert switch.is_checked()
    page.get_by_label("Message d'accueil").fill("Bonjour")
    save(page)
    page.locator("#toasts .toast", has_text="Configuration sauvegardée").first.wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert stored["servers"][GUILD][MODULE]["notify"] is True


def test_a_new_role_menu_starts_with_empty_fields(page, dashboard, monkeypatch):
    """Its first row used to read "undefined" in the emoji and label inputs (Q3)."""
    import src.webui.routes.config as config_routes

    class RoleMenus(SchemaBase):
        __label__ = "Menus de rôles"
        enabled: bool = enabled_field()

    monkeypatch.setitem(schemas._MODULE_REGISTRY, "moduleReactionRoles", RoleMenus)
    monkeypatch.setitem(
        config_routes.MODULE_SCHEMAS,
        "moduleReactionRoles",
        schemas.MODULE_SCHEMAS["moduleReactionRoles"],
    )
    config_store.mutate(
        lambda data: data["servers"][GUILD].update(moduleReactionRoles={"enabled": True})
    )
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/moduleReactionRoles")
    page.get_by_role("button", name="✨ Nouveau menu").click()
    page.wait_for_selector('[data-rrf="emoji"]')
    assert page.locator('[data-rrf="emoji"]').input_value() == ""
    assert page.locator('[data-rrf="label"]').input_value() == ""
    page.get_by_role("button", name="+ Ajouter une entrée").click()
    assert page.locator('[data-rrf="label"]').evaluate_all("els => els.map(e => e.value)") == [
        "",
        "",
    ]


def test_an_expired_session_keeps_the_page_and_the_typing(page, dashboard):
    """A 401 used to swap the page for the login screen, edits and all (F02)."""
    page.get_by_label("Message d'accueil").fill("Pas perdu")
    dashboard["oauth"].sessions.clear()
    save(page)
    page.wait_for_selector("#session-expired")
    assert page.get_by_label("Message d'accueil").input_value() == "Pas perdu"
    assert not any("sauvegardée" in t for t in toasts(page))


def confirm_dialog(page):
    """The app's confirmation dialog, once it is open."""
    dialog = page.get_by_role("alertdialog")
    dialog.wait_for()
    return dialog


def test_leaving_an_edited_form_asks_first(page):
    page.get_by_label("Message d'accueil").fill("Pas encore enregistré")
    page.get_by_role("link", name="Accueil").click()
    dialog = confirm_dialog(page)
    assert "pas enregistrées" in dialog.inner_text()
    # Leaving is the destructive choice: Entrée must not take it.
    assert page.evaluate("document.activeElement.textContent.trim()") == "Annuler"
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert "/module/" in page.url  # still on the form
    assert page.get_by_label("Message d'accueil").input_value() == "Pas encore enregistré"
    page.get_by_role("link", name="Accueil").click()
    confirm_dialog(page).get_by_role("button", name="Abandonner").click()
    page.wait_for_function("() => !location.hash.includes('/module/')")


def test_the_spa_logs_out_but_another_site_cannot(page, browser, dashboard, caplog):
    """The Origin check refuses a cross-site POST, and lets the dashboard's own through (S15)."""
    caplog.set_level(logging.WARNING, logger="webui.request_guard")
    port = dashboard["port"]
    other = browser.new_page()  # about:blank: any page that isn't the dashboard
    other.evaluate(
        "url => fetch(url, {method: 'POST', mode: 'no-cors'}).catch(() => null)",
        f"http://127.0.0.1:{port}/api/reload",
    )
    other.close()
    deadline = time.time() + 5
    while time.time() < deadline and not any(
        "cross-site" in r.getMessage() for r in caplog.records
    ):
        time.sleep(0.05)
    assert any("Refused a cross-site POST /api/reload" in r.getMessage() for r in caplog.records)

    status = page.evaluate("() => fetch('/auth/logout', {method: 'POST'}).then(r => r.status)")
    assert status < 400
    assert dashboard["session"].token_hash not in dashboard["oauth"].sessions


def test_the_secret_santa_card_shows_the_session_and_draws(page, dashboard, monkeypatch):
    """P2: the session on its module page, counts only, and a draw that confirms first."""
    import src.webui.routes.config as config_routes

    class Santa(SchemaBase):
        __label__ = "Secret Santa"
        enabled: bool = enabled_field()

    monkeypatch.setitem(schemas._MODULE_REGISTRY, "moduleSecretSanta", Santa)
    monkeypatch.setitem(
        config_routes.MODULE_SCHEMAS,
        "moduleSecretSanta",
        schemas.MODULE_SCHEMAS["moduleSecretSanta"],
    )
    config_store.mutate(
        lambda data: data["servers"][GUILD].update(moduleSecretSanta={"enabled": True})
    )
    summary = {
        "is_drawn": False,
        "budget": "20 €",
        "exchange_date": "2026-12-24",
        "exchange_label": "jeudi 24 décembre 2026",
        "shipping": True,
        "created_by": "Alice",
        "participants": [
            {"id": "3", "name": "Alice", "has_wishlist": True},
            {"id": "4", "name": "<img src=x onerror=alert(1)>", "has_wishlist": False},
        ],
        "total": 2,
    }
    draws = []

    def answer(route):
        if route.request.method == "POST":
            draws.append(json.loads(route.request.post_data))
            summary["is_drawn"], summary["sent"], summary["received"] = True, 1, 0
            route.fulfill(json={"count": 2, "subgroups": 1, "failed_dms": []})
        else:
            route.fulfill(json={"session": summary})

    page.route(f"**/api/servers/{GUILD}/secretsanta**", answer)
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/moduleSecretSanta")
    card = page.locator("#secretsanta-session")
    card.get_by_text("jeudi 24 décembre 2026").wait_for()
    assert "📦 Par colis" in card.inner_text()
    assert card.locator("img").count() == 0  # a member's name is text, never markup
    assert "<img src=x onerror=alert(1)>" in card.inner_text()
    assert "📝 Listes d'envies" in card.inner_text() and "1/2" in card.inner_text()

    card.get_by_label("Autoriser plusieurs sous-groupes").check()
    card.get_by_role("button", name="🎲 Lancer le tirage").click()
    confirm_dialog(page).get_by_role("button", name="🎲 Lancer le tirage").click()
    page.locator("#toasts .toast", has_text="Tirage effectué pour 2 participants").wait_for()
    assert draws == [{"allow_subgroups": True}]
    card.get_by_text("✅ Tirage effectué").wait_for()
    assert "📦 Colis envoyés" in card.inner_text()
    assert card.get_by_role("button", name="🎲 Lancer le tirage").count() == 0


def test_a_cancelled_draw_sends_nothing(page, dashboard, monkeypatch):
    import src.webui.routes.config as config_routes

    class Santa(SchemaBase):
        __label__ = "Secret Santa"
        enabled: bool = enabled_field()

    monkeypatch.setitem(schemas._MODULE_REGISTRY, "moduleSecretSanta", Santa)
    monkeypatch.setitem(
        config_routes.MODULE_SCHEMAS,
        "moduleSecretSanta",
        schemas.MODULE_SCHEMAS["moduleSecretSanta"],
    )
    config_store.mutate(
        lambda data: data["servers"][GUILD].update(moduleSecretSanta={"enabled": True})
    )
    posts = []
    session = {
        "is_drawn": False,
        "budget": None,
        "exchange_date": None,
        "exchange_label": None,
        "shipping": False,
        "created_by": "Alice",
        "total": 2,
        "participants": [
            {"id": "3", "name": "Alice", "has_wishlist": False},
            {"id": "4", "name": "Bob", "has_wishlist": False},
        ],
    }

    def answer(route):
        if route.request.method == "POST":
            posts.append(route.request.post_data)
        route.fulfill(json={"session": session})

    page.route(f"**/api/servers/{GUILD}/secretsanta**", answer)
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/moduleSecretSanta")
    card = page.locator("#secretsanta-session")
    card.get_by_text("🤝 En main propre").wait_for()
    card.get_by_role("button", name="🎲 Lancer le tirage").click()
    confirm_dialog(page).get_by_role("button", name="Annuler").click()
    page.wait_for_timeout(300)
    assert posts == []


def test_a_field_emptied_in_the_form_is_emptied_in_the_config(page, dashboard):
    """The save merged the form into the stored module: a cleared field stayed (P3)."""
    page.get_by_label("Message d'accueil").fill("")
    save(page)
    page.locator("#toasts .toast", has_text="Configuration sauvegardée").first.wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert "greeting" not in stored["servers"][GUILD][MODULE]
    assert stored["servers"][GUILD][MODULE]["enabled"] is True  # not a field of the form
