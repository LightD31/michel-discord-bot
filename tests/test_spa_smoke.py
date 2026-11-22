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
    board: str | None = ui("Forum suivi", "forum")


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
    page.on("console", lambda m: script_error(m) and errors.append(m.text))
    # The app asks with its own dialog: the browser's confirm() is never used.
    page.on("dialog", lambda d: errors.append(f"boîte native : {d.message}") or d.dismiss())
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/{MODULE}")
    page.wait_for_selector('[data-field="greeting"]')
    yield page
    assert errors == [], errors  # no uncaught script error along the way
    context.close()


def script_error(message) -> bool:
    """A console error from the app: a module that didn't load, an unknown action.

    A failed request (an API answering 503 on purpose) is logged too, as
    "Failed to load resource": the test asserts on what the page shows instead.
    """
    return message.type == "error" and not message.text.startswith("Failed to load resource")


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
    page.locator("#toasts .toast.error", has_text="Configuration invalide").wait_for()
    texts = " ".join(toasts(page))
    assert "[object Object]" not in texts
    assert "« Message d'accueil » : 20 caractères au plus" in texts  # its label (P15)


def test_a_number_shows_its_bounds_and_one_outside_them_is_refused(page, dashboard):
    """A starboard threshold of -3 used to be saved with a success toast (Q2)."""
    rounds = page.get_by_label("Nombre de tours")
    assert rounds.get_attribute("min") == "1"
    assert rounds.get_attribute("max") == "10"
    assert page.locator(".form-range", has_text="Entre 1 et 10").is_visible()
    rounds.fill("-3")
    assert rounds.evaluate("el => el.matches(':invalid')")
    save(page)
    page.locator("#toasts .toast.error", has_text="Configuration invalide").wait_for()
    texts = " ".join(toasts(page))
    assert "« Nombre de tours » : doit être supérieur ou égal à 1" in texts, texts
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

    # Its look and limit (held over from P7) go with what the builder sends.
    page.get_by_label("Présentation").select_option("select")
    page.get_by_label("Rôles par membre").fill("1")
    payload = page.evaluate(
        """async () => {
            const v = new URL(document.querySelector('script[type=module]').src).search;
            const m = await import(`/static/js/features/rolemenus.js${v}`);
            return m._collectRoleMenuPayload();
        }"""
    )
    assert (payload["style"], payload["max_roles"]) == ("select", 1)
    page.get_by_label("Rôles par membre").fill("")
    payload = page.evaluate(
        """async () => {
            const v = new URL(document.querySelector('script[type=module]').src).search;
            return (await import(`/static/js/features/rolemenus.js${v}`))._collectRoleMenuPayload();
        }"""
    )
    assert payload["max_roles"] is None  # a new menu: no limit


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
        if route.request.url.endswith("/reveal"):
            pairs = [
                {"giver": "Alice", "receiver": "<b>Bob</b>"},
                {"giver": "<b>Bob</b>", "receiver": "Alice"},
            ]
            route.fulfill(json={"pairs": pairs})
        elif route.request.method == "POST":
            draws.append(json.loads(route.request.post_data))
            summary["is_drawn"], summary["sent"], summary["received"] = True, 1, 0
            summary["can_reveal"] = True  # an administrator, as the API says
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

    # P15: an administrator reveals who gives to whom, after a confirmation.
    card.get_by_role("button", name="🔍 Révéler les attributions").click()
    confirm_dialog(page).get_by_role("button", name="🔍 Révéler").click()
    pairs = card.locator(".santa-pairs li")
    pairs.first.wait_for()
    assert pairs.first.inner_text().split() == ["Alice", "→", "<b>Bob</b>"]  # text, not markup
    card.get_by_role("button", name="Masquer").click()
    assert pairs.count() == 0


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


def test_a_custom_command_keeps_its_target_cooldown_and_roles(page, dashboard, monkeypatch):
    """A9: the command widget reads back what it shows, the roles as chips."""
    import extensions.customcommands._common  # noqa: F401 — registers the schema
    import src.webui.routes.config as config_routes

    key = "moduleCustomCommands"
    monkeypatch.setitem(config_routes.MODULE_SCHEMAS, key, schemas.MODULE_SCHEMAS[key])
    command = {"name": "calin", "responses": ["{auteur} → {cible}"], "target": "required"}
    config_store.mutate(
        lambda data: data["servers"][GUILD].update(
            {key: {"enabled": True, "commands": [{**command, "cooldown": 30, "roles": ["555"]}]}}
        )
    )
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/{key}")
    page.reload()  # the page read the schemas before this one was added
    entry = page.locator(".commandlist-entry")
    entry.wait_for()
    assert entry.get_by_label("Membre visé").input_value() == "required"
    assert entry.locator('[data-cmd-field="cooldown"]').input_value() == "30"
    assert entry.locator(".chip").count() == 1  # no bot: the id, typed by hand
    entry.locator('[data-cmd-field="cooldown"]').fill("45")
    entry.locator(".multi-picker-manual").fill("556")
    entry.locator(".multi-picker-manual").press("Enter")
    entry.get_by_label("Membre visé").select_option("optional")
    save(page)
    page.locator("#toasts .toast", has_text="Configuration sauvegardée").first.wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert stored["servers"][GUILD][key]["commands"] == [
        {
            **command,
            "description": "",
            "target": "optional",
            "cooldown": 45,
            "roles": ["555", "556"],
        }
    ]
    # A new command starts without a target, a cooldown or roles.
    page.get_by_role("button", name="+ Ajouter une commande").click()
    new = page.locator(".commandlist-entry").nth(1)
    assert new.get_by_label("Membre visé").input_value() == "none"
    assert new.locator(".chip").count() == 0


def test_a_streamer_keeps_its_mention_role_and_message(page, dashboard, monkeypatch):
    """A10: the streamer widget reads back the role to ping and the message."""
    import extensions.twitch._common  # noqa: F401 — registers the schema
    import src.webui.routes.config as config_routes

    key = "moduleTwitch"
    monkeypatch.setitem(config_routes.MODULE_SCHEMAS, key, schemas.MODULE_SCHEMAS[key])
    streamer = {"twitchNotificationChannelId": "70", "notifyStreamStart": True}
    config_store.mutate(
        lambda data: data["servers"][GUILD].update(
            {
                key: {
                    "enabled": True,
                    "twitchStreamerList": {"zerator": {**streamer, "twitchMentionRoleId": "555"}},
                }
            }
        )
    )
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/{key}")
    page.reload()
    entry = page.locator(".streamer-entry")
    entry.wait_for()
    assert entry.locator('[data-streamer-field="twitchMentionRoleId"]').input_value() == "555"
    entry.get_by_label("Message au début du live").fill("{streamer} est en live !")
    save(page)
    page.locator("#toasts .toast", has_text="Configuration sauvegardée").first.wait_for()
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert stored["servers"][GUILD][key]["twitchStreamerList"] == {
        "zerator": {
            **streamer,
            "twitchMentionRoleId": "555",
            "twitchNotificationTemplate": "{streamer} est en live !",
        }
    }


MEMBER_SPACE = {
    "sections": ["xp", "reminders", "tricount"],
    "unavailable": [],
    "xp": {
        "xp": 1500,
        "level": 11,
        "xpInLevel": 30,
        "xpToNext": 1255,
        "rank": 3,
        "ranked": 214,
        "season": {"label": "novembre 2026", "xp": 1240},
    },
    "reminders": {
        "limit": 25,
        "items": [
            {
                "id": "r1",
                "message": "<b>Sortir</b> le chien",
                "at": "2026-11-21T19:30:00+00:00",
                "frequency": "daily",
                "label": "Tous les jours à 20:30",
                "jumpUrl": None,
            },
        ],
    },
    "tricount": [
        {
            "id": "g1",
            "name": "Vacances",
            "balance": -2340,
            "owes": [{"to": "8", "cents": 2340}],
            "owed": [],
        }
    ],
    "currency": "€",
    "names": {"8": "Léa"},
}


@pytest.fixture
def member_page(browser, dashboard):
    """A member who manages nothing and isn't a developer, on their space."""
    session = Session(
        user_id="77",
        username="hugo",
        avatar=None,
        guilds=[{"id": GUILD, "name": "Les Copains", "permissions": "0"}],
        expires_at=time.time() + 3600,
    )
    dashboard["oauth"].sessions[session.token_hash] = session
    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": COOKIE_NAME,
                "value": session.session_token,
                "url": f"http://127.0.0.1:{dashboard['port']}",
            }
        ]
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: script_error(m) and errors.append(m.text))
    page.on("dialog", lambda d: errors.append(f"boîte native : {d.message}") or d.dismiss())
    yield page
    assert errors == [], errors
    context.close()


def test_a_member_lands_on_their_space_and_marks_a_debt_paid(member_page, dashboard):
    page = member_page
    sent: list[dict] = []
    page.route(
        f"**/api/me/servers/{GUILD}",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(MEMBER_SPACE)
        ),
    )

    def settle(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body='{"status": "ok"}')

    page.route("**/api/me/servers/*/tricount/g1/settle", settle)
    page.goto(f"http://127.0.0.1:{dashboard['port']}/")
    page.wait_for_selector("#me-xp")
    # The servers come from the real route (the session's guilds, without a bot).
    assert page.url.endswith(f"#/me/{GUILD}")
    assert page.locator(".page-header h2").inner_text() == "Salut hugo !"
    assert "3e / 214" in page.locator("#me-xp").inner_text().replace("\n", "")
    # A reminder's text is shown as text, never as markup.
    assert page.locator("#me-reminders .me-row-title").inner_text() == "<b>Sortir</b> le chien"
    assert "Tu dois 23,40 € à Léa" in page.locator("#me-tricount").inner_text()
    assert page.locator(".sidebar").get_by_text("Accueil").count() == 0
    page.get_by_role("button", name="J'ai remboursé").click()
    page.locator("#confirm-ok").click()
    page.wait_for_function("() => document.querySelectorAll('#toasts .toast').length > 0")
    assert sent == [{"to": "8", "cents": 2340}]
    assert "Remboursement enregistré." in toasts(page)


def test_a_member_cannot_reach_a_server_page(member_page, dashboard):
    page = member_page
    page.route(
        f"**/api/me/servers/{GUILD}",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(MEMBER_SPACE)
        ),
    )
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/server/{GUILD}")
    page.wait_for_selector("#me-xp")
    assert "#/me" in page.url


def test_a_member_creates_a_reminder_and_reads_the_moment_back(member_page, dashboard):
    page = member_page
    sent: list[dict] = []
    page.route(
        f"**/api/me/servers/{GUILD}",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(MEMBER_SPACE)
        ),
    )

    def create(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200, content_type="application/json", body='{"status": "ok", "id": "r2"}'
        )

    page.route(f"**/api/me/servers/{GUILD}/reminders", create)
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/me/{GUILD}")
    page.get_by_role("button", name="+ Nouveau rappel").click()
    page.locator("#me-reminder-message").fill("Appeler mamie")
    page.locator("#me-reminder-when").press_sequentially("dans 2h")
    # Read back by the real /api/me/when, as the slash command's autocomplete does.
    page.wait_for_function(
        "() => document.getElementById('me-reminder-reading').textContent.startsWith('→')"
    )
    page.locator("#me-reminder-frequency").select_option("weekly")
    page.get_by_role("button", name="Créer le rappel").click()
    page.wait_for_function("() => !document.getElementById('me-reminder-form')")
    assert sent == [{"message": "Appeler mamie", "when": "dans 2h", "frequency": "weekly"}]


def test_the_public_xp_page_shows_the_season_where_there_is_one(browser, dashboard):
    """P14: the current season's top members above the statistics; none, no card."""
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: script_error(m) and errors.append(m.text))
    meta = {"id": GUILD, "name": "Les Copains", "icon": None, "dashboards": ["xp"]}
    season = {
        "label": "T4 2026",
        "ends_at": "2026-12-31T23:00:00+00:00",
        "top": [{"rank": 1, "display_name": "<b>Camille</b>", "xp": 1200}],
    }
    answers = {"season": season}
    page.route("**/api/public/tok", lambda r: r.fulfill(json=meta))
    page.route("**/api/public/tok/xp/stats**", lambda r: r.fulfill(json={"summary": {}}))
    page.route(
        "**/api/public/tok/xp/season",
        lambda r: (
            r.fulfill(json=answers["season"])
            if answers["season"]
            else r.fulfill(status=404, json={"detail": "Pas de saison sur ce serveur."})
        ),
    )
    base = f"http://127.0.0.1:{dashboard['port']}"
    page.goto(f"{base}/public/tok/xp")
    card = page.locator("#xp-season")
    card.get_by_text("🏆 Saison T4 2026").wait_for()
    assert card.locator(".xp-top-name").inner_text() == "<b>Camille</b>"  # escaped, not markup
    answers["season"] = None
    page.reload()
    page.get_by_text("Aucune donnée").wait_for()
    assert not card.is_visible()
    assert errors == [], errors
    context.close()


def test_a_forum_field_offers_the_forums_only(page, dashboard):
    """P15: the Confrérie's forums are picked by name; a forum is never a channel to post in."""
    channels = {
        "channels": [
            {"id": "10", "name": "général", "parent_id": None, "position": 0,
             "is_thread": False, "archived": False, "is_forum": False},
            {"id": "20", "name": "textes", "parent_id": None, "position": 1,
             "is_thread": False, "archived": False, "is_forum": True},
        ],
        "categories": [],
    }  # fmt: skip
    page.route(f"**/api/servers/{GUILD}/channels", lambda r: r.fulfill(json=channels))
    page.reload()
    select = page.locator('[data-field="board"] select')
    select.locator('option[value="20"]').wait_for(state="attached")
    options = select.locator("option").all_inner_texts()
    assert "#textes" in options and "#général" not in options
    select.select_option("20")
    save(page)
    page.wait_for_timeout(500)
    stored = json.loads(dashboard["config"].read_text(encoding="utf-8"))
    assert stored["servers"][GUILD][MODULE]["board"] == "20"


def test_the_moderation_page_lists_the_reports(page, dashboard, monkeypatch):
    """P15: the reported messages, named, with links to the message and its card."""
    import src.webui.routes.config as config_routes

    class Moderation(SchemaBase):
        __label__ = "Modération"
        enabled: bool = enabled_field()

    monkeypatch.setitem(schemas._MODULE_REGISTRY, "moduleModeration", Moderation)
    monkeypatch.setitem(
        config_routes.MODULE_SCHEMAS, "moduleModeration", schemas.MODULE_SCHEMAS["moduleModeration"]
    )
    config_store.mutate(
        lambda data: data["servers"][GUILD].update(moduleModeration={"enabled": True})
    )
    report = {
        "message_id": "500", "channel_id": "10", "author_id": "7", "status": "open",
        "handled_by": None, "handled_at": None, "created_at": "2026-11-20T10:00:00+00:00",
        "reporters": [{"user_id": "8", "reason": "<script>spam</script>", "at": "2026-11-20T10:00:00+00:00"}],
        "message_url": f"https://discord.com/channels/{GUILD}/10/500", "card_url": None,
    }  # fmt: skip
    asked = []

    def answer(route):
        asked.append(route.request.url.split("?")[-1])
        route.fulfill(json={"reports": [report] if "status=open" in route.request.url else []})

    page.route(f"**/api/servers/{GUILD}/reports**", answer)
    page.route(
        f"**/api/servers/{GUILD}/infractions**", lambda r: r.fulfill(json={"infractions": []})
    )
    page.goto(f"http://127.0.0.1:{dashboard['port']}/#/module/{GUILD}/moduleModeration")
    item = page.locator("#reports-list .report-item")
    item.wait_for()
    assert "<script>spam</script>" in item.inner_text()  # a reason is text
    assert item.get_by_role("link", name="Voir le message").get_attribute("href").endswith("/500")
    page.locator("#report-filter").select_option("")
    page.locator("#reports-list", has_text="Aucun signalement").wait_for()
    assert asked == ["status=open", "status="]
