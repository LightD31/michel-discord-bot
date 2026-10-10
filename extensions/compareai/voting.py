"""VotingMixin — persistent vote buttons, timed reveal, and /ask-stats.

Votes are stored in MongoDB (``ai_comparisons``), any member can vote (and
change their vote) until the deadline, and a minute task closes due
comparisons: model names are revealed, the winner marked, buttons removed.
Button custom ids carry the storage scope and comparison id, so votes keep
working across restarts.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from interactions import (
    Client,
    ComponentContext,
    Embed,
    IntegrationType,
    IntervalTrigger,
    SlashContext,
    Task,
    auto_defer,
    component_callback,
    slash_command,
)

from features.ai import AiComparisonRepository, Comparison, compute_model_stats
from src.core.errors import DatabaseError
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import edit_message_if_changed

from ._common import SCOPE_GLOBAL, SCOPE_GUILD, enabled_guild_ids, logger, repo_for, scope_for
from .ask import VOTE_PREFIX, render_comparison

_VOTE_RE = re.compile(rf"^{VOTE_PREFIX}:([{SCOPE_GUILD}{SCOPE_GLOBAL}]):([0-9a-f]{{24}}):(\d)$")
STATS_TOP = 15


class VotingMixin:
    bot: Client

    # =========================================================================
    # Vote buttons
    # =========================================================================

    @component_callback(_VOTE_RE)
    async def on_ai_vote(self, ctx: ComponentContext) -> None:
        match = _VOTE_RE.match(ctx.custom_id)
        if not match:
            return
        scope, comparison_id, index = match.group(1), match.group(2), int(match.group(3))
        repo = repo_for(scope, ctx.guild_id)

        try:
            comparison = await repo.get(comparison_id)
            if comparison is None:
                await ctx.send("Comparaison introuvable.", ephemeral=True)
                return
            if comparison.closed or index >= len(comparison.answers):
                await self._show_final(ctx, comparison)
                return
            previous = comparison.votes.get(str(ctx.author.id))
            key = comparison.answers[index].key
            updated = await repo.set_vote(comparison_id, str(ctx.author.id), key)
        except DatabaseError as e:
            logger.error("Vote failed for comparison %s: %s", comparison_id, e)
            await ctx.send("❌ Impossible d'enregistrer le vote pour l'instant.", ephemeral=True)
            return

        if updated is None:  # closed between the read and the write
            await self._show_final(ctx, comparison)
            return

        try:
            if ctx.message is not None:
                await edit_message_if_changed(
                    ctx.message, content=render_comparison(updated)[-1], logger=logger
                )
        except Exception as e:
            logger.warning("Could not refresh comparison %s: %s", comparison_id, e)

        if previous == key:
            feedback = f"Tu as déjà voté pour la réponse {index + 1}."
        elif previous:
            feedback = f"Vote changé : réponse {index + 1}."
        else:
            feedback = f"Vote enregistré pour la réponse {index + 1}."
        await ctx.send(feedback, ephemeral=True)

    async def _show_final(self, ctx: ComponentContext, comparison: Comparison) -> None:
        """A click on a closed comparison: make sure it shows the results, then say so.

        Covers comparisons the close task couldn't edit (e.g. a user-installed
        /ask in a server the bot isn't a member of).
        """
        comparison.closed = True
        try:
            if ctx.message is not None:
                await edit_message_if_changed(
                    ctx.message,
                    content=render_comparison(comparison)[-1],
                    components=[],
                    logger=logger,
                )
        except Exception as e:
            logger.debug("Could not show final results: %s", e)
        await ctx.send("Le vote est terminé.", ephemeral=True)

    # =========================================================================
    # Closing
    # =========================================================================

    def _close_targets(self) -> list[AiComparisonRepository]:
        repos = [repo_for(SCOPE_GUILD, gid) for gid in enabled_guild_ids()]
        repos.append(repo_for(SCOPE_GLOBAL, None))
        return repos

    @Task.create(IntervalTrigger(minutes=1))
    async def close_due_comparisons(self) -> None:
        now = datetime.now(UTC)
        for repo in self._close_targets():
            try:
                due = await repo.due_for_close(now)
            except DatabaseError as e:
                logger.error("Could not list due comparisons (%s): %s", repo.guild_id, e)
                continue
            for comparison in due:
                await self._close_comparison(repo, comparison)

    async def _close_comparison(self, repo: AiComparisonRepository, comparison: Comparison) -> None:
        try:
            final = await repo.close(comparison.id or "")
        except DatabaseError as e:
            logger.error("Could not close comparison %s: %s", comparison.id, e)
            return
        if final is None or not final.message_ids:
            return
        try:
            channel = await self.bot.fetch_channel(int(final.channel_id))
            message = await channel.fetch_message(int(final.message_ids[-1]))  # type: ignore[union-attr]
        except Exception as e:
            logger.info("Comparison %s closed, message not reachable: %s", final.id, e)
            return
        if message is None:
            return
        try:
            await edit_message_if_changed(
                message, content=render_comparison(final)[-1], components=[], logger=logger
            )
        except Exception as e:
            logger.warning("Could not reveal comparison %s: %s", final.id, e)

    # =========================================================================
    # /ask-stats
    # =========================================================================

    @slash_command(
        name="ask-stats",
        description="Classement des modèles d'IA selon vos votes",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @auto_defer()
    async def ask_stats(self, ctx: SlashContext) -> None:
        repo = repo_for(scope_for(ctx.guild_id), ctx.guild_id)
        try:
            comparisons = await repo.list_for_stats()
        except DatabaseError as e:
            logger.error("Could not load AI stats: %s", e)
            await ctx.send("❌ Statistiques indisponibles pour l'instant.", ephemeral=True)
            return

        stats = compute_model_stats(comparisons)
        embed = Embed(title="🏆 Classement des modèles d'IA", color=Colors.INFO)
        if not stats:
            embed.description = "Aucun vote pour l'instant. Lance une comparaison avec `/ask` !"
        else:
            lines = [
                f"**{rank}. {s.display_name}** — {s.wins}/{s.appearances} victoires "
                f"({s.win_rate:.0%}) · {s.votes} vote{'s' if s.votes > 1 else ''}"
                for rank, s in enumerate(stats[:STATS_TOP], start=1)
            ]
            embed.description = "\n".join(lines)
            voted = len(comparisons)
            embed.set_footer(
                f"{voted} comparaison{'s' if voted > 1 else ''} votée{'s' if voted > 1 else ''}"
            )
        await ctx.send(embed=embed)
