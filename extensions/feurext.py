"""Extension Feur — thin Discord glue layer.

All MongoDB I/O lives in features/feur/.
"""

import os
import re
import string
from datetime import datetime

from interactions import Embed, EmbedFooter, Extension, SlashContext, listen, slash_command
from interactions.api.events import MessageCreate

from features.feur import FeurRepository
from src import logutil
from src.core.config import load_config
from src.core.text import sanitize_content
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleFeur")

FEUR_EMOJIS = ["🇫", "🇪", "🇺", "🇷"]
POUR_FEUR_EMOJIS = ["🇵", "🇴", "🇺", "🇷", "🇫", "🇪", "⛎", "®️"]


def _extract_words(content: str) -> list:
    translator = str.maketrans("", "", string.punctuation)
    return content.translate(translator).split()


def _split_into_sentences(content: str) -> list:
    return [s.strip() for s in re.split(r"[.!?\n]+|  +", content) if s.strip()]


def _should_respond(content: str, keyword: str) -> bool:
    if keyword not in content:
        return False
    words = _extract_words(content)
    if words and words[-1] == keyword:
        return True
    for sentence in _split_into_sentences(content):
        if (
            keyword in sentence
            and "?" in sentence
            and sentence.find("?", sentence.find(keyword)) != -1
        ):
            return True
    return False


class FeurExtension(Extension):
    def __init__(self, bot):
        self.bot = bot

    async def _add_reactions(self, message, emojis: list) -> None:
        try:
            for emoji in emojis:
                await message.add_reaction(emoji)
        except Exception as e:
            logger.debug("Could not add reactions: %s", e)

    @listen()
    async def on_message(self, event: MessageCreate):
        if event.message.author.bot:
            return
        if event.message.guild is None:
            return
        if str(event.message.guild.id) not in module_config:
            return

        content = sanitize_content(event.message.content.lower()).strip()
        user_id = str(event.message.author.id)
        guild_id = str(event.message.guild.id)
        repo = FeurRepository(guild_id)

        if _should_respond(content, "pourquoi"):
            await self._add_reactions(event.message, POUR_FEUR_EMOJIS)
            await repo.record_event(user_id, "pour_feur")
            return

        if _should_respond(content, "quoi"):
            words = _extract_words(content)
            if words and words[-1] == "quoi" or ("quoi" in content and "pourquoi" not in content):
                await self._add_reactions(event.message, FEUR_EMOJIS)
                await repo.record_event(user_id, "feur")

    @slash_command(name="feurstats", description="Affiche les statistiques de feur")
    async def feur_stats(self, ctx: SlashContext):
        if not await require_guild(ctx):
            return

        guild_id = str(ctx.guild_id)
        user_id = str(ctx.author.id)
        repo = FeurRepository(guild_id)

        guild_stats = await repo.get_guild_stats()
        user_stats = await repo.get_user_stats(user_id)

        top_users = []
        if ctx.guild:
            user_totals = await repo.get_all_user_totals()
            resolved = []
            for uid, total in user_totals:
                try:
                    member = await ctx.guild.fetch_member(int(uid))
                    if member:
                        resolved.append((member.display_name, total))
                except Exception:
                    pass
            top_users = sorted(resolved, key=lambda x: x[1], reverse=True)[:5]

        embed = Embed(
            title="📊 Statistiques Feur",
            color=Colors.FEUR,
            timestamp=datetime.now(),
            footer=EmbedFooter(text=f"Demandé par {ctx.author.display_name}"),
        )
        embed.add_field(
            name="🏠 Stats du serveur",
            value=f"Total: **{guild_stats.total}**\nFeur: **{guild_stats.feur}**\nPour feur: **{guild_stats.pour_feur}**",
            inline=False,
        )
        embed.add_field(
            name="👤 Tes stats",
            value=f"Total: **{user_stats.total}**\nFeur: **{user_stats.feur}**\nPour feur: **{user_stats.pour_feur}**",
            inline=True,
        )
        if top_users:
            embed.add_field(
                name="🏆 Top 5 du serveur",
                value="\n".join(
                    f"{i + 1}. {name}: **{count}**" for i, (name, count) in enumerate(top_users)
                ),
                inline=False,
            )
        await ctx.send(embed=embed)
