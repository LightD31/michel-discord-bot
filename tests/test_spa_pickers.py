"""The dashboard without raw ids, in a real browser, against the demo's fake bot (P3).

Roles, channels and members are picked and shown by name: the role picker
says before the save why a role would be refused, a list of channels or
roles is a row of chips, an infraction names its member. No MongoDB here:
the fake bot of ``tools/dashboard_demo.py`` answers for Discord, and the
reads that need a database are answered by ``page.route``.
"""

import asyncio
import json
import os
import socket
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from test_spa_smoke import script_error  # noqa: E402

import extensions.moderation._common  # noqa: E402, F401 — registers moduleModeration
import extensions.xp._common  # noqa: E402, F401 — registers moduleXp
from features.userinfo.repository import UserInfoRepository  # noqa: E402
from src.core import config as core_config  # noqa: E402
from src.core.config import config_store  # noqa: E402
from src.core.errors import DatabaseError  # noqa: E402
from src.webui.auth import Session  # noqa: E402
from src.webui.context import COOKIE_NAME  # noqa: E402
from tools.dashboard_demo import ADMIN, CH, GA, FakeBot, _roles, demo_config  # noqa: E402

pytestmark = pytest.mark.browser

ROLE = {name: rid for name, (rid, *_rest) in _roles().items()}


class Moderation:
    MODULE_KEYS = ("moduleModeration",)
    extension_name = "extensions.moderation"


class Xp:
    MODULE_KEYS = ("moduleXp",)
    extension_name = "extensions.xp"


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
    config = demo_config(str(tmp_path), port, "")
    config["config"]["webui"]["developerUserIds"] = []
    del config["config"]["mongodb"]
    config["servers"] = {
        GA: {k: config["servers"][GA][k] for k in ("moduleModeration", "moduleXp")}
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(path))
    config_store._data = None

    # The members come from the bot's cache alone: no "users" collection here.
    async def no_stored_members(self):
        raise DatabaseError("pas de MongoDB dans ce test")

    monkeypatch.setattr(UserInfoRepository, "all_members", no_stored_members)
    bot = FakeBot()
    bot.ext = {"Moderation": Moderation(), "Xp": Xp()}
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    app = create_app(bot=bot, bot_loop=loop)
    session = Session(
        user_id=ADMIN,
        username="camille.b",
        avatar=None,
        guilds=[{"id": GA, "name": "Les Copains d'abord", "permissions": str(0x8)}],
        expires_at=time.time() + 3600,
    )
    app.state.webui.oauth.sessions[session.token_hash] = session
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"base": f"http://127.0.0.1:{port}", "session": session, "config": path}
    server.should_exit = True
    thread.join(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    config_store._data = None


@pytest.fixture
def page(browser, dashboard):
    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": COOKIE_NAME,
                "value": dashboard["session"].session_token,
                "url": dashboard["base"],
            }
        ]
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: script_error(m) and errors.append(m.text))
    page.on("dialog", lambda d: errors.append(f"boîte native : {d.message}") or d.dismiss())
    # What MongoDB would answer: no case, no message, no audit entry.
    page.route(
        "**/api/servers/*/infractions**", lambda route: route.fulfill(json={"infractions": []})
    )
    page.route(
        "**/api/servers/*/xp/stats**", lambda route: route.fulfill(status=503, json={"detail": "x"})
    )
    yield page
    assert errors == [], errors
    context.close()


def open_module(page, dashboard, module):
    page.goto(f"{dashboard['base']}/#/module/{GA}/{module}")
    page.wait_for_selector(".schema-form")
    page.wait_for_function(
        "() => ![...document.querySelectorAll('.schema-form select')]"
        ".some(s => s.textContent.includes('Chargement'))"
    )


def stored(dashboard, module):
    return json.loads(dashboard["config"].read_text(encoding="utf-8"))["servers"][GA][module]


def test_roles_and_channels_show_by_name_never_by_id(page, dashboard):
    open_module(page, dashboard, "moduleModeration")
    staff = page.get_by_label("Rôle staff")
    assert staff.locator("option:checked").inner_text() == "Modérateur"
    channels = page.locator('.multi-picker[data-field="ignoredChannelIds"]')
    roles = page.locator('.multi-picker[data-field="ignoredRoleIds"]')
    assert channels.locator(".chip-label").all_inner_texts() == ["#jeux"]
    assert roles.locator(".chip-label").all_inner_texts() == ["Modérateur"]
    form = page.locator(".schema-form").inner_text()
    assert ROLE["Modérateur"] not in form and CH["jeux"] not in form


def test_a_list_of_channels_and_roles_is_edited_with_chips(page, dashboard):
    open_module(page, dashboard, "moduleModeration")
    channels = page.locator('.multi-picker[data-field="ignoredChannelIds"]')
    channels.get_by_label("Ajouter un salon").select_option(label="#général")
    assert channels.locator(".chip-label").all_inner_texts() == ["#jeux", "#général"]
    # A channel already in the list is no longer offered.
    assert (
        channels.get_by_label("Ajouter un salon").locator("option", has_text="#jeux").count() == 0
    )
    roles = page.locator('.multi-picker[data-field="ignoredRoleIds"]')
    roles.get_by_role("button", name="Retirer Modérateur").click()
    assert roles.locator(".chip").count() == 0
    assert page.locator(".unsaved-badge").is_visible()
    page.get_by_role("button", name="💾 Sauvegarder").click()
    page.locator("#toasts .toast", has_text="sauvegardée").first.wait_for()
    saved = stored(dashboard, "moduleModeration")
    assert saved["ignoredChannelIds"] == [CH["jeux"], CH["général"]]
    assert "ignoredRoleIds" not in saved
    assert saved["staffRoleId"] == ROLE["Modérateur"]


def test_a_role_the_save_would_refuse_says_why_before_the_save(page, dashboard):
    open_module(page, dashboard, "moduleXp")
    first = page.locator(".keyvaluemap-entry").first
    picker = first.locator(".role-picker")
    assert picker.locator("option:checked").inner_text() == "Niveau 5"
    assert picker.locator(".picker-warning").inner_text() == ""
    # Above the bot's highest role: locked in the list, and the reason shows.
    assert "🔒" in picker.locator("option", has_text="Fondateurs").inner_text()
    picker.locator("select").select_option(value=ROLE["Fondateurs"])
    warning = picker.locator(".picker-warning").inner_text()
    assert "« Fondateurs »" in warning and "au-dessus" in warning
    assert ROLE["Fondateurs"] not in warning
    page.get_by_role("button", name="💾 Sauvegarder").click()
    error = page.locator("#toasts .toast.error", has_text="Fondateurs").first
    error.wait_for()
    assert "« Fondateurs »" in error.inner_text() and "<@&" not in error.inner_text()
    assert stored(dashboard, "moduleXp")["levelRewards"]["5"] == ROLE["Niveau 5"]


def test_a_new_level_reward_picks_its_role(page, dashboard):
    open_module(page, dashboard, "moduleXp")
    page.get_by_role("button", name="+ Ajouter une entrée").click()
    row = page.locator(".keyvaluemap-entry").last
    row.locator('input[data-kvmap-field="key"]').fill("30")
    row.locator(".role-picker select").select_option(label="Gamers")
    page.get_by_role("button", name="💾 Sauvegarder").click()
    page.locator("#toasts .toast", has_text="sauvegardée").first.wait_for()
    assert stored(dashboard, "moduleXp")["levelRewards"]["30"] == ROLE["Gamers"]


def test_an_infraction_names_its_member_and_opens_their_history(page, dashboard):
    hugo = "200000000000000044"
    asked: list[str] = []

    def answer(route):
        asked.append(route.request.url)
        case = {
            "id": 7,
            "type": "warn",
            "user_id": hugo,
            "moderator_id": ADMIN,
            "reason": "Spam",
            "active": True,
            "created_at": "2026-11-10T10:00:00+00:00",
        }
        route.fulfill(json={"infractions": [case]})

    page.unroute("**/infractions**")
    page.route("**/api/servers/*/infractions**", answer)
    page.goto(f"{dashboard['base']}/#/module/{GA}/moduleModeration")
    case = page.locator("#infractions-list > div").first
    case.get_by_text("Spam").wait_for()
    text = case.inner_text()
    assert "Hugo" in text and "Par Camille" in text and hugo not in text
    case.get_by_role("link", name="Hugo").click()
    page.wait_for_function(f"() => document.getElementById('inf-filter-user').value === '{hugo}'")
    page.wait_for_timeout(200)
    assert f"user_id={hugo}" in asked[-1]


def test_a_switch_asks_in_the_apps_own_dialog(page, dashboard):
    open_module(page, dashboard, "moduleXp")
    page.locator("label.toggle", has=page.get_by_label("Activer le module Système d'XP")).click()
    dialog = page.get_by_role("alertdialog")
    dialog.wait_for()
    assert "Désactiver le module Système d'XP" in dialog.inner_text()
    dialog.get_by_role("button", name="Annuler").click()
    dialog.wait_for(state="hidden")
    assert page.get_by_label("Activer le module Système d'XP").is_checked()
    assert stored(dashboard, "moduleXp")["enabled"] is True


def test_a_long_page_has_an_index_of_its_sections(page, dashboard):
    page.route("**/api/servers/*/bot-messages", lambda route: route.fulfill(json={"messages": []}))
    page.route("**/api/servers/*/audit**", lambda route: route.fulfill(json={"entries": []}))
    page.set_viewport_size({"width": 390, "height": 700})
    page.goto(f"{dashboard['base']}/#/server/{GA}")
    index = page.get_by_role("navigation", name="Sur cette page")
    index.wait_for()
    names = index.get_by_role("button").all_inner_texts()
    assert "Journal des modifications" in names and len(names) >= 3
    index.get_by_role("button", name="Journal des modifications").click()
    page.wait_for_timeout(800)
    box = page.locator(".card", has_text="Journal des modifications").first.bounding_box()
    assert box is not None and 0 <= box["y"] < 700
    assert index.bounding_box()["y"] < 120  # still on screen, under the header


def test_the_login_page_says_what_michel_is(browser, dashboard):
    context = browser.new_context()
    page = context.new_page()
    page.goto(dashboard["base"])
    card = page.locator(".login-card")
    card.get_by_role("link", name="Se connecter avec Discord").wait_for()
    text = card.inner_text()
    assert "Gérer le serveur" in text and "/aide" in text
    context.close()
