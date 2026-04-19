"""
VotingMixin — voting-related slash commands and handlers for CompareAI.
"""

import contextlib

from interactions import (
    Buckets,
    Button,
    ButtonStyle,
    Client,
    IntegrationType,
    OptionType,
    SlashContext,
    auto_defer,
    cooldown,
    slash_command,
    slash_option,
)
from interactions.api.events import Component
from interactions.client.errors import CommandOnCooldown

from src.discord_ext.messages import send_error, send_success

from ._common import (
    VOTE_TIMEOUT_SECONDS,
    VoteManager,
    logger,
)


class VotingMixin:
    """Mixin providing all voting-related slash commands and handlers."""

    # These attributes are provided by the concrete extension class.
    bot: Client
    vote_manager: VoteManager

    # =========================================================================
    # Slash command
    # =========================================================================

    @slash_command(
        name="ask",
        description="Ask Michel and vote for the better answer",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @cooldown(Buckets.USER, 1, 20)
    @auto_defer()
    @slash_option("question", "Ta question", opt_type=OptionType.STRING, required=True)
    async def ask_question(self, ctx: SlashContext, question: str) -> None:
        """Main command to ask a question and compare AI responses."""
        if not self.openrouter_client:
            await send_error(ctx, "Le client OpenRouter n'est pas initialisé")
            return

        try:
            await self._process_question(ctx, question)
        except CommandOnCooldown:
            await send_error(ctx, "La commande est en cooldown, veuillez réessayer plus tard")
        except Exception as e:
            logger.error(f"Unexpected error in ask_question: {e}")
            await send_error(ctx, "Une erreur inattendue s'est produite")

    # =========================================================================
    # Response sending
    # =========================================================================

    async def _send_response_message(
        self, ctx: SlashContext, question: str, responses: list[dict]
    ) -> None:
        """Send the response message with voting buttons."""
        message_content = self._format_responses_message(ctx, question, responses)
        components = self._create_vote_buttons(responses)

        message_info = await self._split_and_send_message(
            ctx, message_content, components=components
        )
        await self._handle_vote(ctx, message_info, question, responses, components)

    def _format_responses_message(
        self, ctx: SlashContext, question: str, responses: list[dict]
    ) -> str:
        """Format the responses into a Discord message."""
        formatted_responses = "\n\n".join(
            f"Réponse {i + 1} : \n> {resp['content'].replace(chr(10), chr(10) + '> ')}"
            for i, resp in enumerate(responses)
        )
        return (
            f"**{ctx.author.mention} : {question}**\n\n"
            f"{formatted_responses}\n\n"
            f"Votez pour la meilleure réponse en cliquant sur le bouton correspondant"
        )

    def _create_vote_buttons(self, responses: list[dict]) -> list[Button]:
        """Create voting buttons for responses."""
        return [
            Button(
                label=f"Réponse {i + 1}",
                style=ButtonStyle.SECONDARY,
                custom_id=resp["custom_id"],
            )
            for i, resp in enumerate(responses)
        ]

    # =========================================================================
    # Vote handling
    # =========================================================================

    async def _handle_vote(
        self,
        ctx: SlashContext,
        message_info,
        question: str,
        responses: list[dict],
        components: list[Button],
    ) -> None:
        """Handle the voting process for AI responses."""
        try:
            button_ctx: Component = await self.bot.wait_for_component(
                components=components, timeout=VOTE_TIMEOUT_SECONDS
            )

            if button_ctx.ctx.author_id != ctx.author.id:
                await send_error(button_ctx.ctx, "Vous n'avez pas le droit de voter sur ce message")
                return

            await self._process_vote(ctx, button_ctx, message_info, question, responses)

        except TimeoutError:
            await self._handle_vote_timeout(ctx, message_info, question, responses)
        except Exception as e:
            logger.error(f"Unexpected error during voting: {e}")
            with contextlib.suppress(Exception):
                await send_error(ctx, "Erreur lors du traitement du vote")

    async def _process_vote(
        self,
        ctx: SlashContext,
        button_ctx: Component,
        message_info,
        question: str,
        responses: list[dict],
    ) -> None:
        """Process a vote selection."""
        provider_id = button_ctx.ctx.custom_id
        logger.info(f"Vote registered: {provider_id}")

        self.vote_manager.save_vote(provider_id)

        selected = next((r for r in responses if r["custom_id"] == provider_id), None)

        if selected:
            model_name = self._get_model_display_name(provider_id)
            new_content = (
                f"**{ctx.author.mention} : {question}**\n\n"
                f"**Réponse choisie ({model_name}) :**\n{selected['content']}"
            )

            try:
                if len(new_content) <= 2000:
                    await message_info.edit(content=new_content, components=[])
                else:
                    await message_info.delete()
                    await self._split_and_send_message(ctx, new_content)
            except Exception as e:
                logger.error(f"Error editing message: {e}")
                await ctx.send(f"✅ Vote enregistré pour {provider_id}")

        # Log vote counts
        vote_counts = self.vote_manager.count_votes()
        logger.info(f"Total votes: {vote_counts}")

        await send_success(button_ctx.ctx, "Vote enregistré avec succès")

    async def _handle_vote_timeout(
        self,
        ctx: SlashContext,
        message_info,
        question: str,
        responses: list[dict],
    ) -> None:
        """Handle vote timeout by revealing model names."""
        try:
            formatted_responses = "\n\n".join(
                f"**Réponse {i + 1} ({self._get_model_display_name(r['custom_id'])}) :** "
                f"\n> {r['content'].replace(chr(10), chr(10) + '> ')}"
                for i, r in enumerate(responses)
            )
            timeout_content = (
                f"**{ctx.author.mention} : {question}**\n\n"
                f"{formatted_responses}\n\n"
                f"⏰ *Temps de vote expiré*"
            )

            if len(timeout_content) <= 2000:
                await message_info.edit(content=timeout_content, components=[])
            else:
                await message_info.delete()
                await self._split_and_send_message(ctx, timeout_content)

            logger.info("Vote timeout - buttons removed and models revealed")
        except Exception as e:
            logger.error(f"Error handling timeout: {e}")
