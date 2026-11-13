"""A module save can't make the bot do what its editor couldn't do by hand.

``PUT /api/servers/{id}/modules/{module}`` lets ``MANAGE_GUILD`` in, but the
bot hands out the roles a module names, pings the ones it is told to and posts
its templates with its own (administrator) permissions. What a save *adds* is
held to the Discord rule its use calls for (``schemas.ROLE_USES``).
"""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from discord_fakes import FakeGuild, FakeMember, FakeRole
from fastapi import HTTPException
from interactions import Permissions

import extensions.birthday  # noqa: F401 — registers moduleBirthday
import extensions.customcommands._common  # noqa: F401 — registers moduleCustomCommands
import extensions.lldap._common  # noqa: F401 — registers moduleLldap
import extensions.suggestions._common  # noqa: F401 — registers moduleSuggestions
import extensions.xp._common  # noqa: F401 — registers moduleXp
import extensions.zunivers._common  # noqa: F401 — registers moduleZunivers
import src.webui.routes.servers as servers_routes
from src.discord_ext.role_grants import MASS_MENTION_REFUSAL
from src.webui import schemas

GUILD = "1"
EDITOR = 7

# Roles of guild 1, below the bot's top role (position 50).
MEMBER_ROLE = FakeRole(101, "Membre", position=5)
VIP = FakeRole(102, "VIP", position=10)
STAFF = FakeRole(103, "Modo", position=20, permissions=Permissions.BAN_MEMBERS)
PINGABLE = FakeRole(104, "Annonces", position=3, mentionable=True)
EDITOR_TOP = FakeRole(105, "Gestion", position=15)
HELPER = FakeRole(106, "Helper", position=8, permissions=Permissions.KICK_MEMBERS)


class Bot:
    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild
        self.user = guild.me

    def get_guild(self, gid):
        return self.guild if int(gid) == self.guild.id else None


class Ctx:
    def __init__(self, permissions: Permissions, stored: dict | None = None, *, dev=False):
        guild = FakeGuild(int(GUILD))
        for role in (MEMBER_ROLE, VIP, STAFF, PINGABLE, EDITOR_TOP, HELPER):
            guild.add_role(role)
        guild.add_member(FakeMember(EDITOR, roles=[EDITOR_TOP], permissions=permissions))
        self.bot = Bot(guild)
        self.oauth = SimpleNamespace(is_developer=lambda session: dev)
        self.data = {"config": {}, "servers": {GUILD: stored or {}}}
        self.writes = 0

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return SimpleNamespace(username="alice", user_id=str(EDITOR))

    async def call_bot(self, func, *args, what: str, **kwargs):  # noqa: ARG002
        return await func(*args, **kwargs)

    def get_full_config(self):
        return copy.deepcopy(self.data)

    def mutate_config(self, mutator):
        data = copy.deepcopy(self.data)
        mutator(data)
        self.data = data
        self.writes += 1
        return data

    def bot_loop_alive(self):
        return False

    def module_extension_map(self, *, on_save: bool = False):  # noqa: ARG002
        return {}


def save(ctx: Ctx, module: str, config: dict):
    router = servers_routes.create_router(ctx)
    put = next(
        r.endpoint
        for r in router.routes
        if r.path == "/api/servers/{server_id}/modules/{module_name}" and "PUT" in r.methods
    )
    return asyncio.run(put(None, GUILD, module, servers_routes.ConfigUpdate(config=config)))


def refused(ctx: Ctx, module: str, config: dict) -> str:
    with pytest.raises(HTTPException) as exc:
        save(ctx, module, config)
    assert exc.value.status_code == 422
    assert ctx.writes == 0
    return exc.value.detail


MANAGER = Permissions.MANAGE_GUILD
ROLE_MANAGER = Permissions.MANAGE_GUILD | Permissions.MANAGE_ROLES


# ── roles the bot hands out ─────────────────────────────────────────────


def test_a_level_reward_needs_manage_roles():
    detail = refused(Ctx(MANAGER), "moduleXp", {"levelRewards": {"5": "101"}})
    assert "Gérer les rôles" in detail


def test_a_level_reward_below_the_editor_is_accepted():
    ctx = Ctx(ROLE_MANAGER)
    save(ctx, "moduleXp", {"levelRewards": {"5": "101", "10": "102"}})
    assert ctx.writes == 1


@pytest.mark.parametrize("role", ["103", "105", "1", "999"])
def test_staff_higher_everyone_or_unknown_rewards_are_refused(role):
    # Even the owner can't make the bot hand out a staff role automatically.
    refused(Ctx(Permissions.ADMINISTRATOR), "moduleXp", {"levelRewards": {"5": role}})


def test_the_birthday_role_is_checked_like_a_reward():
    detail = refused(Ctx(ROLE_MANAGER), "moduleBirthday", {"birthdayRoleId": "103"})
    assert "modération" in detail


def test_only_roles_the_save_adds_are_checked():
    # A reward set by someone allowed to stays editable by a co-admin.
    ctx = Ctx(MANAGER, {"moduleXp": {"levelRewards": {"5": "101"}}})
    save(ctx, "moduleXp", {"levelRewards": {"5": "101"}, "xpPinMessage": True})
    assert ctx.writes == 1


# ── roles the bot pings ─────────────────────────────────────────────────


def test_a_non_mentionable_ping_role_needs_mention_everyone():
    refused(Ctx(MANAGER), "moduleZunivers", {"journaReminderRoleId": "101"})
    ctx = Ctx(MANAGER | Permissions.MENTION_EVERYONE)
    save(ctx, "moduleZunivers", {"journaReminderRoleId": "101"})
    assert ctx.writes == 1


def test_a_mentionable_ping_role_is_fine():
    ctx = Ctx(MANAGER)
    save(ctx, "moduleZunivers", {"journaReminderRoleId": "104"})
    assert ctx.writes == 1


# ── roles that unlock something outside Discord, staff roles ───────────


def test_an_lldap_role_mapping_needs_manage_roles():
    detail = refused(Ctx(MANAGER), "moduleLldap", {"lldapRoleGroups": {"101": "3"}})
    assert "LLDAP" in detail


def test_an_lldap_mapping_may_name_a_staff_role_below_the_editor():
    # Rule 2 only: the bot gives the role nothing, only the LLDAP group.
    ctx = Ctx(ROLE_MANAGER)
    save(ctx, "moduleLldap", {"lldapRoleGroups": {"106": "3"}})
    assert ctx.writes == 1


def test_a_staff_role_is_left_to_the_dashboard_bar():
    ctx = Ctx(MANAGER)
    save(ctx, "moduleSuggestions", {"staffRoleId": "103"})
    assert ctx.writes == 1


def test_a_developer_skips_the_hierarchy_but_not_the_staff_rule():
    ctx = Ctx(Permissions.NONE, dev=True)
    save(ctx, "moduleXp", {"levelRewards": {"5": "102"}})
    assert ctx.writes == 1
    refused(Ctx(Permissions.NONE, dev=True), "moduleXp", {"levelRewards": {"5": "103"}})


# ── @everyone / @here in a template ─────────────────────────────────────


def test_a_new_mass_mention_needs_mention_everyone():
    messages = [{"text": "@everyone {mention} niveau {lvl}", "weight": 1}]
    detail = refused(Ctx(MANAGER), "moduleXp", {"levelUpMessageList": messages})
    assert detail == MASS_MENTION_REFUSAL
    ctx = Ctx(MANAGER | Permissions.MENTION_EVERYONE)
    save(ctx, "moduleXp", {"levelUpMessageList": messages})
    assert ctx.writes == 1


def test_a_custom_command_answering_here_is_refused():
    commands = [{"name": "annonce", "description": "Annonce", "response": "@here réveillez-vous"}]
    refused(Ctx(MANAGER), "moduleCustomCommands", {"commands": commands})


def test_an_existing_mass_mention_doesnt_block_other_edits():
    messages = [{"text": "@everyone {mention} niveau {lvl}", "weight": 1}]
    ctx = Ctx(MANAGER, {"moduleXp": {"levelUpMessageList": messages}})
    save(ctx, "moduleXp", {"levelUpMessageList": messages, "xpPinMessage": True})
    assert ctx.writes == 1


def test_role_refs_and_mass_mentions_read_the_schema():
    xp = schemas.module_model("moduleXp")
    lldap = schemas.module_model("moduleLldap")
    assert schemas.role_refs(xp, {"levelRewards": {"5": " 101 ", "6": ""}}) == {("grant", "101")}
    assert schemas.role_refs(lldap, {"lldapRoleGroups": {"101": "3"}}) == {("access", "101")}
    assert schemas.role_refs(xp, None) == set()
    assert schemas.mass_mention_texts({"a": ["x @here", {"b": "y"}], "c": 3}) == {"x @here"}
