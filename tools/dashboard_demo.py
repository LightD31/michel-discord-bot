"""The real dashboard on a fake bot and a seeded MongoDB: a demo, and its screenshots.

A development tool, kept out of the Docker image (``.dockerignore`` is an
allowlist that doesn't name ``tools/``). It runs the dashboard exactly as the
bot does, with ``create_app()``, against:

* a fake bot: two servers (one set up, one freshly invited), channels in
  categories, coloured roles, 30 members, and a stub of every extension with
  its ``Task`` s, so module pages, pickers and the health card show real data;
* a MongoDB seeded with 60 days of XP, infractions, the audit log, bot message
  slots and an open Secret Santa session;
* three sessions: a developer, a server admin and a plain member (and the
  login page, for no session at all).

Usage, from the repository root (MongoDB from ``docker run -p 27017:27017 mongo:8``)::

    uv run python tools/dashboard_demo.py --mongo mongodb://localhost:27017
    uv run python tools/dashboard_demo.py --mongo mongodb://localhost:27017 --shots out/

The first prints a login link per session and serves until Ctrl+C. With
``--shots``, it captures every page of every session, desktop and phone,
into the folder, then exits (``--only xp`` keeps the pages whose name holds
``xp``). The demo writes only to its own databases (``demo_global`` and two
``guild_…`` databases it marks as its own), and refuses to touch a guild
database it didn't create.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import importlib
import json
import os
import random
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GA = "1100000000000000001"  # a server that is set up
GB = "1100000000000000002"  # a server Michel just joined
DEV = "200000000000000042"
ADMIN = "200000000000000043"
MEMBER = "200000000000000044"
BOT_ID = "300000000000000001"
DEMO_GLOBAL_DB = "demo_global"
DEMO_MARKER = "dashboard_demo"  # a collection in each database the demo owns
SESSIONS = {"developpeur": DEV, "admin": ADMIN, "membre": MEMBER}

CH = {  # channel name -> id in server A
    "annonces": "1200000000000000010",
    "général": "1200000000000000011",
    "bienvenue": "1200000000000000012",
    "règles": "1200000000000000013",
    "suggestions": "1200000000000000014",
    "starboard": "1200000000000000015",
    "anniversaires": "1200000000000000016",
    "musique": "1200000000000000017",
    "veille-tech": "1200000000000000018",
    "streams": "1200000000000000019",
    "mod-logs": "1200000000000000020",
    "classement": "1200000000000000021",
    "rôles": "1200000000000000022",
    "jeux": "1200000000000000023",
    "textes": "1200000000000000024",  # forums (the Confrérie's, P15)
    "défis": "1200000000000000025",
}

FIRST_NAMES = [
    "Camille", "Léa", "Hugo", "Inès", "Lucas", "Chloé", "Nathan", "Manon", "Théo", "Jade",
    "Louis", "Emma", "Arthur", "Zoé", "Jules", "Lina", "Gabriel", "Rose", "Maël", "Alice",
    "Noah", "Anna", "Tom", "Lou", "Sacha", "Eva", "Paul", "Nina", "Adam", "Mila",
]  # fmt: skip


def _roles() -> dict[str, tuple[str, int, int, int]]:
    """Server A's roles: name -> (id, position, permissions, colour)."""
    from interactions import Permissions

    moderator = (
        Permissions.MODERATE_MEMBERS | Permissions.MANAGE_MESSAGES | Permissions.KICK_MEMBERS
    )
    return {
        "@everyone": (GA, 0, 0x400 | 0x800, 0),
        "Membre": ("1300000000000000001", 1, 0, 0x95A5A6),
        "Niveau 5": ("1300000000000000002", 2, 0, 0x3498DB),
        "Niveau 10": ("1300000000000000003", 3, 0, 0x9B59B6),
        "Niveau 20": ("1300000000000000004", 4, 0, 0xE67E22),
        "Anniversaire 🎂": ("1300000000000000005", 5, 0, 0xF1C40F),
        "Notifs streams": ("1300000000000000006", 6, 0, 0xE91E63),
        "Gamers": ("1300000000000000007", 7, 0, 0x2ECC71),
        "Lecteurs": ("1300000000000000008", 8, 0, 0x1ABC9C),
        "Modérateur": ("1300000000000000009", 9, int(moderator), 0xE74C3C),
        "Michel": ("1300000000000000010", 10, int(Permissions.ADMINISTRATOR), 0x5865F2),
        "Admin": ("1300000000000000011", 11, int(Permissions.ADMINISTRATOR), 0xC0392B),
        "Fondateurs": ("1300000000000000012", 12, 0, 0xF39C12),
    }


def demo_config(data_dir: str, port: int, mongo_url: str) -> dict[str, Any]:
    """The ``config.json`` the demo runs on."""
    role = {name: rid for name, (rid, *_rest) in _roles().items()}
    return {
        "config": {
            "discord": {"botToken": "demo", "devGuildId": GA, "ownerId": DEV},
            "mongodb": {"url": mongo_url},
            "misc": {"dataFolder": data_dir},
            "webui": {
                "enabled": True,
                "clientId": "1",
                "clientSecret": "demo",  # pragma: allowlist secret
                "baseUrl": f"http://127.0.0.1:{port}",
                "developerUserIds": [DEV],
            },
        },
        "servers": {
            GA: {
                "moduleXp": {
                    "enabled": True,
                    "xpChannelId": CH["classement"],
                    "xpPinMessage": True,
                    "publicDashboard": True,
                    "voiceXpEnabled": True,
                    "seasonsEnabled": True,
                    "seasonMonths": 1,
                    "levelRewards": {
                        "5": role["Niveau 5"],
                        "10": role["Niveau 10"],
                        "20": role["Niveau 20"],
                    },
                    "levelUpMessageList": [
                        {"text": "Bravo {mention}, tu passes niveau {lvl} ! 🎉", "weight": 3},
                        {"text": "{mention} monte en puissance : niveau {lvl} 💪", "weight": 1},
                    ],
                },
                "moduleBirthday": {
                    "enabled": True,
                    "birthdayChannelId": CH["anniversaires"],
                    "birthdayRoleId": role["Anniversaire 🎂"],
                    "birthdayMessageList": [
                        {"text": "Joyeux anniversaire {mention} ! 🎉 ({age} ans)", "weight": 1}
                    ],
                },
                "moduleWelcome": {
                    "enabled": True,
                    "welcomeChannelId": CH["bienvenue"],
                    "welcomeImageEnabled": True,
                    "welcomeMessageList": [
                        {"text": f"Bienvenue {{mention}} ! Passe par <#{CH['règles']}> 👋"}
                    ],
                    "welcomeArrivalRoleId": role["Membre"],
                    "welcomeRulesChannelId": CH["règles"],
                    "welcomeRolesChannelId": CH["rôles"],
                    "leaveMessageList": [{"text": "{username} nous a quittés."}],
                },
                "moduleStarboard": {
                    "enabled": True,
                    "starboardChannelId": CH["starboard"],
                    "threshold": 4,
                    "ignoredChannels": [CH["mod-logs"]],
                },
                "moduleSuggestions": {
                    "enabled": True,
                    "suggestChannelId": CH["suggestions"],
                    "staffRoleId": role["Modérateur"],
                },
                "moduleConfrerie": {
                    "enabled": False,
                    "confrerieTextesForumId": CH["textes"],
                    "confrerieDefisForumId": CH["défis"],
                },
                "moduleModeration": {
                    "enabled": True,
                    "modLogChannelId": CH["mod-logs"],
                    "staffRoleId": role["Modérateur"],
                    "dmOnAction": True,
                    "antiInvite": True,
                    "antiSpam": True,
                    "bannedWords": ["spoil"],
                    "ignoredChannelIds": [CH["jeux"]],
                    "ignoredRoleIds": [role["Modérateur"]],
                },
                "moduleGiveaway": {"enabled": True, "giveawayEmoji": "🎉"},
                "moduleAchievements": {"enabled": True, "achievementsChannelId": CH["général"]},
                "moduleAgenda": {"enabled": True, "agendaChannelId": CH["annonces"]},
                "moduleIA": {"enabled": True, "catchupChannelIds": [CH["général"], CH["jeux"]]},
                "moduleGazette": {
                    "enabled": True,
                    "gazetteChannelId": CH["général"],
                    "gazetteWeekday": 7,
                    "gazetteHour": 18,
                },
                "moduleUtils": {"enabled": True, "pollDefaultDurationMinutes": 1440},
                "moduleFeur": {"enabled": True},
                "moduleRss": {
                    "enabled": True,
                    "ChannelId": CH["veille-tech"],
                    "rssPollMinutes": 15,
                    "rssFeeds": {
                        "korben": {"url": "https://korben.info/feed"},
                        "pixels": {
                            "url": "https://www.lemonde.fr/pixels/rss_full.xml",
                        },
                    },
                },
                "moduleCustomCommands": {
                    "enabled": True,
                    "commands": [
                        {
                            "name": "cafe",
                            "description": "Un café pour la route",
                            "responses": ["☕ Et un café, un !", "☕☕ Double ristretto."],
                        },
                        {
                            "name": "calin",
                            "description": "Un câlin pour quelqu'un",
                            "responses": ["{auteur} fait un gros câlin à {cible} 🤗"],
                            "target": "required",
                            "cooldown": 30,
                            "roles": [role["Membre"]],
                        },
                    ],
                },
                "moduleReactionRoles": {"enabled": True},
                "moduleTricount": {"enabled": True, "tricountCurrency": "€"},
                "moduleSecretSanta": {"enabled": True},
                "moduleTwitch": {
                    "enabled": False,
                    "twitchStreamerList": {
                        "zerator": {
                            "twitchNotificationChannelId": CH["général"],
                            "notifyStreamStart": True,
                            "twitchMentionRoleId": role["Notifs streams"],
                            "twitchNotificationTemplate": "{streamer} est en live : {titre} !",
                        }
                    },
                },
                "moduleYoutube": {
                    "enabled": False,
                    "ChannelId": CH["général"],
                    "youtubeChannelList": {
                        "@micode": {"vod": True, "mentionRoleId": role["Notifs streams"]}
                    },
                },
                "discord2name": {MEMBER: "Hugo"},
                "publicDashboardToken": PUBLIC_TOKEN,
            },
            GB: {},
        },
    }


# ── The fake bot ─────────────────────────────────────────────────────


class FakeRole:
    def __init__(self, guild_id: str, name: str, rid: str, position: int, perms: int, color: int):
        from interactions import Permissions

        self.id = int(rid)
        self.name = name
        self.position = position
        self.permissions = Permissions(perms)
        self.color = color
        self.managed = name == "Michel"
        self.bot_managed = name == "Michel"
        self.mentionable = name in {"Notifs streams", "Gamers"}
        self._guild_id = int(guild_id)


class FakeMember:
    def __init__(self, guild, uid, username, display, roles, *, bot=False):
        self.guild = guild
        self.id = int(uid)
        self.username = username
        self.display_name = display
        self.bot = bot
        self.roles = roles

    @property
    def top_role(self):
        return max(self.roles, key=lambda r: r.position) if self.roles else self.guild.roles[0]

    @property
    def guild_permissions(self):
        from interactions import Permissions

        perms = 0
        for r in [self.guild.roles[0], *self.roles]:
            perms |= int(r.permissions)
        if perms & int(Permissions.ADMINISTRATOR):
            return Permissions.ALL
        return Permissions(perms)

    def has_permission(self, *permissions):
        mine = int(self.guild_permissions)
        return all((mine & int(p)) == int(p) for p in permissions)

    @property
    def mention(self):
        return f"<@{self.id}>"


class FakeChannel:
    def __init__(self, guild, cid, name, ctype, position=0, parent=None):
        self.id = int(cid)
        self.name = name
        self.type = ctype
        self.position = position
        self.parent_id = int(parent) if parent else None
        self.category_id = self.parent_id
        self._guild_id = guild.id
        self.guild = guild
        self.nsfw = False
        self.archived = False

    def permissions_for(self, member):
        from interactions import Permissions

        if self.name == "streams":  # a channel the bot can't post in
            return Permissions.VIEW_CHANNEL | Permissions.READ_MESSAGE_HISTORY
        return Permissions.ALL


class FakeGuild:
    def __init__(self, gid, name, member_count):
        self.id = int(gid)
        self.name = name
        self.icon = None
        self.member_count = member_count
        self.roles: list[FakeRole] = []
        self.channels: list[FakeChannel] = []
        self.members: list[FakeMember] = []
        self.owner_id = int(ADMIN)
        self.me: FakeMember | None = None
        self.system_channel: FakeChannel | None = None

    def is_owner(self, uid):
        return int(uid) == self.owner_id

    def get_member(self, uid):
        return next((m for m in self.members if m.id == int(uid)), None)

    async def list_scheduled_events(self, with_user_count=False):
        # The agenda's Discord events (P19): one under way, two to come.
        from src.core import clock

        today = clock.to_local(clock.now()).date()
        return [
            SimpleNamespace(
                id=1500000000000000001,
                name="Soirée jeux de société",
                status=1,
                start_time=clock.at_local(today + timedelta(days=2), 20, 30),
            ),
            SimpleNamespace(
                id=1500000000000000002,
                name="Tournoi Mario Kart",
                status=1,
                start_time=clock.at_local(today + timedelta(days=6), 21),
            ),
            SimpleNamespace(
                id=1500000000000000003,
                name="Stream de Lucas",
                status=2,
                start_time=clock.now() - timedelta(hours=1),
            ),
        ]

    async def fetch_member(self, uid):
        return self.get_member(uid)

    async def fetch_channels(self):
        return list(self.channels)

    def get_channel(self, cid):
        return next((c for c in self.channels if c.id == int(cid)), None)

    async def fetch_channel(self, cid):
        return self.get_channel(cid)

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == int(rid)), None)

    async def fetch_role(self, rid):
        return self.get_role(rid)


def _server_a() -> FakeGuild:
    from interactions import ChannelType

    g = FakeGuild(GA, "Les Copains d'abord", 214)
    g.roles = [FakeRole(GA, n, *v) for n, v in _roles().items()]
    role = {r.name: r for r in g.roles}
    info = FakeChannel(g, "1200000000000000001", "📢 INFOS", ChannelType.GUILD_CATEGORY, 0)
    community = FakeChannel(
        g, "1200000000000000002", "💬 COMMUNAUTÉ", ChannelType.GUILD_CATEGORY, 1
    )
    staff = FakeChannel(g, "1200000000000000003", "🛡️ STAFF", ChannelType.GUILD_CATEGORY, 2)
    g.channels = [info, community, staff]
    layout = [
        ("annonces", info, ChannelType.GUILD_NEWS),
        ("règles", info, ChannelType.GUILD_TEXT),
        ("bienvenue", info, ChannelType.GUILD_TEXT),
        ("rôles", info, ChannelType.GUILD_TEXT),
        ("général", community, ChannelType.GUILD_TEXT),
        ("suggestions", community, ChannelType.GUILD_TEXT),
        ("starboard", community, ChannelType.GUILD_TEXT),
        ("anniversaires", community, ChannelType.GUILD_TEXT),
        ("musique", community, ChannelType.GUILD_TEXT),
        ("veille-tech", community, ChannelType.GUILD_TEXT),
        ("streams", community, ChannelType.GUILD_TEXT),
        ("jeux", community, ChannelType.GUILD_TEXT),
        ("classement", community, ChannelType.GUILD_TEXT),
        ("mod-logs", staff, ChannelType.GUILD_TEXT),
        ("textes", community, ChannelType.GUILD_FORUM),
        ("défis", community, ChannelType.GUILD_FORUM),
    ]
    for i, (name, category, kind) in enumerate(layout):
        g.channels.append(FakeChannel(g, CH[name], name, kind, i, category.id))
    g.system_channel = g.get_channel(CH["général"])
    g.members = [
        FakeMember(g, DEV, "lightd", "LightD", [role["Admin"]]),
        FakeMember(g, ADMIN, "camille.b", "Camille", [role["Admin"]]),
        FakeMember(g, MEMBER, "hugo_42", "Hugo", [role["Membre"]]),
        FakeMember(g, BOT_ID, "Michel", "Michel", [role["Michel"]], bot=True),
    ]
    for i, name in enumerate(FIRST_NAMES[1:], start=100):
        uid = f"2000000000000{i:05d}"
        g.members.append(FakeMember(g, uid, f"{name.lower()}{i}", name, [role["Membre"]]))
    g.me = g.get_member(BOT_ID)
    return g


def _server_b() -> FakeGuild:
    from interactions import ChannelType, Permissions

    g = FakeGuild(GB, "Nouveau serveur", 12)
    admin = int(Permissions.ADMINISTRATOR)
    g.roles = [
        FakeRole(GB, "@everyone", GB, 0, 0x400 | 0x800, 0),
        FakeRole(GB, "Michel", "1310000000000000010", 3, admin, 0x5865F2),
    ]
    g.channels = [FakeChannel(g, "1210000000000000011", "général", ChannelType.GUILD_TEXT)]
    g.system_channel = g.channels[0]
    g.members = [
        FakeMember(g, DEV, "lightd", "LightD", [g.roles[1]]),
        FakeMember(g, ADMIN, "camille.b", "Camille", []),
        FakeMember(g, BOT_ID, "Michel", "Michel", [g.roles[1]], bot=True),
    ]
    g.me = g.get_member(BOT_ID)
    return g


class FakeUser:
    id = int(BOT_ID)
    username = "Michel"

    def __str__(self):
        return "Michel#4242"


class FakeBot:
    """What the dashboard reads from ``interactions.Client``."""

    def __init__(self):
        self.guilds = [_server_a(), _server_b()]
        self.user = FakeUser()
        self.latency = 0.043
        self.is_ready = True
        self.ext: dict[str, Any] = {}
        self.interactions_by_scope: dict = {}

    def get_guild(self, gid):
        return next((g for g in self.guilds if g.id == int(gid)), None)

    async def fetch_guild(self, gid):
        return self.get_guild(gid)

    async def fetch_channel(self, cid):
        return next((c for g in self.guilds if (c := g.get_channel(cid)) is not None), None)

    def get_user(self, uid):
        return next((m for g in self.guilds if (m := g.get_member(uid)) is not None), None)

    async def fetch_user(self, uid):
        return self.user if str(uid) == BOT_ID else self.get_user(uid)

    async def synchronise_interactions(self, *args, **kwargs):
        return None


def load_extension_stubs(bot: FakeBot) -> None:
    """One stub per real extension class, with its ``MODULE_KEYS`` and ``Task`` s."""
    from interactions import Extension, Task

    for entry in sorted((ROOT / "extensions").iterdir()):
        if entry.name.startswith(("_", ".")) or entry.name == "__pycache__":
            continue
        modname = "extensions." + entry.name.removesuffix(".py")
        try:
            module = importlib.import_module(modname)
        except Exception as e:  # noqa: BLE001 - a demo shows what it can
            print(f"Extension {modname} non chargée : {e}", file=sys.stderr)
            continue
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Extension)
                and obj is not Extension
                and obj.__module__.startswith(modname)
            ):
                attrs: dict[str, Any] = {
                    "MODULE_KEYS": getattr(obj, "MODULE_KEYS", ()),
                    "RELOAD_ON_SAVE": getattr(obj, "RELOAD_ON_SAVE", False),
                    "HELP_LABEL": getattr(obj, "HELP_LABEL", None),
                    "extension_name": modname,
                    "__module__": modname,
                    "__doc__": obj.__doc__,
                }
                for klass in obj.__mro__:
                    for name, value in vars(klass).items():
                        if isinstance(value, Task):
                            attrs[name] = value
                bot.ext[obj.__name__] = type(obj.__name__, (), attrs)()
                break


# ── The data ─────────────────────────────────────────────────────────


async def claim_databases(client) -> None:
    """Make sure the demo's databases are its own, then empty them.

    A guild database that exists without the demo's marker belongs to someone
    else: the demo stops rather than drop it.
    """
    names = set(await client.list_database_names())
    for name in (f"guild_{GA}", f"guild_{GB}", DEMO_GLOBAL_DB):
        if name in names and DEMO_MARKER not in await client[name].list_collection_names():
            raise SystemExit(f"La base {name} existe et n'a pas été créée par la démo : arrêt.")
        await client.drop_database(name)
        await client[name][DEMO_MARKER].insert_one({"_id": "demo"})


async def seed(bot: FakeBot, *, days: int = 60) -> None:
    """Fill the demo's databases: XP, infractions, audit, slots, Secret Santa."""
    from features.moderation.models import Infraction
    from features.moderation.repository import ModerationRepository
    from features.userinfo.repository import UserInfoRepository
    from features.xp.repository import XpRepository
    from src.core import clock
    from src.core.db import mongo_manager
    from src.core.message_refs import MessageRefRepository
    from src.webui import audit

    await claim_databases(mongo_manager.client)
    guild = bot.get_guild(GA)
    humans = [m for m in guild.members if not m.bot]
    await UserInfoRepository(GA).bulk_upsert(
        [{"id": str(m.id), "username": m.username, "display_name": m.display_name} for m in humans]
    )
    xp = XpRepository(GA)
    await xp.ensure_collection(guild.name)
    rnd = random.Random(7)
    now = clock.now()
    for idx, member in enumerate(humans):
        uid, total = str(member.id), 0
        await xp.insert_new_user(uid, 20, (now - timedelta(days=days)).timestamp())
        activity = max(1, 40 - idx)
        for day in range(days):
            for _ in range(rnd.randint(0, activity // 4)):
                gained = rnd.randint(15, 25)
                total += gained
                at = now - timedelta(days=day, hours=rnd.randint(8, 23), minutes=rnd.randint(0, 59))
                source = rnd.choice(["message", "message", "message", "voice"])
                await xp.log_event(uid, gained, total, at, source=source)
        await xp._xp().update_one({"_id": uid}, {"$set": {"xp": total + 20, "msg": total // 20}})

    moderation = ModerationRepository(GA)
    await moderation.ensure_indexes()
    cases = [
        ("warn", humans[5], "Spam de GIF dans #général"),
        ("timeout", humans[8], "Insultes répétées"),
        ("automod", humans[11], "Invitation Discord interdite"),
        ("warn", humans[8], "Hors-sujet persistant"),
        ("kick", humans[14], "Pub en MP"),
    ]
    for i, (kind, member, reason) in enumerate(cases, start=1):
        automod = kind == "automod"
        await moderation.add(
            Infraction(
                id=await moderation.next_case_id(),
                guild_id=GA,
                user_id=str(member.id),
                moderator_id="automod" if automod else ADMIN,
                type=kind,
                reason=reason,
                duration_seconds=3600 if kind == "timeout" else None,
                source="automod" if automod else "manual",
                created_at=now - timedelta(days=10 - i),
            )
        )

    refs = MessageRefRepository(GA)
    await refs.save("xp:leaderboard", CH["classement"], "1400000000000000001", "Classement XP")
    await refs.save("rolemenu:1", CH["rôles"], "1400000000000000002", "Menu de rôles")

    # Secret Santa: an open session by parcel, 9 participants, 6 wish lists.
    santa = [int(m.id) for m in humans[:9]]
    db = mongo_manager.get_guild_db(GA)
    await db["secret_santa_sessions"].insert_one(
        {
            "_id": f"guild_{GA}",
            "channel_id": int(CH["général"]),
            "message_id": None,
            "created_at": now.isoformat(),
            "created_by": int(ADMIN),
            "participants": santa,
            "is_drawn": False,
            "budget": "25 €",
            "exchange_date": f"{now.year}-12-19",
            "shipping": True,
            "reminded": False,
        }
    )
    for uid in santa[:6]:
        await db["secret_santa_participants"].insert_one(
            {
                "_id": f"guild_{GA}:{uid}",
                "context_id": f"guild_{GA}",
                "user_id": uid,
                "wishlist": "Des livres, du thé, des chaussettes rigolotes.",
                "expires_at": now + timedelta(days=60),
            }
        )

    # Badges (P18): a few won, by the first members of the leaderboard.
    won = {0: ["star", "voice", "seniority"], 1: ["voice", "dj"], 2: ["star"], 3: ["ideas"]}
    await db["achievements"].insert_many(
        [
            {
                "_id": f"{humans[i].id}:{badge}",
                "user_id": str(humans[i].id),
                "badge": badge,
                "earned_at": now - timedelta(days=3 * (i + n)),
            }
            for i, badges in won.items()
            for n, badge in enumerate(badges)
        ]
    )

    # Two reported messages (P15): one to handle, one already dismissed.
    await db["reports"].insert_many(
        [
            {
                "_id": "1400000000000000101",
                "channel_id": CH["général"],
                "author_id": str(humans[7].id),
                "reporters": [
                    {"user_id": str(humans[3].id), "reason": "Spoil du dernier épisode", "at": now},
                    {"user_id": str(humans[4].id), "reason": "Spoil sans balise", "at": now},
                ],
                "status": "open",
                "card_channel_id": CH["mod-logs"],
                "card_message_id": "1400000000000000102",
                "created_at": now - timedelta(hours=2),
                "expires_at": now + timedelta(days=365),
            },
            {
                "_id": "1400000000000000103",
                "channel_id": CH["jeux"],
                "author_id": str(humans[8].id),
                "reporters": [{"user_id": str(humans[5].id), "reason": "Pub", "at": now}],
                "status": "dismissed",
                "handled_by": ADMIN,
                "handled_at": now - timedelta(days=1),
                "created_at": now - timedelta(days=2),
                "expires_at": now + timedelta(days=363),
            },
        ]
    )

    # Hugo's own things, for his space (C6): reminders, a debt in a Tricount group.
    from features.reminders import Reminder, ReminderRepository

    reminders = ReminderRepository(GA)
    evening = clock.at_local(clock.local_now().date(), 20, 30)
    await reminders.add(
        Reminder(user_id=MEMBER, message="Sortir le chien", remind_time=evening, frequency="daily")
    )
    await reminders.add(
        Reminder(
            user_id=MEMBER,
            message="Voter pour la chanson du jour",
            remind_time=evening + timedelta(hours=12, minutes=30),
        )
    )
    lea, tom = int(humans[5].id), int(humans[6].id)
    group = await db["tricount_groups"].insert_one(
        {
            "name": "Vacances Lacanau",
            "is_active": True,
            "members": [int(MEMBER), lea, tom],
            "created_by": lea,
        }
    )
    for payer, amount, what in ((lea, 120.6, "Courses"), (tom, 30, "Essence")):
        await db["tricount_expenses"].insert_one(
            {
                "group_id": group.inserted_id,
                "group_name": "Vacances Lacanau",
                "amount": amount,
                "description": what,
                "payer": payer,
                "participants": [int(MEMBER), lea, tom],
                "added_by": payer,
                "date": now - timedelta(days=2),
            }
        )

    # A week the Gazette can tell: a starred message, a birthday, a suggestion, a giveaway.
    await db["starboard"].insert_one(
        {
            "_id": "1300000000000000001",
            "guild_id": GA,
            "channel_id": CH["général"],
            "original_message_id": "1300000000000000001",
            "mirror_channel_id": CH["starboard"],
            "mirror_message_id": "1300000000000000002",
            "author_id": str(humans[2].id),
            "count": 6,
            "created_at": now - timedelta(days=2),
        }
    )
    soon = (now + timedelta(days=3)).date()
    await db["birthday"].insert_one(
        {
            "user": int(humans[4].id),
            "date": datetime(1996, soon.month, soon.day, tzinfo=UTC),
            "timezone": "Europe/Paris",
        }
    )
    await db["suggestions"].insert_one(
        {
            "id": 4,
            "guild_id": GA,
            "channel_id": CH["suggestions"],
            "author_id": str(humans[3].id),
            "text": "Un salon pour les photos de vacances",
            "status": "implemented",
            "decided_at": now - timedelta(days=1),
            "created_at": now - timedelta(days=9),
        }
    )
    await db["giveaways"].insert_one(
        {
            "guild_id": GA,
            "channel_id": CH["général"],
            "message_id": "1300000000000000003",
            "host_id": ADMIN,
            "prize": "Une clé Steam",
            "ends_at": now - timedelta(days=3),
            "drawn": True,
            "winners": [str(humans[6].id)],
        }
    )

    # Incidents (P21): what the server page's card lists after a few failures.
    from src.core import incidents as incident_log

    kept = incident_log.install()
    for ago, ref, where, reason in [
        (3600 * 5, "4f2a9c", "ScheduleMixin.update", incident_log.REASONS[4][1]),
        (3600 * 2, "b71e03", "component:rolemenu:1300000000000000099", incident_log.REASONS[1][1]),
        (600, "c0ffee", "/agenda", incident_log.UNEXPECTED),
    ]:
        kept.add(GA, incident_log.Incident(now.timestamp() - ago, ref, where, reason, True))

    # Coming up (P19): a giveaway and a poll that close this fortnight.
    await db["giveaways"].insert_one(
        {
            "guild_id": GA,
            "channel_id": CH["général"],
            "message_id": "1300000000000000004",
            "host_id": ADMIN,
            "prize": "Un abonnement Nitro",
            "ends_at": now + timedelta(days=4, hours=2),
            "drawn": False,
            "cancelled": False,
            "winners": [],
        }
    )
    await db["polls"].insert_one(
        {
            "channel_id": CH["général"],
            "message_id": "1300000000000000005",
            "author_id": ADMIN,
            "question": "On fait une sortie ciné samedi ?",
            "options": ["Oui", "Non"],
            "closes_at": now + timedelta(days=1, hours=3),
            "closed": False,
            "votes": {},
        }
    )

    class Admin:
        user_id = ADMIN
        username = "camille.b"

    for action, target, changes in [
        ("module.save", "moduleXp", ["levelRewards", "levelUpMessageList"]),
        ("module.toggle", "moduleFeur", ["enabled"]),
        ("rolemenu.create", "Rôles de jeux", None),
        ("infraction.revoke", "#2", None),
    ]:
        await audit.record(GA, Admin(), action, target=target, changes=changes)
    # What the moduleXp save above replaced: the module's history (C5).
    from src.webui.config_history import ConfigHistoryRepository

    xp = copy.deepcopy(demo_config("", 0, "")["servers"][GA]["moduleXp"])
    xp["levelUpMessageList"] = [{"text": "GG {mention}, niveau {lvl} !", "weight": 1}]
    xp["levelUpEvery"] = 5
    await ConfigHistoryRepository(GA).add("moduleXp", xp)


def record_task_runs(bot: FakeBot) -> None:
    """A run of every extension's tasks, the RSS poller's failing."""
    from interactions import Task

    from src.core.health import health
    from src.discord_ext.task_health import task_name

    for ext in bot.ext.values():
        for value in vars(type(ext)).values():
            if isinstance(value, Task):
                name = task_name(value)
                health.record_task(name, ok="rss" not in name.lower(), duration=0.4)


# ── Serving ──────────────────────────────────────────────────────────


class Demo:
    """The running demo: its app, its bot loop, a session cookie per user."""

    def __init__(self, port: int, mongo_url: str, *, days: int = 60):
        import uvicorn
        from fastapi.responses import RedirectResponse

        from src.core import config as core_config
        from src.core import db
        from src.core.config import config_store
        from src.webui.mutation_limit import SESSION_COOKIE

        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self._tmp = tempfile.TemporaryDirectory(prefix="michel-demo-")
        config_path = os.path.join(self._tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(demo_config(self._tmp.name, port, mongo_url), f, ensure_ascii=False)
        core_config.CONFIG_PATH = config_path
        config_store._data = None
        db.GLOBAL_DB_NAME = DEMO_GLOBAL_DB
        db.mongo_manager._url = mongo_url

        self.bot = FakeBot()
        load_extension_stubs(self.bot)
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self.loop.run_forever, daemon=True, name="demo-bot"
        )
        self._loop_thread.start()
        asyncio.run_coroutine_threadsafe(seed(self.bot, days=days), self.loop).result(600)
        record_task_runs(self.bot)

        # Imported once the extensions are: the routes take the schemas they register.
        from src.webui.app import create_app
        from src.webui.auth import Session

        app = create_app(bot=self.bot, bot_loop=self.loop)
        ctx = app.state.webui
        self.cookies: dict[str, str] = {}
        guilds = [
            {"id": GA, "name": "Les Copains d'abord", "permissions": str(0x8)},
            {"id": GB, "name": "Nouveau serveur", "permissions": str(0x8)},
        ]
        for who, uid in SESSIONS.items():
            user = self.bot.get_user(uid)
            own = guilds if uid != MEMBER else [{**guilds[0], "permissions": "0"}]
            session = Session(
                user_id=uid,
                username=user.username,
                avatar=None,
                guilds=own,
                expires_at=time.time() + 86400,
            )
            ctx.oauth.sessions[session.token_hash] = session
            self.cookies[who] = session.session_token

        def login(who: str):
            response = RedirectResponse("/", status_code=303)
            if who in self.cookies:
                response.set_cookie(SESSION_COOKIE, self.cookies[who], httponly=True)
            return response

        app.add_api_route("/demo/login/{who}", login, methods=["GET"])
        # Ahead of the SPA's catch-all route, which would answer it otherwise.
        app.router.routes.insert(0, app.router.routes.pop())
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"
        )
        self.server = uvicorn.Server(config)
        threading.Thread(target=self.server.run, daemon=True, name="demo-web").start()
        for _ in range(400):
            if self.server.started:
                break
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        # Stopped and closed: an open loop is a ResourceWarning (P27).
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._loop_thread.join(timeout=5)
        self.loop.close()
        self._tmp.cleanup()


# ── Screenshots ──────────────────────────────────────────────────────

VIEWPORTS = {"bureau": (1440, 900), "telephone": (390, 844)}
PUBLIC_TOKEN = "demo-public-0123456789abcdef"  # pragma: allowlist secret — the demo's own


def pages_for(who: str, module_names: list[str]) -> list[tuple[str, ...]]:
    """``(name, hash[, step])`` of every page *who* can open; *step* names a click in STEPS."""
    if who == "anonyme":
        # The public XP page as anyone with its link sees it (its season card, P14).
        return [("connexion", ""), ("public-xp", f"public/{PUBLIC_TOKEN}/xp")]
    if who == "membre":
        return [("accueil", "")]
    pages = [("accueil", ""), ("serveur", f"#/server/{GA}"), ("serveur-neuf", f"#/server/{GB}")]
    pages += [("mon-espace", f"#/me/{GA}")]
    pages += [("serveur-assistant", f"#/setup/{GB}")]
    # After a click: what only shows once the admin does something (P9).
    pages += [("xp-modifie", f"#/module/{GA}/moduleXp", "edit")]
    pages += [("xp-historique", f"#/module/{GA}/moduleXp", "history")]
    pages += [("rss-test", f"#/module/{GA}/moduleRss", "feed")]
    if who == "developpeur":
        pages += [("config-globale", "#/global"), ("extensions", "#/extensions")]
        pages += [("config-globale-discord", "#/global", "accounts")]
        pages += [("liens-courts", "#/links"), ("logs", "#/logs")]
        pages += [(f"module-{m}", f"#/module/{GA}/{m}") for m in module_names]
    return pages


def _edit(page) -> None:
    field = page.locator('.form-field[data-key="levelUpMessageList"] [data-msg-field="message"]')
    field.first.fill("**Bravo** {mention} : niveau {lvl} ! Passe dans <#" + CH["général"] + ">")
    page.locator("#save-bar").wait_for()
    page.wait_for_timeout(800)  # the preview follows the typing


def _history(page) -> None:
    page.get_by_role("button", name="🕘 Historique").click()
    page.locator("#modal-overlay .history-version").first.wait_for()


def _feed(page) -> None:
    # The demo has no Internet: the feed's answer is the one a real feed would give.
    page.route(
        "**/api/servers/*/rss/test",
        lambda route: route.fulfill(
            json={
                "count": 20,
                "entries": [
                    {
                        "title": title,
                        "link": f"https://korben.info/{n}",
                        "summary": "",
                        "author": "Korben",
                        "text": f"**korben** — [{title}](https://korben.info/{n})",
                    }
                    for n, title in enumerate(
                        ["Un outil pour tout sauvegarder", "Linux sur un grille-pain", "Le Wi-Fi 8"]
                    )
                ],
            }
        ),
    )
    page.locator(".rssfeed-entry").first.get_by_role("button", name="🧪 Tester").click()
    page.locator("#modal-overlay .dc-message").first.wait_for()


def _accounts(page) -> None:
    # The Discord section in edit mode: the bot, owner and developer ids named (P17).
    section = page.locator(".card", has=page.locator(".card-title", has_text="Discord"))
    section.get_by_role("button", name="✏️ Éditer").first.click()
    page.locator(".account-chip:not(.account-unknown)").nth(1).wait_for()
    page.wait_for_timeout(400)


STEPS = {"edit": _edit, "history": _history, "feed": _feed, "accounts": _accounts}
# Leaves the page unmodified, so the next one opens without "Abandonner ?".
FORGET_EDITS = """async () => {
    document.activeElement?.blur?.();
    const v = new URL(document.querySelector('script[type=module]').src).search;
    (await import(`/static/js/core/dirty.js${v}`)).setDirty(false);
}"""


def take_shots(demo: Demo, out: Path, chromium: str | None, only: str | None) -> list[Path]:
    """Every page of every session, full height, at desktop and phone width."""
    from playwright.sync_api import sync_playwright

    from src.webui import schemas

    out.mkdir(parents=True, exist_ok=True)
    modules = sorted(schemas.MODULE_SCHEMAS)
    written = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium) if chromium else pw.chromium.launch()
        for viewport, (width, height) in VIEWPORTS.items():
            phone = viewport == "telephone"
            for who in [*SESSIONS, "anonyme"]:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2,
                    locale="fr-FR",
                    is_mobile=phone,
                    has_touch=phone,
                )
                if who in demo.cookies:
                    cookie = {
                        "name": "michel_session",
                        "value": demo.cookies[who],
                        "url": demo.base,
                    }
                    context.add_cookies([cookie])
                page = context.new_page()
                for name, hash_, *step in pages_for(who, modules):
                    if only and only not in name:
                        continue
                    page.goto(f"{demo.base}/{hash_}")
                    page.wait_for_load_state("load")
                    # A page still loading after 8 s is shot as it is.
                    with contextlib.suppress(Exception):
                        page.wait_for_function(
                            "() => !document.querySelector('.loading .spinner')", timeout=8000
                        )
                    page.wait_for_timeout(500)
                    if step:
                        STEPS[step[0]](page)
                    path = out / f"{viewport}-{who}-{name}.png"
                    page.screenshot(path=str(path), full_page=True)
                    written.append(path)
                    if step:  # what the click left behind (an edit, a modal) goes
                        page.keyboard.press("Escape")
                        page.evaluate(FORGET_EDITS)
                        page.goto("about:blank")
                context.close()
        browser.close()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mongo", required=True, help="URL d'un MongoDB de test")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--days", type=int, default=60, help="jours d'XP générés")
    parser.add_argument("--shots", type=Path, help="dossier des captures (puis quitte)")
    parser.add_argument("--only", help="seulement les pages dont le nom contient ce texte")
    parser.add_argument(
        "--chromium",
        default=os.environ.get("PLAYWRIGHT_CHROMIUM_PATH"),
        help="navigateur à utiliser pour les captures",
    )
    args = parser.parse_args(argv)
    demo = Demo(args.port, args.mongo, days=args.days)
    try:
        if args.shots:
            written = take_shots(demo, args.shots, args.chromium, args.only)
            print(f"{len(written)} captures dans {args.shots}")
            return 0
        print(f"Dashboard de démo sur {demo.base}")
        for who in SESSIONS:
            print(f"  {who:12} {demo.base}/demo/login/{who}")
        print("Ctrl+C pour arrêter.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        demo.stop()


if __name__ == "__main__":
    sys.exit(main())
