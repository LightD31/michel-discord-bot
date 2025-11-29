import json
import random
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from interactions import (
    Extension, Client, BrandColors, Embed, OptionType, ComponentContext,
    SlashContext, slash_command, slash_option, Member, User, Button,
    ButtonStyle, ActionRow, component_callback, Permissions, spread_to_rows,
    IntegrationType
)

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(__name__)
config, module_config, enabled_servers = load_config("moduleSecretSanta")

# Data directory setup
DATA_DIR = Path("data/secret_santa")
DATA_DIR.mkdir(parents=True, exist_ok=True)

SESSIONS_FILE = DATA_DIR / "sessions.json"
DRAW_RESULTS_FILE = DATA_DIR / "draw_results.json"
BANNED_PAIRS_FILE = DATA_DIR / "banned_pairs.json"

discord2name = config.get("discord2name", {})


@dataclass
class SecretSantaSession:
    """Represents an active Secret Santa session."""
    context_id: str
    channel_id: int
    message_id: Optional[int] = None
    created_at: str = ""
    created_by: int = 0
    participants: List[int] = None
    is_drawn: bool = False
    budget: Optional[str] = None
    deadline: Optional[str] = None
    
    def __post_init__(self):
        if self.participants is None:
            self.participants = []
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class SecretSanta(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        self._ensure_data_files()

    def _ensure_data_files(self) -> None:
        """Ensure all data files exist."""
        for file_path in [SESSIONS_FILE, DRAW_RESULTS_FILE, BANNED_PAIRS_FILE]:
            if not file_path.exists():
                file_path.write_text("{}", encoding="utf-8")

    def _read_json(self, file_path: Path) -> dict:
        """Read JSON data from file."""
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_json(self, file_path: Path, data: dict) -> None:
        """Write JSON data to file."""
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_context_id(self, ctx: Union[SlashContext, ComponentContext]) -> str:
        """Get a unique identifier for the context (guild or channel for private groups)."""
        if ctx.guild:
            return f"guild_{ctx.guild.id}"
        return f"channel_{ctx.channel.id}"

    def create_embed(self, title: str, message: str, color=BrandColors.RED) -> Embed:
        """Create a themed embed."""
        return Embed(
            title=f"🎅 {title}",
            description=message,
            color=color,
        )

    # ========== Session Management ==========
    
    def get_session(self, context_id: str) -> Optional[SecretSantaSession]:
        """Get a session by context ID."""
        data = self._read_json(SESSIONS_FILE)
        session_data = data.get(context_id)
        if session_data:
            return SecretSantaSession(**session_data)
        return None

    def save_session(self, session: SecretSantaSession) -> None:
        """Save a session."""
        data = self._read_json(SESSIONS_FILE)
        data[session.context_id] = asdict(session)
        self._write_json(SESSIONS_FILE, data)
        logger.info(f"Session saved for {session.context_id}")

    def delete_session(self, context_id: str) -> bool:
        """Delete a session. Returns True if session existed."""
        data = self._read_json(SESSIONS_FILE)
        if context_id in data:
            del data[context_id]
            self._write_json(SESSIONS_FILE, data)
            logger.info(f"Session deleted for {context_id}")
            return True
        return False

    # ========== Banned Pairs ==========
    
    def read_banned_pairs(self, context_id: str) -> List[Tuple[int, int]]:
        """Read banned pairs for a context."""
        data = self._read_json(BANNED_PAIRS_FILE)
        pairs = data.get(context_id, [])
        return [tuple(p) for p in pairs]

    def write_banned_pairs(self, context_id: str, banned_pairs: List[Tuple[int, int]]) -> None:
        """Write banned pairs for a context."""
        data = self._read_json(BANNED_PAIRS_FILE)
        data[context_id] = [list(p) for p in banned_pairs]
        self._write_json(BANNED_PAIRS_FILE, data)
        logger.info(f"Banned pairs updated for {context_id}")

    # ========== Draw Results ==========
    
    def save_draw_results(self, context_id: str, draw_results: List[Tuple[int, int]]) -> None:
        """Save draw results."""
        data = self._read_json(DRAW_RESULTS_FILE)
        data[context_id] = {
            "results": [list(p) for p in draw_results],
            "drawn_at": datetime.now().isoformat()
        }
        self._write_json(DRAW_RESULTS_FILE, data)
        logger.info(f"Draw results saved for {context_id}")

    def get_draw_results(self, context_id: str) -> Optional[List[Tuple[int, int]]]:
        """Get draw results for a context."""
        data = self._read_json(DRAW_RESULTS_FILE)
        result_data = data.get(context_id)
        if result_data:
            return [tuple(p) for p in result_data.get("results", [])]
        return None

    # ========== Assignment Logic ==========
    
    def is_valid_assignment(self, giver: int, receiver: int, banned_pairs: List[Tuple[int, int]]) -> bool:
        """Check if an assignment is valid (not in banned pairs)."""
        return not any(
            (giver == p1 and receiver == p2) or (giver == p2 and receiver == p1)
            for p1, p2 in banned_pairs
        )

    def _build_valid_receivers(
        self, 
        participant_ids: List[int], 
        banned_pairs: List[Tuple[int, int]]
    ) -> Dict[int, List[int]]:
        """Build a dict of valid receivers for each giver."""
        valid_receivers = {}
        for giver in participant_ids:
            valid_receivers[giver] = [
                receiver for receiver in participant_ids
                if receiver != giver and self.is_valid_assignment(giver, receiver, banned_pairs)
            ]
        return valid_receivers

    def _backtrack_assign(
        self,
        givers: List[int],
        index: int,
        assignments: Dict[int, int],
        available_receivers: set,
        valid_receivers: Dict[int, List[int]]
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
                if self._backtrack_assign(givers, index + 1, assignments, available_receivers, valid_receivers):
                    return True
            
            # Backtrack
            available_receivers.add(receiver)
            del assignments[giver]
        
        return False

    def _has_valid_future(
        self,
        givers: List[int],
        start_index: int,
        available_receivers: set,
        valid_receivers: Dict[int, List[int]]
    ) -> bool:
        """Forward checking: verify all future givers have at least one valid receiver."""
        for i in range(start_index, len(givers)):
            giver = givers[i]
            if not any(r in available_receivers for r in valid_receivers[giver]):
                return False
        return True

    def generate_valid_assignments(
        self, 
        participant_ids: List[int], 
        banned_pairs: List[Tuple[int, int]]
    ) -> Optional[List[Tuple[int, int]]]:
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
        
        assignments: Dict[int, int] = {}
        available_receivers = set(participant_ids)
        
        if self._backtrack_assign(givers, 0, assignments, available_receivers, valid_receivers):
            # Convert to list of tuples
            return [(giver, assignments[giver]) for giver in participant_ids]
        
        return None

    def generate_assignments_with_subgroups(
        self, 
        participant_ids: List[int], 
        banned_pairs: List[Tuple[int, int]]
    ) -> Optional[Tuple[List[Tuple[int, int]], int]]:
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
        
        assignments: Dict[int, int] = {}
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
                candidates = [
                    r for r in valid_receivers[current] 
                    if r in remaining
                ]
                
                if not candidates:
                    # Can we close the loop back to start?
                    if (len(subgroup_members) >= 2 and 
                        self.is_valid_assignment(current, subgroup_start, banned_pairs)):
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
                candidates.sort(key=lambda c: len([r for r in valid_receivers[c] if r in remaining or r == subgroup_start]))
                
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
        self, 
        participant_ids: List[int], 
        banned_pairs: List[Tuple[int, int]],
        max_retries: int = 50
    ) -> Optional[Tuple[List[Tuple[int, int]], int]]:
        """Retry subgroup assignment with different random starts."""
        valid_receivers = self._build_valid_receivers(participant_ids, banned_pairs)
        
        for _ in range(max_retries):
            assignments: Dict[int, int] = {}
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
                        if (len(subgroup_members) >= 2 and 
                            self.is_valid_assignment(current, subgroup_start, banned_pairs)):
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

    def _create_join_buttons(self, context_id: str, disabled: bool = False) -> List[ActionRow]:
        """Create join/leave buttons for the session."""
        return spread_to_rows(
            Button(
                style=ButtonStyle.SUCCESS,
                label="Participer 🎁",
                custom_id=f"secretsanta_join:{context_id}",
                disabled=disabled
            ),
            Button(
                style=ButtonStyle.DANGER,
                label="Se retirer",
                custom_id=f"secretsanta_leave:{context_id}",
                disabled=disabled
            )
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
        self, 
        ctx: SlashContext, 
        budget: Optional[str] = None,
        deadline: Optional[str] = None
    ) -> None:
        context_id = self.get_context_id(ctx)
        existing = self.get_session(context_id)
        
        if existing and not existing.is_drawn:
            await ctx.send(
                embed=self.create_embed(
                    "Session existante",
                    "Une session de Père Noël Secret est déjà en cours !\n"
                    "Utilisez `/secretsanta cancel` pour l'annuler d'abord."
                ),
                ephemeral=True
            )
            return

        # Create session
        session = SecretSantaSession(
            context_id=context_id,
            channel_id=ctx.channel.id,
            created_by=ctx.author.id,
            budget=budget,
            deadline=deadline
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
        
        embed = self.create_embed("Père Noël Secret", description, color=BrandColors.GREEN)
        
        msg = await ctx.send(
            embed=embed,
            components=self._create_join_buttons(context_id)
        )
        
        session.message_id = msg.id
        self.save_session(session)
        
        logger.info(f"Secret Santa session created by {ctx.author.id} in {context_id}")

    @secret_santa.subcommand(
        sub_cmd_name="participants",
        sub_cmd_description="Affiche la liste des participants",
    )
    async def list_participants(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        session = self.get_session(context_id)
        
        if not session:
            await ctx.send(
                embed=self.create_embed(
                    "Aucune session",
                    "Il n'y a pas de session de Père Noël Secret en cours."
                ),
                ephemeral=True
            )
            return

        if not session.participants:
            description = "*Aucun participant pour le moment*"
        else:
            participant_mentions = []
            for user_id in session.participants:
                try:
                    user = await self.bot.fetch_user(user_id)
                    participant_mentions.append(user.mention)
                except Exception:
                    participant_mentions.append(f"<@{user_id}>")
            
            description = "\n".join(f"• {m}" for m in participant_mentions)
        
        status = "✅ Tirage effectué" if session.is_drawn else "⏳ En attente du tirage"
        
        embed = self.create_embed(
            f"Participants ({len(session.participants)})",
            f"**Statut :** {status}\n\n{description}"
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
        session = self.get_session(context_id)
        
        if not session:
            await ctx.send(
                embed=self.create_embed(
                    "Aucune session",
                    "Il n'y a pas de session de Père Noël Secret en cours.\n"
                    "Créez-en une avec `/secretsanta create`"
                ),
                ephemeral=True
            )
            return
        
        if session.is_drawn:
            await ctx.send(
                embed=self.create_embed(
                    "Déjà tiré",
                    "Le tirage au sort a déjà été effectué pour cette session !"
                ),
                ephemeral=True
            )
            return
        
        if len(session.participants) < 2:
            await ctx.send(
                embed=self.create_embed(
                    "Pas assez de participants",
                    f"Il faut au moins 2 participants pour le tirage.\n"
                    f"Participants actuels : {len(session.participants)}"
                ),
                ephemeral=True
            )
            return

        # Generate assignments
        banned_pairs = self.read_banned_pairs(context_id)
        
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
            
            await ctx.send(
                embed=self.create_embed("Échec du tirage", error_msg),
                ephemeral=True
            )
            return

        # Send DMs to participants
        server = str(ctx.guild.id) if ctx.guild else None
        discord2name_data = discord2name.get(server, {}) if server else {}
        
        failed_dms = []
        for giver_id, receiver_id in assignments:
            try:
                giver = await self.bot.fetch_user(giver_id)
                receiver = await self.bot.fetch_user(receiver_id)
                
                receiver_name = discord2name_data.get(str(receiver_id), receiver.mention)
                
                dm_embed = self.create_embed(
                    "Père Noël Secret",
                    f"🎄 Ho, ho, ho ! C'est le Père Noël ! 🎄\n\n"
                    f"Cette année, tu dois offrir un cadeau à **{receiver_name}** !\n"
                    f"À toi de voir s'il/elle a été sage... 😉\n\n"
                    + (f"💰 **Budget suggéré :** {session.budget}\n" if session.budget else "")
                    + (f"📅 **Date limite :** {session.deadline}\n" if session.deadline else "")
                    + "\n*Signé : Le vrai Père Noël* 🎅"
                )
                
                await giver.send(embed=dm_embed)
            except Exception as e:
                logger.error(f"Failed to send DM to {giver_id}: {e}")
                failed_dms.append(giver_id)

        # Save results and update session
        self.save_draw_results(context_id, assignments)
        session.is_drawn = True
        self.save_session(session)

        # Update original message (only in guilds, not in group DMs)
        if session.message_id and ctx.guild:
            try:
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    message = await channel.fetch_message(session.message_id)
                    
                    participant_mentions = []
                    for uid in session.participants:
                        try:
                            user = await self.bot.fetch_user(uid)
                            participant_mentions.append(user.mention)
                        except Exception:
                            participant_mentions.append(f"<@{uid}>")
                    
                    embed = self.create_embed(
                        "Tirage effectué ! 🎉",
                        f"Le tirage au sort a été effectué pour **{len(session.participants)}** participants !\n\n"
                        f"**Participants :**\n" + "\n".join(f"• {m}" for m in participant_mentions) + "\n\n"
                        "Vérifiez vos messages privés pour découvrir qui vous devez gâter ! 🎁"
                    )
                    
                    await message.edit(embed=embed, components=self._create_join_buttons(context_id, disabled=True))
            except Exception as e:
                logger.error(f"Failed to update session message: {e}")

        # Response
        response_msg = f"🎉 Le tirage a été effectué pour {len(session.participants)} participants !"
        if num_subgroups > 1:
            response_msg += f"\n\n🔄 **{num_subgroups} sous-groupes** ont été formés (les contraintes empêchaient une boucle unique)."
        if failed_dms:
            failed_mentions = [f"<@{uid}>" for uid in failed_dms]
            response_msg += f"\n\n⚠️ Impossible d'envoyer un DM à : {', '.join(failed_mentions)}"
        
        await ctx.send(embed=self.create_embed("Tirage effectué", response_msg), ephemeral=True)

    @secret_santa.subcommand(
        sub_cmd_name="cancel",
        sub_cmd_description="Annule la session en cours",
    )
    async def cancel_session(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        session = self.get_session(context_id)
        
        if not session:
            await ctx.send(
                embed=self.create_embed(
                    "Aucune session",
                    "Il n'y a pas de session à annuler."
                ),
                ephemeral=True
            )
            return
        
        # Only creator or admin can cancel
        is_creator = ctx.author.id == session.created_by
        is_admin = ctx.author.has_permission(Permissions.ADMINISTRATOR) if ctx.guild else False
        
        if not is_creator and not is_admin:
            await ctx.send(
                embed=self.create_embed(
                    "Permission refusée",
                    "Seul le créateur de la session ou un administrateur peut l'annuler."
                ),
                ephemeral=True
            )
            return
        
        # Update original message (only in guilds, not in group DMs)
        if session.message_id and ctx.guild:
            try:
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    message = await channel.fetch_message(session.message_id)
                    embed = self.create_embed(
                        "Session annulée",
                        "Cette session de Père Noël Secret a été annulée.",
                        color=BrandColors.FUCHSIA
                    )
                    await message.edit(embed=embed, components=[])
            except Exception as e:
                logger.error(f"Failed to update cancelled session message: {e}")
        
        self.delete_session(context_id)
        
        await ctx.send(
            embed=self.create_embed(
                "Session annulée",
                "La session de Père Noël Secret a été annulée avec succès."
            ),
            ephemeral=True
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
            await ctx.send(
                embed=self.create_embed(
                    "Permission refusée",
                    "Seul un administrateur peut révéler les attributions."
                ),
                ephemeral=True
            )
            return
        
        results = self.get_draw_results(context_id)
        
        if not results:
            await ctx.send(
                embed=self.create_embed(
                    "Aucun tirage",
                    "Aucun tirage n'a été effectué pour cette session."
                ),
                ephemeral=True
            )
            return

        description = "**Attributions :**\n\n"
        for giver_id, receiver_id in results:
            try:
                giver = await self.bot.fetch_user(giver_id)
                receiver = await self.bot.fetch_user(receiver_id)
                description += f"• {giver.mention} → {receiver.mention}\n"
            except Exception:
                description += f"• <@{giver_id}> → <@{receiver_id}>\n"
        
        await ctx.send(
            embed=self.create_embed("Révélation des attributions", description),
            ephemeral=True
        )

    @secret_santa.subcommand(
        sub_cmd_name="remind",
        sub_cmd_description="Renvoie votre attribution par DM",
    )
    async def remind_assignment(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        results = self.get_draw_results(context_id)
        
        if not results:
            await ctx.send(
                embed=self.create_embed(
                    "Aucun tirage",
                    "Aucun tirage n'a été effectué pour cette session."
                ),
                ephemeral=True
            )
            return
        
        # Find user's assignment
        user_assignment = None
        for giver_id, receiver_id in results:
            if giver_id == ctx.author.id:
                user_assignment = receiver_id
                break
        
        if not user_assignment:
            await ctx.send(
                embed=self.create_embed(
                    "Non participant",
                    "Vous n'avez pas participé à ce tirage."
                ),
                ephemeral=True
            )
            return
        
        session = self.get_session(context_id)
        server = str(ctx.guild.id) if ctx.guild else None
        discord2name_data = discord2name.get(server, {}) if server else {}
        
        try:
            receiver = await self.bot.fetch_user(user_assignment)
            receiver_name = discord2name_data.get(str(user_assignment), receiver.mention)
            
            dm_embed = self.create_embed(
                "Rappel - Père Noël Secret",
                f"🎄 Rappel : Tu dois offrir un cadeau à **{receiver_name}** ! 🎁\n\n"
                + (f"💰 **Budget suggéré :** {session.budget}\n" if session and session.budget else "")
                + (f"📅 **Date limite :** {session.deadline}\n" if session and session.deadline else "")
            )
            
            await ctx.author.send(embed=dm_embed)
            await ctx.send(
                embed=self.create_embed("Rappel envoyé", "Vérifiez vos messages privés ! 📬"),
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Failed to send reminder DM: {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Erreur",
                    "Impossible d'envoyer le message privé. Vérifiez que vos DMs sont ouverts."
                ),
                ephemeral=True
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
            await ctx.send(
                embed=self.create_embed("Erreur", "Vous ne pouvez pas bannir un utilisateur avec lui-même."),
                ephemeral=True
            )
            return

        context_id = self.get_context_id(ctx)
        banned_pairs = self.read_banned_pairs(context_id)
        
        # Check if pair already exists
        for p1, p2 in banned_pairs:
            if (user1.id == p1 and user2.id == p2) or (user1.id == p2 and user2.id == p1):
                await ctx.send(
                    embed=self.create_embed("Déjà interdit", "Ces utilisateurs sont déjà interdits de se tirer mutuellement."),
                    ephemeral=True
                )
                return

        banned_pairs.append((user1.id, user2.id))
        self.write_banned_pairs(context_id, banned_pairs)
        
        await ctx.send(
            embed=self.create_embed(
                "Paire interdite",
                f"{user1.mention} et {user2.mention} ne pourront pas se tirer mutuellement."
            ),
            ephemeral=True
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
    async def unban_pair(self, ctx: SlashContext, user1: Member | User, user2: Member | User) -> None:
        context_id = self.get_context_id(ctx)
        banned_pairs = self.read_banned_pairs(context_id)
        
        new_pairs = [
            (p1, p2) for p1, p2 in banned_pairs
            if not ((user1.id == p1 and user2.id == p2) or (user1.id == p2 and user2.id == p1))
        ]
        
        if len(new_pairs) == len(banned_pairs):
            await ctx.send(
                embed=self.create_embed("Non trouvé", "Ces utilisateurs ne sont pas interdits de se tirer mutuellement."),
                ephemeral=True
            )
            return

        self.write_banned_pairs(context_id, new_pairs)
        
        await ctx.send(
            embed=self.create_embed(
                "Paire autorisée",
                f"{user1.mention} et {user2.mention} peuvent à nouveau se tirer mutuellement."
            ),
            ephemeral=True
        )

    @secret_santa.subcommand(
        sub_cmd_name="listbans",
        sub_cmd_description="Liste les paires d'utilisateurs interdites",
    )
    async def list_bans(self, ctx: SlashContext) -> None:
        context_id = self.get_context_id(ctx)
        banned_pairs = self.read_banned_pairs(context_id)
        
        if not banned_pairs:
            await ctx.send(
                embed=self.create_embed("Aucune restriction", "Aucune paire d'utilisateurs n'est interdite."),
                ephemeral=True
            )
            return

        description = ""
        for user1_id, user2_id in banned_pairs:
            try:
                user1 = await self.bot.fetch_user(user1_id)
                user2 = await self.bot.fetch_user(user2_id)
                description += f"• {user1.mention} ↔ {user2.mention}\n"
            except Exception:
                description += f"• <@{user1_id}> ↔ <@{user2_id}>\n"

        await ctx.send(
            embed=self.create_embed("Paires interdites", description),
            ephemeral=True
        )

    # ========== Button Callbacks ==========

    @component_callback(re.compile(r"secretsanta_join:(.+)"))
    async def on_join_button(self, ctx: ComponentContext) -> None:
        # Extract context_id from custom_id
        context_id = ctx.custom_id.split(":", 1)[1]
        logger.debug(f"Join button clicked by {ctx.author.id} for session {context_id}")
        session = self.get_session(context_id)
        
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
        self.save_session(session)
        logger.info(f"User {ctx.author.id} ({ctx.author.username}) joined Secret Santa in {context_id} (total: {len(session.participants)})")
        
        # Update message
        await self._update_session_message(ctx, session)
        await self._send_response(ctx, session, "Vous êtes inscrit au Père Noël Secret ! 🎁")

    @component_callback(re.compile(r"secretsanta_leave:(.+)"))
    async def on_leave_button(self, ctx: ComponentContext) -> None:
        # Extract context_id from custom_id
        context_id = ctx.custom_id.split(":", 1)[1]
        logger.debug(f"Leave button clicked by {ctx.author.id} for session {context_id}")
        session = self.get_session(context_id)
        
        if not session:
            await self._send_response(ctx, session, "Cette session n'existe plus.")
            return
        
        if session.is_drawn:
            await self._send_response(ctx, session, "Le tirage a déjà été effectué, vous ne pouvez plus vous retirer.")
            return
        
        if ctx.author.id not in session.participants:
            await self._send_response(ctx, session, "Vous ne participez pas à cette session.")
            return
        
        session.participants.remove(ctx.author.id)
        self.save_session(session)
        logger.info(f"User {ctx.author.id} ({ctx.author.username}) left Secret Santa in {context_id} (total: {len(session.participants)})")
        
        # Update message
        await self._update_session_message(ctx, session)
        await self._send_response(ctx, session, "Vous avez été retiré du Père Noël Secret.")

    async def _send_response(self, ctx: ComponentContext, session: Optional[SecretSantaSession], message: str) -> None:
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

    async def _update_session_message(self, ctx: ComponentContext, session: SecretSantaSession) -> None:
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
                    try:
                        user = await self.bot.fetch_user(user_id)
                        participant_mentions.append(user.mention)
                    except Exception:
                        participant_mentions.append(f"<@{user_id}>")
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
            
            embed = self.create_embed("Père Noël Secret", description, color=BrandColors.GREEN)
            
            await ctx.message.edit(embed=embed, components=self._create_join_buttons(session.context_id))
        except Exception as e:
            logger.error(f"Failed to update session message: {e}")
