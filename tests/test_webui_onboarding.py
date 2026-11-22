"""A new server's first steps (C1): packs, the checklist, the announcement."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from interactions import Permissions

import extensions.birthday  # noqa: F401 — registers moduleBirthday
import extensions.feur  # noqa: F401 — registers moduleFeur
import extensions.gazette  # noqa: F401 — registers moduleGazette
import extensions.moderation._common  # noqa: F401 — registers moduleModeration
import extensions.olympics._common  # noqa: F401 — registers moduleOlympics
import extensions.starboard._common  # noqa: F401 — registers moduleStarboard
import extensions.welcome  # noqa: F401 — registers moduleWelcome
import extensions.xp._common  # noqa: F401 — registers moduleXp
import src.webui.routes.onboarding as onboarding_routes
from src.webui import schemas
from src.webui.onboarding import (
    PACKS,
    announcement_text,
    checklist,
    missing_server_rights,
    pack_catalogue,
    pack_settings,
    post_announcement,
)

GUILD = "1"
ALL_RIGHTS = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES | Permissions.EMBED_LINKS
ALL_RIGHTS |= Permissions.ATTACH_FILES | Permissions.READ_MESSAGE_HISTORY
ALL_RIGHTS |= Permissions.MANAGE_ROLES


def module_schemas() -> dict:
    return schemas.MODULE_SCHEMAS


# ── packs ───────────────────────────────────────────────────────────────


def test_a_pack_switches_on_its_modules_with_the_channels_chosen():
    writes, skipped = pack_settings(
        PACKS["potes"],
        {},
        {
            "moduleWelcome": {"welcomeChannelId": "11"},
            "moduleStarboard": {"starboardChannelId": "12"},
            "moduleGazette": {"gazetteChannelId": "13"},
        },
        module_schemas(),
    )
    assert writes["moduleWelcome"] == {"enabled": True, "welcomeChannelId": "11"}
    assert writes["moduleXp"] == {"enabled": True}
    assert writes["moduleStarboard"]["starboardChannelId"] == "12"
    for module, settings in writes.items():
        model = schemas.module_model(module)
        assert schemas.validation_errors(model, settings, module=True) == []
    assert all(
        s["reason"] == "module indisponible sur ce bot"
        for s in skipped
        if s["module"] not in writes
    )


def test_a_pack_never_touches_a_module_the_server_already_has():
    server = {"moduleXp": {"enabled": False, "xpChannelId": "5"}}
    writes, skipped = pack_settings(
        PACKS["potes"], server, {"moduleWelcome": {"welcomeChannelId": "11"}}, module_schemas()
    )
    assert "moduleXp" not in writes
    assert {"module": "moduleXp", "reason": "déjà réglé : laissé tel quel"} in skipped


def test_a_module_missing_a_required_channel_is_left_aside():
    writes, skipped = pack_settings(PACKS["potes"], {}, {}, module_schemas())
    assert "moduleWelcome" not in writes
    reason = next(s["reason"] for s in skipped if s["module"] == "moduleWelcome")
    assert reason == "il manque : Salon de bienvenue"
    assert "moduleBirthday" in writes  # its channel is optional


def test_only_the_fields_a_pack_asks_are_taken_from_the_answers():
    writes, _ = pack_settings(
        PACKS["potes"],
        {},
        {"moduleWelcome": {"welcomeChannelId": "11", "welcomeImageEnabled": False}},
        module_schemas(),
    )
    assert "welcomeImageEnabled" not in writes["moduleWelcome"]


def test_the_catalogue_lists_loaded_modules_and_their_pickers():
    packs = {p["key"]: p for p in pack_catalogue(module_schemas())}
    assert set(packs) == {"potes", "stream", "esport", "sur-mesure"}
    asks = {(a["module"], a["field"]) for a in packs["potes"]["asks"]}
    assert ("moduleWelcome", "welcomeChannelId") in asks
    welcome = next(a for a in packs["potes"]["asks"] if a["module"] == "moduleWelcome")
    assert welcome["meta"]["type"] == "channel"
    assert packs["sur-mesure"]["modules"] == []


def test_no_pack_switches_on_an_archived_module():
    archived = {k for k, s in module_schemas().items() if s.get("archived")}
    assert "moduleOlympics" in archived
    for pack in PACKS.values():
        assert not archived & set(pack.modules)


# ── checklist ───────────────────────────────────────────────────────────


def steps(server, **facts):
    facts = {"missing_rights": [], "welcome_tested": False, "announced": False, **facts}
    return {s["key"]: s for s in checklist(server, **facts)}


def test_a_fresh_server_has_everything_to_do_but_the_rights():
    done = {k for k, s in steps({}).items() if s["done"]}
    assert done == {"rights"}


def test_each_step_is_read_from_the_server():
    server = {
        "moduleWelcome": {"enabled": True, "welcomeChannelId": "11"},
        "moduleModeration": {"enabled": True, "modLogChannelId": "12", "staffRoleId": "7"},
    }
    result = steps(server, welcome_tested=True, announced=True)
    assert all(s["done"] for s in result.values())


def test_a_welcome_switched_off_is_not_done():
    assert not steps({"moduleWelcome": {"enabled": False, "welcomeChannelId": "11"}})["welcome"][
        "done"
    ]


def test_missing_rights_are_named_and_an_offline_bot_is_said():
    lacking = steps({}, missing_rights=["Gérer les rôles"])["rights"]
    assert not lacking["done"] and "Gérer les rôles" in lacking["hint"]
    offline = steps({}, missing_rights=None)["rights"]
    assert not offline["done"] and "hors ligne" in offline["hint"]


def bot_with(permissions):
    me = SimpleNamespace(guild_permissions=permissions)
    return SimpleNamespace(get_guild=lambda gid: SimpleNamespace(id=gid, me=me))


def test_the_rights_michel_lacks_on_the_server():
    assert missing_server_rights(bot_with(ALL_RIGHTS), GUILD) == []
    assert missing_server_rights(bot_with(Permissions.ADMINISTRATOR), GUILD) == []
    assert missing_server_rights(bot_with(ALL_RIGHTS & ~Permissions.MANAGE_ROLES), GUILD) == [
        "Gérer les rôles"
    ]
    assert missing_server_rights(SimpleNamespace(get_guild=lambda gid: None), GUILD) is None


# ── announcement ────────────────────────────────────────────────────────


def test_the_announcement_lists_the_modules_switched_on():
    text = announcement_text(
        {"moduleXp": {"enabled": True}, "moduleFeur": {"enabled": False}}, module_schemas()
    )
    assert "**Système d'XP**" in text and "Feur" not in text
    assert text.endswith("Tape `/aide` pour voir mes commandes, module par module.")


class FakeChannel:
    def __init__(self, cid, guild_id, *, member_rights, bot_rights):
        self.id = cid
        self._guild_id = guild_id
        self.name = "général"
        self.rights = {"member": member_rights, "bot": bot_rights}
        self.sent = []

    def permissions_for(self, who):
        return self.rights[who.kind]

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=99)


class FakeAnnounceBot:
    def __init__(self, channel):
        self.channel = channel
        member = SimpleNamespace(kind="member")
        self.guild = SimpleNamespace(
            id=int(GUILD), me=SimpleNamespace(kind="bot"), fetch_member=self._member(member)
        )

    @staticmethod
    def _member(member):
        async def fetch(_uid):
            return member

        return fetch

    def get_guild(self, _gid):
        return self.guild

    async def fetch_channel(self, cid):
        return self.channel if cid == self.channel.id else None


def announce(channel, *, is_developer=False):
    bot = FakeAnnounceBot(channel)
    return asyncio.run(
        post_announcement(bot, GUILD, str(channel.id), "9", "Salut", is_developer=is_developer)
    )


def test_michel_announces_where_the_editor_can_write():
    channel = FakeChannel(11, GUILD, member_rights=ALL_RIGHTS, bot_rights=ALL_RIGHTS)
    assert announce(channel) == f"https://discord.com/channels/{GUILD}/11/99"
    (sent,) = channel.sent
    assert sent["embeds"][0].description == "Salut"


def test_an_editor_who_cant_write_there_is_refused():
    channel = FakeChannel(11, GUILD, member_rights=Permissions.VIEW_CHANNEL, bot_rights=ALL_RIGHTS)
    with pytest.raises(HTTPException) as exc:
        announce(channel)
    assert exc.value.status_code == 403 and "Envoyer des messages" in exc.value.detail
    assert channel.sent == []


def test_a_channel_michel_cant_write_in_is_said():
    channel = FakeChannel(11, GUILD, member_rights=ALL_RIGHTS, bot_rights=Permissions.VIEW_CHANNEL)
    with pytest.raises(HTTPException) as exc:
        announce(channel)
    assert exc.value.status_code == 409 and channel.sent == []


def test_another_guilds_channel_is_refused():
    channel = FakeChannel(11, "2", member_rights=ALL_RIGHTS, bot_rights=ALL_RIGHTS)
    with pytest.raises(HTTPException) as exc:
        announce(channel, is_developer=True)
    assert exc.value.status_code == 422 and channel.sent == []


# ── routes ──────────────────────────────────────────────────────────────


class FakeBot:
    """Channels 1x belong to guild 1, 2x to guild 2."""

    async def fetch_channel(self, cid):
        owner = str(cid)[0]
        if len(str(cid)) != 2 or owner not in "12":
            raise LookupError("Unknown Channel")
        return SimpleNamespace(id=int(cid), _guild_id=owner)


class FakeCtx:
    def __init__(self, stored: dict | None = None) -> None:
        self.bot = FakeBot()
        self.data = {"config": {}, "servers": {GUILD: stored or {}}}
        self.oauth = SimpleNamespace(is_developer=lambda _session: False)

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return SimpleNamespace(username="admin", user_id="9")

    async def call_bot(self, func, *args, what: str, **kwargs):  # noqa: ARG002
        return await func(*args, **kwargs)

    def get_full_config(self):
        return copy.deepcopy(self.data)

    def mutate_config(self, mutator):
        data = copy.deepcopy(self.data)
        mutator(data)
        self.data = data
        return data

    def bot_loop_alive(self):
        return False

    def module_extension_map(self, *, on_save: bool = False):  # noqa: ARG002
        return {}


def endpoint(ctx, path):
    router = onboarding_routes.create_router(ctx)
    return next(r.endpoint for r in router.routes if r.path == path)


def apply(ctx, pack, answers):
    post = endpoint(ctx, "/api/servers/{server_id}/onboarding/pack")
    body = onboarding_routes.PackBody(pack=pack, answers=answers)
    return json.loads(asyncio.run(post(None, GUILD, body)).body)


def test_applying_a_pack_writes_the_new_modules_only():
    ctx = FakeCtx({"moduleFeur": {"enabled": False}})
    result = apply(ctx, "potes", {"moduleWelcome": {"welcomeChannelId": "11"}})
    server = ctx.data["servers"][GUILD]
    assert server["moduleFeur"] == {"enabled": False}
    assert server["moduleWelcome"] == {"enabled": True, "welcomeChannelId": "11"}
    assert "moduleWelcome" in result["applied"] and "moduleFeur" not in result["applied"]
    assert "moduleStarboard" not in server  # no channel chosen


def test_a_pack_with_another_guilds_channel_writes_nothing():
    ctx = FakeCtx()
    with pytest.raises(HTTPException) as exc:
        apply(ctx, "potes", {"moduleWelcome": {"welcomeChannelId": "21"}})
    assert exc.value.status_code == 422
    assert ctx.data["servers"][GUILD] == {}


def test_an_unknown_pack_is_a_404():
    with pytest.raises(HTTPException) as exc:
        apply(FakeCtx(), "inconnu", {})
    assert exc.value.status_code == 404


def test_hiding_the_list_is_stored_and_undone():
    ctx = FakeCtx()
    dismiss = endpoint(ctx, "/api/servers/{server_id}/onboarding/dismiss")
    asyncio.run(dismiss(None, GUILD, onboarding_routes.DismissBody(dismissed=True)))
    assert ctx.data["servers"][GUILD]["onboardingDismissed"] is True
    asyncio.run(dismiss(None, GUILD, onboarding_routes.DismissBody(dismissed=False)))
    assert "onboardingDismissed" not in ctx.data["servers"][GUILD]


def test_the_dismissed_flag_is_not_a_module():
    data = {"servers": {GUILD: {"onboardingDismissed": True}}}
    assert schemas.config_problems(data) == []


def test_the_onboarding_view_without_a_bot_or_database():
    ctx = FakeCtx({"onboardingDismissed": True})
    ctx.bot = None
    get = endpoint(ctx, "/api/servers/{server_id}/onboarding")
    data = json.loads(asyncio.run(get(None, GUILD)).body)
    assert data["dismissed"] is True
    assert [s["key"] for s in data["steps"]][0] == "rights"
    assert {p["key"] for p in data["packs"]} >= {"potes", "sur-mesure"}


def test_the_overview_without_a_database_shows_nothing_rather_than_failing():
    import src.webui.routes.overview as overview_routes

    ctx = FakeCtx({"moduleXp": {"enabled": True}})
    router = overview_routes.create_router(ctx)
    get = next(r.endpoint for r in router.routes if r.path.endswith("/overview"))
    data = json.loads(asyncio.run(get(None, GUILD)).body)
    assert data == {"activity": None, "recent": None, "lastChanges": None, "lastRuns": {}}
