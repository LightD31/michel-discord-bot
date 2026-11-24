"""The text above a "live started" notification.

Per streamer, a server may name a role to ping and write a message:
``{streamer}`` is the streamer's name, ``{titre}`` the live's title, ``{jeu}``
its category and ``{lien}`` the channel's link. The role's mention leads the
text, so the members who asked are notified even with no message written.
"""

from __future__ import annotations

from src.core.text import fill_message

LIVE_VARIABLES = ("streamer", "titre", "jeu", "lien")


def live_text(
    template: str | None,
    role_id: str | None,
    *,
    streamer: str,
    titre: str,
    jeu: str,
    lien: str,
) -> str | None:
    """The content sent with the notification's embed; ``None`` for none."""
    filled = (
        fill_message(template.strip(), streamer=streamer, titre=titre, jeu=jeu, lien=lien)
        if template and template.strip()
        else ""
    )
    mention = f"<@&{role_id}>" if role_id and str(role_id).isdigit() else ""
    text = " ".join(part for part in (mention, filled) if part)
    return text or None


__all__ = ["LIVE_VARIABLES", "live_text"]
