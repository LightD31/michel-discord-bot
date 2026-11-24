"""A new server's first steps in the dashboard.

A server Michel just joined used to face a flat list of switches. The
dashboard now offers a short wizard and keeps a "Pour bien démarrer" list on
the server page until every step is done or an admin hides it.

* A *pack* is a preset of module settings for one kind of community. Applying
  it goes through the same checks as a module save (schema, channels of this
  guild, the roles the editor may hand out), and never touches a module the
  server already has settings for, even switched off.
* The checklist is computed from facts, never from a "step done" flag: the
  bot's rights on the server, the modules switched on, the welcome channel and
  the moderators' role in the settings, and the audit log (a welcome message
  tested, Michel announced). Hiding it is the one thing stored, as
  ``servers.<id>.onboardingDismissed`` in the config.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from interactions import Permissions

from src.core.text import EMBED_DESCRIPTION_LIMIT, clip
from src.discord_ext.channels import fetch_guild_channel, member_can
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import CAN_SEND, jump_url
from src.webui.guild_health import REQUIRED_PERMISSIONS

DISMISSED_KEY = "onboardingDismissed"
# The audit actions the checklist reads (src/webui/audit.py).
TEMPLATE_TEST = "template.test"
ANNOUNCE = "onboarding.announce"
WELCOME = "moduleWelcome"
WELCOME_FIELD = "welcomeChannelId"
MODERATION = "moduleModeration"
MODERATORS_FIELD = "staffRoleId"

# What Michel needs on the whole server, beyond what each channel checks:
# the channel rights everywhere, and handing out the roles its modules give.
SERVER_RIGHTS: tuple[tuple[Permissions, str], ...] = (
    *REQUIRED_PERMISSIONS,
    (Permissions.MANAGE_ROLES, "Gérer les rôles"),
)


@dataclass(frozen=True)
class Pack:
    """One kind of community, and the modules that suit it.

    *modules* maps each module to the settings it gets on top of
    ``enabled: True`` (the schema's defaults do the rest); *asks* names, per
    module, the channel and role fields the wizard asks with its pickers;
    *later* lists modules worth a look afterwards, which need settings a pack
    can't guess (a streamer's name, a team).
    """

    key: str
    label: str
    icon: str
    tagline: str
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    asks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    later: tuple[str, ...] = ()


PACKS: dict[str, Pack] = {
    pack.key: pack
    for pack in (
        Pack(
            "potes",
            "Entre potes",
            "🍻",
            "Le serveur d'une bande d'amis",
            modules={
                WELCOME: {},
                "moduleBirthday": {},
                "moduleXp": {},
                "moduleStarboard": {},
                "moduleUtils": {},
                "moduleFeur": {},
                "moduleGazette": {},
            },
            asks={
                WELCOME: (WELCOME_FIELD,),
                "moduleBirthday": ("birthdayChannelId",),
                "moduleStarboard": ("starboardChannelId",),
                "moduleGazette": ("gazetteChannelId",),
            },
            later=("moduleCustomCommands", "moduleSpotify"),
        ),
        Pack(
            "stream",
            "Communauté de stream",
            "📺",
            "Autour d'un·e créateur·ice",
            modules={
                WELCOME: {},
                MODERATION: {},
                "moduleXp": {},
                "moduleGiveaway": {},
                "moduleReactionRoles": {},
                "moduleSuggestions": {},
                "moduleUtils": {},
            },
            asks={
                WELCOME: (WELCOME_FIELD,),
                MODERATION: ("modLogChannelId", MODERATORS_FIELD),
                "moduleSuggestions": ("suggestChannelId",),
            },
            later=("moduleTwitch", "moduleYoutube"),
        ),
        Pack(
            "esport",
            "Équipe / esport",
            "🎮",
            "Suivre des matchs, jouer ensemble",
            modules={
                WELCOME: {},
                "moduleVlrgg": {},
                "moduleXp": {},
                "moduleUtils": {},
            },
            asks={
                WELCOME: (WELCOME_FIELD,),
                "moduleVlrgg": ("notificationChannelId",),
            },
            later=("moduleMDI", "moduleMinecraft", "moduleSatisfactory"),
        ),
        Pack("sur-mesure", "Sur mesure", "📚", "Je choisis tout moi-même"),
    )
}


def _field_label(schema: dict[str, Any], name: str) -> str:
    return str((schema.get("fields") or {}).get(name, {}).get("label") or name)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def pack_settings(
    pack: Pack,
    server: dict[str, Any],
    answers: dict[str, Any],
    module_schemas: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """What applying *pack* writes, module by module, and what it leaves aside, with why.

    *answers* holds what the wizard's pickers chose, by module then field;
    only the fields the pack asks are taken from it. A module whose extension
    isn't loaded (no schema), that the server already has settings for, or
    missing a required field is left aside.
    """
    writes: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for module, preset in pack.modules.items():
        schema = module_schemas.get(module)
        if schema is None:
            skipped.append({"module": module, "reason": "module indisponible sur ce bot"})
            continue
        if module in server:
            skipped.append({"module": module, "reason": "déjà réglé : laissé tel quel"})
            continue
        settings: dict[str, Any] = {"enabled": True, **copy.deepcopy(preset)}
        given = answers.get(module)
        chosen: dict[str, Any] = given if isinstance(given, dict) else {}
        for name in pack.asks.get(module, ()):
            value = chosen.get(name)
            if not _is_empty(value):
                settings[name] = value
        missing = [
            _field_label(schema, name)
            for name, meta in (schema.get("fields") or {}).items()
            if meta.get("required") and _is_empty(settings.get(name, meta.get("default")))
        ]
        if missing:
            skipped.append({"module": module, "reason": "il manque : " + ", ".join(missing)})
            continue
        writes[module] = settings
    return writes, skipped


def pack_catalogue(module_schemas: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The packs as the wizard shows them: their modules, and the fields to ask."""
    out = []
    for pack in PACKS.values():
        available = [m for m in pack.modules if m in module_schemas]
        asks = [
            {"module": module, "field": name, "meta": module_schemas[module]["fields"][name]}
            for module in available
            for name in pack.asks.get(module, ())
            if name in (module_schemas[module].get("fields") or {})
        ]
        out.append(
            {
                "key": pack.key,
                "label": pack.label,
                "icon": pack.icon,
                "tagline": pack.tagline,
                "modules": available,
                "asks": asks,
                "later": [m for m in pack.later if m in module_schemas],
            }
        )
    return out


def _enabled(server: dict[str, Any], module: str) -> dict[str, Any] | None:
    settings = server.get(module)
    if isinstance(settings, dict) and settings.get("enabled"):
        return settings
    return None


def checklist(
    server: dict[str, Any],
    *,
    missing_rights: list[str] | None,
    welcome_tested: bool,
    announced: bool,
) -> list[dict[str, Any]]:
    """The "Pour bien démarrer" steps, each done or not, from what the server holds.

    *missing_rights* is ``None`` when the bot couldn't be asked.
    """
    welcome = _enabled(server, WELCOME)
    stored = server.get(MODERATION)
    moderation: dict[str, Any] = stored if isinstance(stored, dict) else {}
    if missing_rights is None:
        rights_hint = "Bot hors ligne : droits non vérifiés."
    elif missing_rights:
        rights_hint = (
            "Il lui manque : "
            + ", ".join(missing_rights)
            + ". Paramètres du serveur → Rôles → Michel."
        )
    else:
        rights_hint = ""
    return [
        {"key": "rights", "label": "Michel a les droits nécessaires",
         "done": missing_rights == [], "hint": rights_hint},
        {"key": "pack", "label": "Choisir un type de communauté",
         "done": any(_enabled(server, k) for k in server), "hint": ""},
        {"key": "welcome", "label": "Salon de bienvenue",
         "done": bool(welcome and welcome.get(WELCOME_FIELD)), "hint": ""},
        {"key": "moderators", "label": "Rôle des modérateurs",
         "done": bool(moderation.get(MODERATORS_FIELD)), "hint": ""},
        {"key": "welcome_test", "label": "Tester le message de bienvenue",
         "done": welcome_tested, "hint": ""},
        {"key": "announce", "label": "Annoncer Michel aux membres",
         "done": announced, "hint": ""},
    ]  # fmt: skip


def missing_server_rights(bot: Any, guild_id: str) -> list[str] | None:
    """The rights Michel lacks on the whole server, ``None`` when it can't tell (bot loop)."""
    guild = bot.get_guild(int(guild_id)) if bot is not None else None
    me = getattr(guild, "me", None)
    if me is None:
        return None
    try:
        held = me.guild_permissions
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    if Permissions.ADMINISTRATOR in held:
        return []
    return [label for flag, label in SERVER_RIGHTS if flag not in held]


ANNOUNCE_TITLE = "👋 Michel arrive sur le serveur"
# What the announcement needs from the bot in its channel.
BOT_CAN_ANNOUNCE = CAN_SEND | Permissions.EMBED_LINKS


def announcement_text(server: dict[str, Any], module_schemas: dict[str, dict[str, Any]]) -> str:
    """What Michel tells the members: one line per module switched on, then ``/aide``."""
    lines = []
    for module, schema in sorted(module_schemas.items(), key=lambda kv: kv[1].get("label", "")):
        if not _enabled(server, module) or schema.get("noToggle"):
            continue
        what = (schema.get("help") or {}).get("what") or schema.get("description") or ""
        label = f"{schema.get('icon', '')} **{schema.get('label', module)}**".strip()
        lines.append(f"{label} — {what}" if what else label)
    intro = "Salut tout le monde ! Je suis Michel, le bot du serveur."
    if lines:
        intro += " Voici ce que je fais ici :\n\n" + "\n".join(lines)
    outro = "\n\nTape `/aide` pour voir mes commandes, module par module."
    return clip(intro, EMBED_DESCRIPTION_LIMIT - len(outro)) + outro


async def post_announcement(
    bot: Any, guild_id: str, channel_id: str, user_id: str, text: str, *, is_developer: bool
) -> str:
    """Post the announcement in *channel_id*, as the editor could by hand (bot loop).

    The dashboard bar is ``MANAGE_GUILD``: posting there with the bot's rights
    also takes the editor's own right to write in that channel. Returns the
    message's link.
    """
    channel = await fetch_guild_channel(bot, guild_id, channel_id)
    if channel is None or not hasattr(channel, "send"):
        raise HTTPException(status_code=422, detail="Salon introuvable sur ce serveur.")
    guild = bot.get_guild(int(guild_id))
    if not is_developer:
        member = await guild.fetch_member(int(user_id)) if guild is not None else None
        if not member_can(member, channel, CAN_SEND):
            raise HTTPException(
                status_code=403,
                detail=f"Il te faut « Envoyer des messages » dans #{channel.name} pour y annoncer Michel.",
            )
    if not member_can(getattr(guild, "me", None), channel, BOT_CAN_ANNOUNCE):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Michel ne peut pas écrire dans #{channel.name} : il lui faut « Envoyer des "
                "messages » et « Intégrer des liens »."
            ),
        )
    message = await channel.send(
        embeds=[card("michel", title=ANNOUNCE_TITLE, description=text)],
        allowed_mentions=NO_PINGS,
    )
    return jump_url(guild_id, channel.id, message.id)


__all__ = [
    "ANNOUNCE",
    "announcement_text",
    "post_announcement",
    "DISMISSED_KEY",
    "PACKS",
    "Pack",
    "checklist",
    "missing_server_rights",
    "pack_catalogue",
    "pack_settings",
]
