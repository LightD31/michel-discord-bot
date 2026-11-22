"""One look for everything Michel posts (P6, B8): colours, the author line, the footer.

Every embed the bot builds comes from :func:`card`: the module's accent
colour (or a status colour for a success, a warning, an error), the module's
icon and name as the author line, and a ``Michel · <module>`` footer, which
:func:`sign` rewrites when a card adds its own words to it ("Page 2/3"). An
embed whose look a server's admins chose (the Embed manager, role menus) is
built by :func:`custom` instead and carries no branding.

The palette is softened: every colour keeps at least 3:1 against the embed
background of Discord's dark and light themes (``tests/test_look.py``), where
the old pure ``#00FF00`` / ``#FF0000`` glared on the dark one. Nothing outside
this file writes a colour, builds an ``Embed`` or sets a footer
(``tests/test_look.py`` scans the sources).

Replies to a member start with an icon that says what happened:
:func:`ok` (✅ done), :func:`oops` (❌ refused, or a more telling icon: 🔒 a
right, 📅 a date), :func:`ask` (❓ a question). The voice is in ``docs/VOIX.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from interactions import Embed

# Discord's embed backgrounds, dark and light themes.
DARK_EMBED_BACKGROUND = 0x2B2D31
LIGHT_EMBED_BACKGROUND = 0xF2F3F5


class Palette:
    """The hues, each tuned to a luminance both themes read (≥ 3:1)."""

    GREEN = 0x349051
    RED = 0xEC3D40
    BLUE = 0x2386C7
    BLURPLE = 0x6773F3
    AMBER = 0xB1720C
    ORANGE = 0xC46816
    TEAL = 0x149178
    CYAN = 0x008D9E
    PURPLE = 0xA469BD
    PINK = 0xEB3673
    GREY = 0x7B808A
    GOLD = 0x9A7D1E
    LIME = 0x618C33
    BROWN = 0xC56538
    SPOTIFY = 0x179443
    TWITCH = 0x9D59FF


Kind = Literal["info", "success", "warning", "error", "neutral"]
# A card's colour: the module's accent for information, else the status.
STATUS: dict[str, int] = {
    "success": Palette.GREEN,
    "warning": Palette.ORANGE,
    "error": Palette.RED,
    "neutral": Palette.GREY,
}


@dataclass(frozen=True)
class Look:
    icon: str
    label: str
    accent: int


MICHEL = "michel"
# Each module's look, keyed by its config module. Icon and label are the
# dashboard's (``tests/test_look.py`` keeps them in step with the schemas).
LOOKS: dict[str, Look] = {
    MICHEL: Look("🤖", "Michel", Palette.BLURPLE),
    "discord2name": Look("👤", "Discord → Prénoms", Palette.GREY),
    "moduleAchievements": Look("🏆", "Succès", Palette.AMBER),
    "moduleAgenda": Look("🗓️", "Agenda", Palette.TEAL),
    "moduleBirthday": Look("🎂", "Anniversaires", Palette.PINK),
    "moduleConfrerie": Look("📚", "Confrérie", Palette.BROWN),
    "moduleCustomCommands": Look("✨", "Commandes personnalisées", Palette.CYAN),
    "moduleEmbedManager": Look("📝", "Gestionnaire d'Embeds", Palette.BLUE),
    "moduleFeur": Look("🇫", "Feur", Palette.PURPLE),
    "moduleGazette": Look("📰", "La Gazette", Palette.BLUE),
    "moduleGiveaway": Look("🎁", "Giveaways", Palette.TEAL),
    "moduleIA": Look("🤖", "Intelligence Artificielle", Palette.BLURPLE),
    "moduleLldap": Look("🔐", "LLDAP", Palette.GREY),
    "moduleMDI": Look("🐉", "MDI Tracker (Raider.IO)", Palette.ORANGE),
    "moduleMinecraft": Look("⛏️", "Minecraft", Palette.LIME),
    "moduleModeration": Look("🛡️", "Modération", Palette.RED),
    "moduleOlympics": Look("🏅", "Jeux Olympiques", Palette.BLUE),
    "moduleReactionRoles": Look("🎭", "Rôles à boutons", Palette.PURPLE),
    "moduleRss": Look("📰", "Flux RSS", Palette.ORANGE),
    "moduleSatisfactory": Look("🏭", "Satisfactory", Palette.TEAL),
    "moduleSecretSanta": Look("🎁", "Secret Santa", Palette.RED),
    "moduleSpeedons": Look("🏃", "Speedons", Palette.LIME),
    "moduleSpotify": Look("🎵", "Spotify", Palette.SPOTIFY),
    "moduleStarboard": Look("⭐", "Starboard", Palette.AMBER),
    "moduleStreamlabsCharity": Look("❤️", "Streamlabs Charity", Palette.PINK),
    "moduleSuggestions": Look("💡", "Boîte à suggestions", Palette.GOLD),
    "moduleTricount": Look("💰", "Tricount", Palette.GREEN),
    "moduleTwitch": Look("📺", "Twitch", Palette.TWITCH),
    "moduleUptime": Look("📡", "Uptime", Palette.TEAL),
    "moduleUtils": Look("🛠️", "Utilitaires", Palette.BLUE),
    "moduleVlrgg": Look("🎮", "Esport Tracker (VLR.gg)", Palette.RED),
    "moduleWelcome": Look("👋", "Bienvenue", Palette.GREEN),
    "moduleXp": Look("⭐", "Système d'XP", Palette.BLURPLE),
    "moduleYoutube": Look("▶️", "YouTube", Palette.RED),
    "moduleZevent": Look("🎉", "Zevent", Palette.LIME),
    "moduleZunivers": Look("🎲", "Zunivers", Palette.PURPLE),
}


def look_of(module: str) -> Look:
    """*module*'s look; an unknown key gets Michel's (it raises in the tests, not live)."""
    return LOOKS.get(module) or LOOKS[MICHEL]


def colour(module: str, kind: Kind = "info") -> int:
    """The colour of a *kind* card of *module*: its accent, or the status colour."""
    return STATUS.get(kind, look_of(module).accent)


def footer_text(module: str, extra: str | None = None) -> str:
    """``Michel · Système d'XP``, then *extra* (``… · Page 2/3``)."""
    label = look_of(module).label
    base = "Michel" if label == "Michel" else f"Michel · {label}"
    return f"{base} · {extra}" if extra else base


def card(
    module: str,
    kind: Kind = "info",
    *,
    footer: str | None = None,
    footer_icon: str | None = None,
    author: bool = True,
    accent: int | None = None,
    **fields: Any,
) -> Embed:
    """An embed in Michel's look for *module* (a ``module…`` config key, or ``"michel"``).

    *fields* are the ``Embed`` ones (``title``, ``description``, ``url``,
    ``timestamp``, ``fields``…). *footer* adds words after ``Michel · <module>``.
    The author line is the module's icon and name; pass ``author=False`` where
    the embed names someone instead (it may also call ``set_author`` after).
    *accent* is a colour a server's admins chose for this card (an RSS feed's),
    in place of the module's.
    """
    embed = Embed(color=accent if accent is not None else colour(module, kind), **fields)
    if author:
        found = look_of(module)
        embed.set_author(name=f"{found.icon} {found.label}")
    sign(embed, module, footer, icon_url=footer_icon)
    return embed


def sign(
    embed: Embed, module: str, extra: str | None = None, *, icon_url: str | None = None
) -> Embed:
    """Set *embed*'s footer to ``Michel · <module>``, then *extra*; returns the embed."""
    embed.set_footer(text=footer_text(module, extra), icon_url=icon_url)
    return embed


def recolour(embed: Embed, module: str, kind: Kind) -> Embed:
    """Give *embed* the colour of a *kind* card of *module* (a giveaway once drawn)."""
    embed.color = colour(module, kind)
    return embed


def custom(**fields: Any) -> Embed:
    """An embed whose look a server's admins chose (Embed manager, role menus): no branding."""
    return Embed(**fields)


def ok(text: str) -> str:
    """``✅ text``: something was done."""
    return f"✅ {text}"


def oops(text: str, icon: str = "❌") -> str:
    """``❌ text``: a refusal, with a more telling *icon* where one exists (🔒, 📅, ⏱️)."""
    return f"{icon} {text}"


def ask(text: str) -> str:
    """``❓ text``: a question the member answers with the buttons below."""
    return f"❓ {text}"


__all__ = [
    "DARK_EMBED_BACKGROUND",
    "LIGHT_EMBED_BACKGROUND",
    "LOOKS",
    "MICHEL",
    "STATUS",
    "Kind",
    "Look",
    "Palette",
    "ask",
    "card",
    "colour",
    "custom",
    "footer_text",
    "look_of",
    "ok",
    "oops",
    "recolour",
    "sign",
]
