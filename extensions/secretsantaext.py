import json
import os
import random
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    Embed,
    Extension,
    IntegrationType,
    Member,
    OptionType,
    Permissions,
    SlashContext,
    User,
    component_callback,
    slash_command,
    slash_option,
    spread_to_rows,
)

from src import logutil
from src.config_manager import load_config, load_discord2name
from src.helpers import Colors, fetch_user_safe, send_error
from src.mongodb import mongo_manager

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleSecretSanta")

# Data directory setup (kept only for human-readable draw files)
DATA_DIR = Path("data/secret_santa")
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SecretSantaSession:
    """Represents an active Secret Santa session."""

    context_id: str
    channel_id: int
    message_id: int | None = None
    created_at: str = ""
    created_by: int = 0
    participants: list[int] = None
    is_drawn: bool = False
    budget: str | None = None
    deadline: str | None = None

    def __post_init__(self):
        if self.participants is None:
            self.participants = []
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class SecretSantaExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot

    def get_context_id(self, ctx: SlashContext | ComponentContext) -> str:
        """Get a unique identifier for the context (guild or channel for private groups)."""
        if ctx.guild:
            return f"guild_{ctx.guild.id}"
        return f"channel_{ctx.channel.id}"

    def _get_collections(self, context_id: str):
        """Return (sessions_col, draw_results_col, banned_pairs_col) for the given context.

        Guild contexts  → per-guild database ``guild_{guild_id}``
        Channel contexts → global database (DMs / group DMs)
        """
        if context_id.startswith("guild_"):
            guild_id = context_id.removeprefix("guild_")
            db = mongo_manager.get_guild_db(guild_id)
        else:
            db = mongo_manager.global_db
        return (
            db["secret_santa_sessions"],
            db["secret_santa_draw_results"],
            db["secret_santa_banned_pairs"],
        )

    # ========== Session Management ==========

    async def get_session(self, context_id: str) -> SecretSantaSession | None:
        """Get a session by context ID."""
        sessions_col, _, _ = self._get_collections(context_id)
        doc = await sessions_col.find_one({"_id": context_id})
        if doc:
            doc["context_id"] = doc.pop("_id")
            return SecretSantaSession(**doc)
        return None

    async def save_session(self, session: SecretSantaSession) -> None:
        """Save a session."""
        sessions_col, _, _ = self._get_collections(session.context_id)
        data = asdict(session)
        data["_id"] = data.pop("context_id")
        await sessions_col.update_one({"_id": data["_id"]}, {"$set": data}, upsert=True)
        logger.info(f"Session saved for {session.context_id}")

    async def delete_session(self, context_id: str) -> bool:
        """Delete a session. Returns True if session existed."""
        sessions_col, _, _ = self._get_collections(context_id)
        result = await sessions_col.delete_one({"_id": context_id})
        if result.deleted_count > 0:
            logger.info(f"Session deleted for {context_id}")
            return True
        return False

    # ========== Banned Pairs ==========

    async def read_banned_pairs(self, context_id: str) -> list[tuple[int, int]]:
        """Read banned pairs for a context."""
        _, _, banned_pairs_col = self._get_collections(context_id)
        doc = await banned_pairs_col.find_one({"_id": context_id})
        if doc:
            return [tuple(p) for p in doc.get("pairs", [])]
        return []

    async def write_banned_pairs(
        self, context_id: str, banned_pairs: list[tuple[int, int]]
    ) -> None:
        """Write banned pairs for a context."""
        _, _, banned_pairs_col = self._get_collections(context_id)
        await banned_pairs_col.update_one(
            {"_id": context_id},
            {"$set": {"pairs": [list(p) for p in banned_pairs]}},
            upsert=True,
        )
        logger.info(f"Banned pairs updated for {context_id}")

    # ========== Draw Results ==========

    async def save_draw_results(self, context_id: str, draw_results: list[tuple[int, int]]) -> None:
        """Save draw results."""
        _, draw_results_col, _ = self._get_collections(context_id)
        await draw_results_col.update_one(
            {"_id": context_id},
            {
                "$set": {
                    "results": [list(p) for p in draw_results],
                    "drawn_at": datetime.now().isoformat(),
                }
            },
            upsert=True,
        )
        logger.info(f"Draw results saved for {context_id}")

    async def get_draw_results(self, context_id: str) -> list[tuple[int, int]] | None:
        """Get draw results for a context."""
        _, draw_results_col, _ = self._get_collections(context_id)
        doc = await draw_results_col.find_one({"_id": context_id})
        if doc:
            return [tuple(p) for p in doc.get("results", [])]
        return None

    async def save_human_readable_draw(
        self,
        context_id: str,
        assignments: list[tuple[int, int]],
        session: SecretSantaSession,
        context_name: str,
    ) -> None:
        """Save a human-readable text file with the draw results."""
        draw_file = (
            DATA_DIR
            / f"draw_{context_id.replace(':', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

        lines = [
            "=" * 50,
            "🎅 PÈRE NOËL SECRET - RÉSULTATS DU TIRAGE 🎅",
            "=" * 50,
            "",
            f"📅 Date du tirage : {datetime.now().strftime('%d/%m/%Y à %H:%M')}",
            f"📍 Contexte : {context_name}",
            f"👥 Nombre de participants : {len(assignments)}",
        ]

        if session.budget:
            lines.append(f"💰 Budget suggéré : {session.budget}")
        if session.deadline:
            lines.append(f"📆 Date limite : {session.deadline}")

        lines.extend(
            [
                "",
                "-" * 50,
                "ATTRIBUTIONS :",
                "-" * 50,
                "",
            ]
        )

        for giver_id, receiver_id in assignments:
            _, giver = await fetch_user_safe(self.bot, giver_id)
            giver_name = (
                f"{giver.display_name} (@{giver.username})" if giver else f"Utilisateur #{giver_id}"
            )

            _, receiver = await fetch_user_safe(self.bot, receiver_id)
            receiver_name = (
                f"{receiver.display_name} (@{receiver.username})"
                if receiver
                else f"Utilisateur #{receiver_id}"
            )

            lines.append(f"  🎁 {giver_name}  →  {receiver_name}")

        lines.extend(
            [
                "",
                "=" * 50,
                "Fichier généré automatiquement par Michel Bot",
                "=" * 50,
            ]
        )

        draw_file.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Human-readable draw results saved to {draw_file}")

    # ========== Assignment Logic ==========

    def is_valid_assignment(
        self, giver: int, receiver: int, banned_pairs: list[tuple[int, int]]
    ) -> bool:
        """Check if an assignment is valid (not in banned pairs)."""
        return not any(
            (giver == p1 and receiver == p2) or (giver == p2 and receiver == p1)
            for p1, p2 in banned_pairs
        )

    def _build_valid_receivers(
        self, participant_ids: list[int], banned_pairs: list[tuple[int, int]]
    ) -> dict[int, list[int]]:
        """Build a dict of valid receivers for each giver."""
        valid_receivers = {}
        for giver in participant_ids:
            valid_receivers[giver] = [
                receiver
                for receiver in participant_ids
                if receiver != giver and self.is_valid_assignment(giver, receiver, banned_pairs)
            ]
        return valid_receivers

    def _backtrack_assign(
        self,
        givers: list[int],
        index: int,
        assignments: dict[int, int],
        available_receivers: set,
        valid_receivers: dict[int, list[int]],
    ) -> bool:
        """Backtracking algorithm to find valid assignments."""
        if index == len(givers):
            return True

        giver = givers[index]
        # Get valid receivers that are still available, sorted by fewest options first (MRV heuristic)
        candidates = [r for r in valid_receivers[giver] if r in available_receivers]

        # Shuffle to add randomness while still using smart ordering
        random.shuffle(candidates)

        for receiver in candidates:
            assignments[giver] = receiver
            available_receivers.remove(receiver)

            # Forward checking: ensure remaining givers still have valid options
            if self._has_valid_future(givers, index + 1, available_receivers, valid_receivers):
                if self._backtrack_assign(
                    givers, index + 1, assignments, available_receivers, valid_receivers
                ):
                    return True

            # Backtrack
            available_receivers.add(receiver)
            del assignments[giver]

        return False

    def _has_valid_future(
        self,
        givers: list[int],
        start_index: int,
        available_receivers: set,
        valid_receivers: dict[int, list[int]],
    ) -> bool:
        """Forward checking: verify all future givers have at least one valid receiver."""
        for i in range(start_index, len(givers)):
            giver = givers[i]
            if not any(r in available_receivers for r in valid_receivers[giver]):
                return False
        return True

    def generate_valid_assignments(
        self, participant_ids: list[int], banned_pairs: list[tuple[int, int]]
    ) -> list[tuple[int, int]] | None:
        """
        Generate valid Secret Santa assignments using a smart backtracking algorithm.

        Uses:
        - Constraint propagation to build valid receiver lists
        - MRV (Minimum Remaining Values) heuristic to order givers
        - Forward checking to prune early
        - Randomization for variety
        """
        if len(participant_ids) < 2:
            return None

        # Build valid receivers for each participant
        valid_receivers = self._build_valid_receivers(participant_ids, banned_pairs)

        # Check if solution is even possible (each person must have at least one valid receiver)
        for giver, receivers in valid_receivers.items():
            if not receivers:
                logger.warning(f"No valid receivers for participant {giver}")
                return None

        # Sort givers by number of valid receivers (MRV heuristic) - most constrained first
        # Add randomization among equal constraints for variety
        givers = participant_ids.copy()
        random.shuffle(givers)  # Shuffle first for randomness
        givers.sort(key=lambda g: len(valid_receivers[g]))  # Then sort by constraint level

        assignments: dict[int, int] = {}
        available_receivers = set(participant_ids)

        if self._backtrack_assign(givers, 0, assignments, available_receivers, valid_receivers):
            # Convert to list of tuples
            return [(giver, assignments[giver]) for giver in participant_ids]

        return None

    def generate_assignments_with_subgroups(
        self, participant_ids: list[int], banned_pairs: list[tuple[int, int]]
    ) -> tuple[list[tuple[int, int]], int] | None:
        """
        Generate Secret Santa assignments allowing multiple subgroups (cycles).

        Returns a tuple of (assignments, number_of_subgroups) or None if impossible.
        Each subgroup forms its own gift-giving cycle.
        """
        if len(participant_ids) < 2:
            return None

        # Build valid receivers for each participant
        valid_receivers = self._build_valid_receivers(participant_ids, banned_pairs)

        # Check if solution is even possible
        for giver, receivers in valid_receivers.items():
            if not receivers:
                logger.warning(f"No valid receivers for participant {giver}")
                return None

        assignments: dict[int, int] = {}
        remaining = set(participant_ids)
        subgroups = 0

        while remaining:
            # Start a new subgroup with a random participant
            subgroup_start = random.choice(list(remaining))
            current = subgroup_start
            subgroup_members = [current]
            remaining.remove(current)

            # Build the cycle
            while True:
                # Find valid next person who hasn't been assigned yet
                candidates = [r for r in valid_receivers[current] if r in remaining]

                if not candidates:
                    # Can we close the loop back to start?
                    if len(subgroup_members) >= 2 and self.is_valid_assignment(
                        current, subgroup_start, banned_pairs
                    ):
                        # Close the cycle
                        assignments[current] = subgroup_start
                        subgroups += 1
                        break
                    else:
                        # Dead end - this approach failed, try rebuilding
                        # For simplicity, we'll use a greedy retry
                        return self._retry_subgroup_assignment(participant_ids, banned_pairs)

                # Pick next person (prefer those with fewer options)
                random.shuffle(candidates)
                candidates.sort(
                    key=lambda c: len(
                        [r for r in valid_receivers[c] if r in remaining or r == subgroup_start]
                    )
                )

                next_person = candidates[0]
                assignments[current] = next_person
                subgroup_members.append(next_person)
                remaining.remove(next_person)
                current = next_person

        # Verify everyone has exactly one giver and one receiver
        if len(assignments) != len(participant_ids):
            return None

        return [(giver, assignments[giver]) for giver in participant_ids], subgroups

    def _retry_subgroup_assignment(
        self, participant_ids: list[int], banned_pairs: list[tuple[int, int]], max_retries: int = 50
    ) -> tuple[list[tuple[int, int]], int] | None:
        """Retry subgroup assignment with different random starts."""
        valid_receivers = self._build_valid_receivers(participant_ids, banned_pairs)

        for _ in range(max_retries):
            assignments: dict[int, int] = {}
            remaining = set(participant_ids)
            subgroups = 0
            success = True

            while remaining and success:
                # Start a new subgroup
                participants_list = list(remaining)
                random.shuffle(participants_list)
                subgroup_start = participants_list[0]
                current = subgroup_start
                subgroup_members = [current]
                remaining.remove(current)

                while True:
                    candidates = [r for r in valid_receivers[current] if r in remaining]

                    if not candidates:
                        if len(subgroup_members) >= 2 and self.is_valid_assignment(
                            current, subgroup_start, banned_pairs
                        ):
                            assignments[current] = subgroup_start
                            subgroups += 1
                            break
                        else:
                            success = False
                            break

                    random.shuffle(candidates)
                    next_person = candidates[0]
                    assignments[current] = next_person
                    subgroup_members.append(next_person)
                    remaining.remove(next_person)
                    current = next_person

            if success and len(assignments) == len(participant_ids):
                return [(giver, assignments[giver]) for giver in participant_ids], subgroups

        return None

    def _create_join_buttons(self, context_id: str, disabled: bool = False) -> list[ActionRow]:
        """Create join/leave buttons for the session."""
        return spread_to_rows(
            Button(
                style=ButtonStyle.SUCCESS,
                label="Participer 🎁",
                custom_id=f"secretsanta_join:{context_id}",
                disabled=disabled,
            ),
            Button(
                style=ButtonStyle.DANGER,
                label="Se retirer",
                custom_id=f"secretsanta_leave:{context_id}",
                disabled=disabled,
            ),
        )

    # ========== Slash Commands ==========

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    async def secret_santa(self, ctx: SlashContext) -> None:
        pass

    @secret_santa.subcommand(
        sub_cmd_name="create",
        sub_cmd_description="Crée une nouvelle session de Père Noël Secret",
    )
    @slash_option(
        name="budget",
        description="Budget suggéré pour les cadeaux (ex: '20€')",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="deadline",
        description="Date limite pour l'échange (ex: '25 décembre')",
        required=False,
        opt_type=OptionType.STRING,
    )
    async def create_session(
        self, ctx: SlashContext, budget: str | None = None, deadline: str | None = None
    ) -> None:
        context_id = self.get_context_id(ctx)
        existing = await self.get_session(context_id)

        if existing and not existing.is_drawn:
            await send_error(
                ctx,
                "Une session de Père Noël Secret est déjà en cours !\nUtilisez `/secretsanta cancel` pour l'annuler d'abord.",
            )
            return

        # Create session
        session = SecretSantaSession(
            context_id=context_id,
            channel_id=ctx.channel.id,
            created_by=ctx.author.id,
            budget=budget,
            deadline=deadline,
        )

        # Build description
        description = (
            "🎄 **Une session de Père Noël Secret a été créée !** 🎄\n\n"
            "Cliquez sur **Participer** pour rejoindre le tirage au sort.\n"
            "Vous pouvez vous retirer à tout moment avant le tirage.\n\n"
        )

        if budget:
            description += f"💰 **Budget suggéré :** {budget}\n"
        if deadline:
            description += f"📅 **Date limite :** {deadline}\n"

        # Only show participant count in guilds (can be updated there)
        if ctx.guild:
            description += "\n**Participants (0) :**\n*Aucun participant pour le moment*"
        else:
            description += "\nUtilisez `/secretsanta participants` pour voir la liste des inscrits."

        embed = Embed(
            title="🎅 Père Noël Secret", description=description, color=Colors.SECRET_SANTA_SUCCESS
        )

        msg = await ctx.send(embed=embed, components=self._create_join_buttons(context_id))

        session.message_id = msg.id
        await self.save_session(session)

        logger.info(f"Secret Santa session created by {ctx.author.id} in {context_id}")

    @secret_santa.subcommand(
        sub_cmd_name="participants",
        sub_cmd_description="Affiche la liste des participants",
    )
    async def list_participants(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        session = await self.get_session(context_id)

        if not session:
            await send_error(ctx, "Il n'y a pas de session de Père Noël Secret en cours.")
            return

        if not session.participants:
            description = "*Aucun participant pour le moment*"
        else:
            participant_mentions = []
            for user_id in session.participants:
                _, user = await fetch_user_safe(self.bot, user_id)
                participant_mentions.append(user.mention if user else f"<@{user_id}>")

            description = "\n".join(f"• {m}" for m in participant_mentions)

        status = "✅ Tirage effectué" if session.is_drawn else "⏳ En attente du tirage"

        embed = Embed(
            title=f"🎅 Participants ({len(session.participants)})",
            description=f"**Statut :** {status}\n\n{description}",
            color=Colors.SECRET_SANTA,
        )

        if session.budget:
            embed.add_field(name="💰 Budget", value=session.budget, inline=True)
        if session.deadline:
            embed.add_field(name="📅 Date limite", value=session.deadline, inline=True)

        await ctx.send(embed=embed, ephemeral=True)

    @secret_santa.subcommand(
        sub_cmd_name="draw",
        sub_cmd_description="Effectue le tirage au sort",
    )
    @slash_option(
        name="allow_subgroups",
        description="Autoriser plusieurs sous-groupes si une boucle unique est impossible",
        required=False,
        opt_type=OptionType.BOOLEAN,
    )
    async def draw(self, ctx: SlashContext, allow_subgroups: bool = False) -> None:
        await ctx.defer(ephemeral=True)

        context_id = self.get_context_id(ctx)
        session = await self.get_session(context_id)

        if not session:
            await send_error(
                ctx,
                "Il n'y a pas de session de Père Noël Secret en cours.\nCréez-en une avec `/secretsanta create`",
            )
            return

        if session.is_drawn:
            await send_error(ctx, "Le tirage au sort a déjà été effectué pour cette session !")
            return

        if len(session.participants) < 2:
            await send_error(
                ctx,
                f"Il faut au moins 2 participants pour le tirage.\nParticipants actuels : {len(session.participants)}",
            )
            return

        # Generate assignments
        banned_pairs = await self.read_banned_pairs(context_id)

        assignments = None
        num_subgroups = 1

        # First, try single loop
        assignments = self.generate_valid_assignments(session.participants, banned_pairs)

        if not assignments and allow_subgroups:
            # Try with subgroups
            result = self.generate_assignments_with_subgroups(session.participants, banned_pairs)
            if result:
                assignments, num_subgroups = result
                logger.info(f"Draw completed with {num_subgroups} subgroup(s) for {context_id}")

        if not assignments:
            error_msg = "Impossible de générer un tirage valide avec les restrictions actuelles.\n"
            if not allow_subgroups:
                error_msg += "\n💡 **Astuce :** Essayez avec l'option `allow_subgroups: True` pour autoriser plusieurs sous-groupes."
            error_msg += "\nVérifiez les paires interdites avec `/secretsanta listbans`"

            await send_error(ctx, error_msg)
            return

        # Send DMs to participants
        server = str(ctx.guild.id) if ctx.guild else None
        discord2name_data = load_discord2name(server) if server else {}

        failed_dms = []
        for giver_id, receiver_id in assignments:
            try:
                _, giver = await fetch_user_safe(self.bot, giver_id)
                _, receiver = await fetch_user_safe(self.bot, receiver_id)

                receiver_name = discord2name_data.get(
                    str(receiver_id), receiver.mention if receiver else f"<@{receiver_id}>"
                )

                dm_embed = Embed(
                    title="🎅 Père Noël Secret",
                    description=(
                        f"🎄 Ho, ho, ho ! C'est le Père Noël ! 🎄\n\n"
                        f"Cette année, tu dois offrir un cadeau à **{receiver_name}** !\n"
                        f"À toi de voir s'il/elle a été sage... 😉\n\n"
                        + (f"💰 **Budget suggéré :** {session.budget}\n" if session.budget else "")
                        + (f"📅 **Date limite :** {session.deadline}\n" if session.deadline else "")
                        + "\n*Signé : Le vrai Père Noël* 🎅"
                    ),
                    color=Colors.SECRET_SANTA,
                )

                await giver.send(embed=dm_embed)
            except Exception as e:
                logger.error(f"Failed to send DM to {giver_id}: {e}")
                failed_dms.append(giver_id)

        # Save results and update session
        await self.save_draw_results(context_id, assignments)
        session.is_drawn = True
        await self.save_session(session)

        # Save human-readable file
        context_name = ctx.guild.name if ctx.guild else f"DM Group {ctx.channel.id}"
        await self.save_human_readable_draw(context_id, assignments, session, context_name)

        # Build participant mentions for the announcement
        participant_mentions = []
        for uid in session.participants:
            _, user = await fetch_user_safe(self.bot, uid)
            participant_mentions.append(user.mention if user else f"<@{uid}>")

        draw_embed = Embed(
            title="🎅 Tirage effectué ! 🎉",
            description=(
                f"Le tirage au sort a été effectué pour **{len(session.participants)}** participants !\n\n"
                f"**Participants :**\n" + "\n".join(f"• {m}" for m in participant_mentions) + "\n\n"
                "Vérifiez vos messages privés pour découvrir qui vous devez gâter ! 🎁"
            ),
            color=Colors.SECRET_SANTA,
        )

        if ctx.guild:
            # In guilds, update the original message
            if session.message_id:
                try:
                    channel = self.bot.get_channel(session.channel_id)
                    if channel:
                        message = await channel.fetch_message(session.message_id)
                        await message.edit(
                            embed=draw_embed,
                            components=self._create_join_buttons(context_id, disabled=True),
                        )
                except Exception as e:
                    logger.error(f"Failed to update session message: {e}")
        else:
            # In DM groups, send a new message (can't edit original message)
            try:
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    await channel.send(embed=draw_embed)
            except Exception as e:
                logger.error(f"Failed to send draw announcement in DM group: {e}")

        # Response
        response_msg = (
            f"🎉 Le tirage a été effectué pour {len(session.participants)} participants !"
        )
        if num_subgroups > 1:
            response_msg += f"\n\n🔄 **{num_subgroups} sous-groupes** ont été formés (les contraintes empêchaient une boucle unique)."
        if failed_dms:
            failed_mentions = [f"<@{uid}>" for uid in failed_dms]
            response_msg += f"\n\n⚠️ Impossible d'envoyer un DM à : {', '.join(failed_mentions)}"

        await ctx.send(
            embed=Embed(
                title="🎅 Tirage effectué", description=response_msg, color=Colors.SECRET_SANTA
            ),
            ephemeral=True,
        )

    @secret_santa.subcommand(
        sub_cmd_name="cancel",
        sub_cmd_description="Annule la session en cours",
    )
    async def cancel_session(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        session = await self.get_session(context_id)

        if not session:
            await send_error(ctx, "Il n'y a pas de session à annuler.")
            return

        # Only creator or admin can cancel
        is_creator = ctx.author.id == session.created_by
        is_admin = ctx.author.has_permission(Permissions.ADMINISTRATOR) if ctx.guild else False

        if not is_creator and not is_admin:
            await send_error(
                ctx, "Seul le créateur de la session ou un administrateur peut l'annuler."
            )
            return

        # Update original message (only in guilds, not in group DMs)
        if session.message_id and ctx.guild:
            try:
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    message = await channel.fetch_message(session.message_id)
                    embed = Embed(
                        title="🎅 Session annulée",
                        description="Cette session de Père Noël Secret a été annulée.",
                        color=Colors.SECRET_SANTA_ACCENT,
                    )
                    await message.edit(embed=embed, components=[])
            except Exception as e:
                logger.error(f"Failed to update cancelled session message: {e}")

        await self.delete_session(context_id)

        await ctx.send(
            embed=Embed(
                title="🎅 Session annulée",
                description="La session de Père Noël Secret a été annulée avec succès.",
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @secret_santa.subcommand(
        sub_cmd_name="reveal",
        sub_cmd_description="Révèle les attributions (admin uniquement)",
    )
    async def reveal_assignments(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)

        # Check permissions
        is_admin = ctx.author.has_permission(Permissions.ADMINISTRATOR) if ctx.guild else False
        if not is_admin and ctx.guild:
            await send_error(ctx, "Seul un administrateur peut révéler les attributions.")
            return

        results = await self.get_draw_results(context_id)

        if not results:
            await send_error(ctx, "Aucun tirage n'a été effectué pour cette session.")
            return

        description = "**Attributions :**\n\n"
        for giver_id, receiver_id in results:
            _, giver = await fetch_user_safe(self.bot, giver_id)
            _, receiver = await fetch_user_safe(self.bot, receiver_id)
            giver_mention = giver.mention if giver else f"<@{giver_id}>"
            receiver_mention = receiver.mention if receiver else f"<@{receiver_id}>"
            description += f"• {giver_mention} → {receiver_mention}\n"

        await ctx.send(
            embed=Embed(
                title="🎅 Révélation des attributions",
                description=description,
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @secret_santa.subcommand(
        sub_cmd_name="remind",
        sub_cmd_description="Renvoie votre attribution par DM",
    )
    async def remind_assignment(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        results = await self.get_draw_results(context_id)

        if not results:
            await send_error(ctx, "Aucun tirage n'a été effectué pour cette session.")
            return

        # Find user's assignment
        user_assignment = None
        for giver_id, receiver_id in results:
            if giver_id == ctx.author.id:
                user_assignment = receiver_id
                break

        if not user_assignment:
            await send_error(ctx, "Vous n'avez pas participé à ce tirage.")
            return

        session = await self.get_session(context_id)
        server = str(ctx.guild.id) if ctx.guild else None
        discord2name_data = load_discord2name(server) if server else {}

        try:
            _, receiver = await fetch_user_safe(self.bot, user_assignment)
            receiver_name = discord2name_data.get(
                str(user_assignment), receiver.mention if receiver else f"<@{user_assignment}>"
            )

            dm_embed = Embed(
                title="🎅 Rappel - Père Noël Secret",
                description=(
                    f"🎄 Rappel : Tu dois offrir un cadeau à **{receiver_name}** ! 🎁\n\n"
                    + (
                        f"💰 **Budget suggéré :** {session.budget}\n"
                        if session and session.budget
                        else ""
                    )
                    + (
                        f"📅 **Date limite :** {session.deadline}\n"
                        if session and session.deadline
                        else ""
                    )
                ),
                color=Colors.SECRET_SANTA,
            )

            await ctx.author.send(embed=dm_embed)
            await ctx.send(
                embed=Embed(
                    title="🎅 Rappel envoyé",
                    description="Vérifiez vos messages privés ! 📬",
                    color=Colors.SECRET_SANTA,
                ),
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Failed to send reminder DM: {e}")
            await send_error(
                ctx, "Impossible d'envoyer le message privé. Vérifiez que vos DMs sont ouverts."
            )

    # ========== Banned Pairs Commands ==========

    @secret_santa.subcommand(
        sub_cmd_name="banpair",
        sub_cmd_description="Interdit deux utilisateurs de se tirer mutuellement",
    )
    @slash_option(
        name="user1",
        description="Premier utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="user2",
        description="Deuxième utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    async def ban_pair(self, ctx: SlashContext, user1: Member | User, user2: Member | User) -> None:
        if user1.id == user2.id:
            await send_error(ctx, "Vous ne pouvez pas bannir un utilisateur avec lui-même.")
            return

        context_id = self.get_context_id(ctx)
        banned_pairs = await self.read_banned_pairs(context_id)

        # Check if pair already exists
        for p1, p2 in banned_pairs:
            if (user1.id == p1 and user2.id == p2) or (user1.id == p2 and user2.id == p1):
                await send_error(
                    ctx, "Ces utilisateurs sont déjà interdits de se tirer mutuellement."
                )
                return

        banned_pairs.append((user1.id, user2.id))
        await self.write_banned_pairs(context_id, banned_pairs)

        await ctx.send(
            embed=Embed(
                title="🎅 Paire interdite",
                description=f"{user1.mention} et {user2.mention} ne pourront pas se tirer mutuellement.",
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @secret_santa.subcommand(
        sub_cmd_name="unbanpair",
        sub_cmd_description="Autorise à nouveau deux utilisateurs à se tirer mutuellement",
    )
    @slash_option(
        name="user1",
        description="Premier utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="user2",
        description="Deuxième utilisateur",
        required=True,
        opt_type=OptionType.USER,
    )
    async def unban_pair(
        self, ctx: SlashContext, user1: Member | User, user2: Member | User
    ) -> None:
        context_id = self.get_context_id(ctx)
        banned_pairs = await self.read_banned_pairs(context_id)

        new_pairs = [
            (p1, p2)
            for p1, p2 in banned_pairs
            if not ((user1.id == p1 and user2.id == p2) or (user1.id == p2 and user2.id == p1))
        ]

        if len(new_pairs) == len(banned_pairs):
            await send_error(
                ctx, "Ces utilisateurs ne sont pas interdits de se tirer mutuellement."
            )
            return

        await self.write_banned_pairs(context_id, new_pairs)

        await ctx.send(
            embed=Embed(
                title="🎅 Paire autorisée",
                description=f"{user1.mention} et {user2.mention} peuvent à nouveau se tirer mutuellement.",
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @secret_santa.subcommand(
        sub_cmd_name="listbans",
        sub_cmd_description="Liste les paires d'utilisateurs interdites",
    )
    async def list_bans(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        banned_pairs = await self.read_banned_pairs(context_id)

        if not banned_pairs:
            await ctx.send(
                embed=Embed(
                    title="🎅 Aucune restriction",
                    description="Aucune paire d'utilisateurs n'est interdite.",
                    color=Colors.SECRET_SANTA,
                ),
                ephemeral=True,
            )
            return

        description = ""
        for user1_id, user2_id in banned_pairs:
            _, user1 = await fetch_user_safe(self.bot, user1_id)
            _, user2 = await fetch_user_safe(self.bot, user2_id)
            user1_mention = user1.mention if user1 else f"<@{user1_id}>"
            user2_mention = user2.mention if user2 else f"<@{user2_id}>"
            description += f"• {user1_mention} ↔ {user2_mention}\n"

        await ctx.send(
            embed=Embed(
                title="🎅 Paires interdites",
                description=description,
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    # ========== Button Callbacks ==========

    @component_callback(re.compile(r"secretsanta_join:(.+)"))
    async def on_join_button(self, ctx: ComponentContext) -> None:
        # Extract context_id from custom_id
        context_id = ctx.custom_id.split(":", 1)[1]
        logger.debug(f"Join button clicked by {ctx.author.id} for session {context_id}")
        session = await self.get_session(context_id)

        if not session:
            logger.warning(f"Session not found for {context_id}")
            await self._send_response(ctx, session, "Cette session n'existe plus.")
            return

        if session.is_drawn:
            await self._send_response(ctx, session, "Le tirage a déjà été effectué !")
            return

        if ctx.author.id in session.participants:
            await self._send_response(ctx, session, "Vous participez déjà ! 🎅")
            return

        session.participants.append(ctx.author.id)
        await self.save_session(session)
        logger.info(
            f"User {ctx.author.id} ({ctx.author.username}) joined Secret Santa in {context_id} (total: {len(session.participants)})"
        )

        # Update message
        await self._update_session_message(ctx, session)
        await self._send_response(ctx, session, "Vous êtes inscrit au Père Noël Secret ! 🎁")

    @component_callback(re.compile(r"secretsanta_leave:(.+)"))
    async def on_leave_button(self, ctx: ComponentContext) -> None:
        # Extract context_id from custom_id
        context_id = ctx.custom_id.split(":", 1)[1]
        logger.debug(f"Leave button clicked by {ctx.author.id} for session {context_id}")
        session = await self.get_session(context_id)

        if not session:
            await self._send_response(ctx, session, "Cette session n'existe plus.")
            return

        if session.is_drawn:
            await self._send_response(
                ctx, session, "Le tirage a déjà été effectué, vous ne pouvez plus vous retirer."
            )
            return

        if ctx.author.id not in session.participants:
            await self._send_response(ctx, session, "Vous ne participez pas à cette session.")
            return

        session.participants.remove(ctx.author.id)
        await self.save_session(session)
        logger.info(
            f"User {ctx.author.id} ({ctx.author.username}) left Secret Santa in {context_id} (total: {len(session.participants)})"
        )

        # Update message
        await self._update_session_message(ctx, session)
        await self._send_response(ctx, session, "Vous avez été retiré du Père Noël Secret.")

    async def _send_response(
        self, ctx: ComponentContext, session: SecretSantaSession | None, message: str
    ) -> None:
        """
        Send a response to the user. In DM groups, ephemeral messages only work for the session creator,
        so we send a DM to other users instead.
        """
        # In guilds, always use ephemeral
        if ctx.guild:
            await ctx.send(message, ephemeral=True)
            return

        # In DM groups: ephemeral only works for the session creator
        # For others, we need to send a DM
        if session and ctx.author.id == session.created_by:
            await ctx.send(message, ephemeral=True)
        else:
            try:
                # Send a DM to the user
                await ctx.author.send(f"🎅 **Père Noël Secret**\n{message}")
                # Still need to acknowledge the interaction to avoid "interaction failed"
                await ctx.defer(edit_origin=True)
            except Exception as e:
                logger.error(f"Failed to send DM to {ctx.author.id}: {e}")
                # Fallback: try ephemeral anyway (might fail but at least we tried)
                try:
                    await ctx.send(message, ephemeral=True)
                except Exception:
                    pass

    async def _update_session_message(
        self, ctx: ComponentContext, session: SecretSantaSession
    ) -> None:
        """Update the session message with current participants."""
        # Skip updating message in group DMs (bots can't edit messages there)
        if not ctx.guild:
            return

        try:
            # Build participant list
            if not session.participants:
                participant_text = "*Aucun participant pour le moment*"
            else:
                participant_mentions = []
                for user_id in session.participants:
                    _, user = await fetch_user_safe(self.bot, user_id)
                    participant_mentions.append(user.mention if user else f"<@{user_id}>")
                participant_text = "\n".join(f"• {m}" for m in participant_mentions)

            description = (
                "🎄 **Une session de Père Noël Secret est en cours !** 🎄\n\n"
                "Cliquez sur **Participer** pour rejoindre le tirage au sort.\n"
                "Vous pouvez vous retirer à tout moment avant le tirage.\n\n"
            )

            if session.budget:
                description += f"💰 **Budget suggéré :** {session.budget}\n"
            if session.deadline:
                description += f"📅 **Date limite :** {session.deadline}\n"

            description += f"\n**Participants ({len(session.participants)}) :**\n{participant_text}"

            embed = Embed(
                title="🎅 Père Noël Secret",
                description=description,
                color=Colors.SECRET_SANTA_SUCCESS,
            )

            await ctx.message.edit(
                embed=embed, components=self._create_join_buttons(session.context_id)
            )
        except Exception as e:
            logger.error(f"Failed to update session message: {e}")
