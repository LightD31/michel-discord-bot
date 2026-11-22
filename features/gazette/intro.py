"""The Gazette's opening lines, written by the AI where a server's admins asked for it (P14).

Opt-in twice: the Gazette's own ``gazetteAiIntro`` switch, and the AI module
on in that server. Each edition spends one call of the AI's daily budget,
counted under the Gazette's own key (``gazette:<guild>``) as ``/ask`` counts
a member's: the kill switch, the global limit and a database outage all mean
no intro, never a missing paper. Only the paper's text goes to the provider:
the display names it lists, no message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from features.ai import AiClient, AiGlobalSettings, AiUsageRepository, GuildAiSettings
from features.ai.prompt import extract_response
from features.ai.quota import access_refusal
from features.ai.settings import resolve_default_model, usable_models
from features.gazette.edition import AI_INTRO_LIMIT, Edition, render
from src.core import logging as logutil
from src.core.config import ModuleConfig, config_store
from src.core.errors import DatabaseError
from src.core.text import clip
from src.integrations import llm

logger = logutil.init_logger(__name__)

AI_MODULE = ModuleConfig("moduleIA")
INTRO_TOKENS = 220
INSTRUCTIONS = (
    "Tu écris les deux ou trois phrases d'ouverture de « La Gazette », le journal "
    "hebdomadaire du serveur Discord {server}. Ton : complice, chaleureux, une pointe "
    "d'humour, en français, en tutoyant les lecteurs. Appuie-toi uniquement sur le sommaire "
    "ci-dessous : n'invente aucun fait, ne cite personne qui n'y figure pas, pas de titre, "
    "pas de liste, pas de mention @, pas de lien. Moins de 400 caractères."
)


def intro_messages(edition: Edition, persona: str) -> list[dict[str, str]]:
    """The prompt: Michel's persona, the brief, and the paper's own contents."""
    title, intro, fields = render(edition)
    summary = "\n\n".join([intro, *(f"{name}\n{value}" for name, value in fields)])
    return [
        {
            "role": "system",
            "content": f"{persona}\n\n" + INSTRUCTIONS.format(server=edition.guild_name),
        },
        {"role": "user", "content": f"{title}\n\n{summary}"},
    ]


def _clean(text: str) -> str:
    """One paragraph, no mention or link the paper would show."""
    text = " ".join(text.replace("@", "").split())
    return clip(text, AI_INTRO_LIMIT)


async def write_intro(
    guild_id: str,
    edition: Edition,
    *,
    client: AiClient | None = None,
    reserve: Callable[..., Awaitable[str | None]] | None = None,
) -> str:
    """The AI's opening lines for *edition*, or ``""`` (switched off, refused, failed)."""
    settings = ModuleConfig("moduleGazette").get(guild_id)
    if not settings.get("gazetteAiIntro") or not AI_MODULE.enabled(guild_id):
        return ""
    global_cfg = AiGlobalSettings.from_config(config_store.get().get("config", {}))
    if access_refusal(global_cfg.limits, in_enabled_guild=True):
        return ""
    guild_cfg = GuildAiSettings.from_config(AI_MODULE.get(guild_id))
    model = resolve_default_model(
        usable_models(global_cfg.models, llm.configured_apis()),
        guild_cfg.default_model,
        global_cfg.default_model,
    )
    if model is None:
        return ""
    reserve = reserve or AiUsageRepository().reserve
    try:
        hit = await reserve(f"gazette:{guild_id}", 1, global_cfg.limits, datetime.now(UTC))
    except DatabaseError as e:
        logger.warning("Gazette intro of %s skipped, quotas unreadable: %s", guild_id, e)
        return ""
    if hit:
        logger.info("Gazette intro of %s skipped: the AI's %s limit is reached", guild_id, hit)
        return ""
    answer = await (client or AiClient()).complete(
        model, intro_messages(edition, guild_cfg.persona), INTRO_TOKENS
    )
    if answer.error or not answer.content:
        logger.warning("Gazette intro of %s not written: %s", guild_id, answer.error)
        return ""
    return _clean(extract_response(answer.content))


__all__ = ["INSTRUCTIONS", "intro_messages", "write_intro"]
