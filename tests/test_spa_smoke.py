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


def test_leaving_an_edited_form_asks_first(page):
    page.get_by_label("Message d'accueil").fill("Pas encore enregistré")
    asked: list[str] = []

    def dismiss(dialog):
        asked.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", dismiss)
    page.get_by_role("link", name="Accueil").click()
    assert asked and "pas enregistrées" in asked[0]
    assert "/module/" in page.url  # still on the form
    assert page.get_by_label("Message d'accueil").input_value() == "Pas encore enregistré"


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
