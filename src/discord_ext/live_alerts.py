"""The "🔔 M'avertir" button under a live or video notification.

A streamer (or a YouTube channel) may name a role its notification pings; the
button under the notification hands that role out, or takes it back, to
whoever clicks. The same rules as a role menu: the role is re-checked at each
click (never a staff role, nor one Discord manages), and the click is
refused unless the role is still the one the server's settings name, so an
old notification can't hand out a role the admins have since replaced.

Each module answers its own prefix (``twitch:alerte``, ``youtube:alerte``),
the custom id ending with the role's id.
"""

from __future__ import annotations

import re
from typing import Any

from interactions import Button, ButtonStyle

from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.look import oops
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.role_grants import grantable_role

logger = logutil.init_logger(__name__)

TWITCH_ALERT = "twitch:alerte"
YOUTUBE_ALERT = "youtube:alerte"


def alert_pattern(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix)}:\d+$")


def alert_button(prefix: str, role_id: str | int, label: str) -> Button:
    return Button(
        style=ButtonStyle.SECONDARY, label=label, emoji="🔔", custom_id=f"{prefix}:{role_id}"
    )


def alert_role_id(custom_id: str) -> str:
    return custom_id.rsplit(":", 1)[-1]


def configured_alert_roles(entries: Any, key: str) -> set[str]:
    """The role ids the entries of a streamer or channel map name under *key*."""
    values = entries.values() if isinstance(entries, dict) else []
    return {
        str(entry.get(key)).strip()
        for entry in values
        if isinstance(entry, dict) and str(entry.get(key) or "").strip().isdigit()
    }


async def toggle_alert_role(ctx: Any, configured: set[str], *, what: str) -> None:
    """Give the clicker the notification's role, or take it back; say which, privately.

    *what* names what they will be told about (« des lives de zerator »).
    """
    role_id = alert_role_id(ctx.custom_id)
    if role_id not in configured:
        await ctx.send(
            oops("Ce bouton ne sert plus : les admins ont changé le rôle de ces alertes."),
            ephemeral=True,
        )
        return
    role, refusal = grantable_role(ctx.guild, role_id)
    if role is None:
        reason = (refusal or "rôle introuvable").rstrip(".")
        await ctx.send(
            oops(
                f"Je ne peux pas te donner ce rôle : {reason[:1].lower()}{reason[1:]}.", icon="🔒"
            ),
            ephemeral=True,
        )
        return
    member = ctx.author
    had = member.has_role(role)
    try:
        if had:
            await member.remove_role(role, reason="🔔 Alertes")
        else:
            await member.add_role(role, reason="🔔 Alertes")
    except Exception as e:
        log_failure(logger, "Alert role %s not changed for %s: %s", role_id, member.id, e)
        await ctx.send(
            oops(
                "Je n'ai pas pu changer ce rôle : mon rôle doit être au-dessus de lui. "
                "Un administrateur peut corriger l'ordre des rôles."
            ),
            ephemeral=True,
        )
        return
    text = (
        f"🔕 C'est noté : tu ne seras plus averti {what}."
        if had
        else f"🔔 C'est noté : tu seras averti {what} (rôle {role.mention}). Reclique pour arrêter."
    )
    await ctx.send(text, ephemeral=True, allowed_mentions=NO_PINGS)


__all__ = [
    "TWITCH_ALERT",
    "YOUTUBE_ALERT",
    "alert_button",
    "alert_pattern",
    "alert_role_id",
    "configured_alert_roles",
    "toggle_alert_role",
]
