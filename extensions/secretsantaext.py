import json
import os
import random
from typing import Dict, List, Optional, Tuple, Set

from interactions import (
    Extension, Client, BrandColors, PartialEmoji, Embed, OptionType, 
    SlashContext, slash_command, slash_option, Message, Member
)
from interactions.client.utils import get

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(__name__)
config, module_config, enabled_servers = load_config("moduleSecretSanta")

SECRET_SANTA_FILE = config["SecretSanta"]["secretSantaFile"]
SECRET_SANTA_KEY = config["SecretSanta"]["secretSantaKey"]
DRAW_RESULTS_FILE = config["SecretSanta"].get("drawResultsFile", "data/secret_santa_draw_results.json")
BANNED_PAIRS_FILE = config["SecretSanta"].get("bannedPairsFile", "data/secret_santa_banned_pairs.json")

discord2name = config["discord2name"]

class SecretSanta(Extension):
    def __init__(self, bot):
        self.bot: Client = bot

    def create_embed(self, message: str) -> Embed:
        return Embed(
            title="Père Noël Secret",
            description=message,
            color=BrandColors.RED,
        )

    def read_secret_santa_data(self) -> Dict[str, int]:
        try:
            with open(SECRET_SANTA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(SECRET_SANTA_KEY, {})
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    def save_draw_results(self, guild_id: int, draw_results: List[Tuple[int, int]]) -> None:
        try:
            with open(DRAW_RESULTS_FILE, "r+", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        data[str(guild_id)] = draw_results

        with open(DRAW_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        logger.info(f"Draw results saved for guild {guild_id}")

    def read_banned_pairs(self, guild_id: int) -> List[Tuple[int, int]]:
        try:
            with open(BANNED_PAIRS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(str(guild_id), [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def write_banned_pairs(self, guild_id: int, banned_pairs: List[Tuple[int, int]]) -> None:
        try:
            with open(BANNED_PAIRS_FILE, "r+", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        
        data[str(guild_id)] = banned_pairs
        
        with open(BANNED_PAIRS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"Banned pairs updated for guild {guild_id}: {banned_pairs}")

    def is_valid_assignment(self, giver: int, receiver: int, banned_pairs: List[Tuple[int, int]]) -> bool:
        return not any((giver == p1 and receiver == p2) or (giver == p2 and receiver == p1) 
                      for p1, p2 in banned_pairs)

    def generate_valid_assignments(self, participants: List[Member], banned_pairs: List[Tuple[int, int]]) -> Optional[List[Tuple[int, int]]]:
        max_attempts = 100
        for _ in range(max_attempts):
            shuffled = participants.copy()
            random.shuffle(shuffled)
            assignments = []
            valid = True

            for i in range(len(shuffled)):
                giver = shuffled[i]
                receiver = shuffled[(i + 1) % len(shuffled)]
                
                if not self.is_valid_assignment(giver.id, receiver.id, banned_pairs):
                    valid = False
                    break
                
                assignments.append((giver.id, receiver.id))
            
            if valid:
                return assignments
        
        return None

    @slash_command(
        name="secretsanta",
        description="Les commandes du Père Noël Secret",
        scopes=enabled_servers,
    )
    async def secret_santa(self, ctx: SlashContext) -> None:
        pass

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
    async def ban_pair(self, ctx: SlashContext, user1: Member, user2: Member) -> None:
        if user1.id == user2.id:
            await ctx.send("Vous ne pouvez pas bannir un utilisateur avec lui-même.", ephemeral=True)
            return

        banned_pairs = self.read_banned_pairs(ctx.guild.id)
        pair = (user1.id, user2.id)
        reverse_pair = (user2.id, user1.id)

        if pair in banned_pairs or reverse_pair in banned_pairs:
            await ctx.send("Ces utilisateurs sont déjà interdits de se tirer mutuellement.", ephemeral=True)
            return

        banned_pairs.append(pair)
        self.write_banned_pairs(ctx.guild.id, banned_pairs)
        await ctx.send(
            f"Les utilisateurs {user1.mention} et {user2.mention} ne pourront pas se tirer mutuellement.",
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
    async def unban_pair(self, ctx: SlashContext, user1: Member, user2: Member) -> None:
        banned_pairs = self.read_banned_pairs(ctx.guild.id)
        pair = (user1.id, user2.id)
        reverse_pair = (user2.id, user1.id)

        if pair not in banned_pairs and reverse_pair not in banned_pairs:
            await ctx.send("Ces utilisateurs ne sont pas interdits de se tirer mutuellement.", ephemeral=True)
            return

        banned_pairs = [p for p in banned_pairs if p != pair and p != reverse_pair]
        self.write_banned_pairs(ctx.guild.id, banned_pairs)
        await ctx.send(
            f"Les utilisateurs {user1.mention} et {user2.mention} peuvent à nouveau se tirer mutuellement.",
            ephemeral=True
        )

    @secret_santa.subcommand(
        sub_cmd_name="listbans",
        sub_cmd_description="Liste les paires d'utilisateurs interdites",
    )
    async def list_bans(self, ctx: SlashContext) -> None:
        banned_pairs = self.read_banned_pairs(ctx.guild.id)
        
        if not banned_pairs:
            await ctx.send("Aucune paire d'utilisateurs n'est interdite.", ephemeral=True)
            return

        description = "Paires d'utilisateurs interdites :\n\n"
        for user1_id, user2_id in banned_pairs:
            user1 = await self.bot.fetch_user(user1_id)
            user2 = await self.bot.fetch_user(user2_id)
            description += f"• {user1.mention} et {user2.mention}\n"

        embed = self.create_embed(description)
        await ctx.send(embed=embed, ephemeral=True)

    # Modified draw method to respect banned pairs
    @secret_santa.subcommand(
        sub_cmd_name="draw",
        sub_cmd_description="Effectue le tirage au sort du Père Noël Secret",
    )
    async def secret_santa_draw(self, ctx: SlashContext) -> None:
        await ctx.defer()
        data = self.read_secret_santa_data()
        guild_data = data.get(str(ctx.guild.id))
        
        if not guild_data:
            await ctx.send(
                embed=self.create_embed(
                    "Il n'y a pas de Père Noël Secret en cours !\n(Serveur non trouvé)"
                ),
                ephemeral=True,
            )
            return
        
        channel_id = int(guild_data["channel_id"])
        message_id = int(guild_data["message_id"])
        
        channel = self.bot.get_channel(channel_id)
        if not channel:
            await ctx.send(
                embed=self.create_embed(
                    "Le canal du message n'a pas été trouvé !"
                ),
                ephemeral=True,
            )
            return
        
        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            await ctx.send(
                embed=self.create_embed(
                    "Le message du Père Noël Secret n'a pas été trouvé !"
                ),
                ephemeral=True,
            )
            return

        reaction = get(message.reactions, emoji=PartialEmoji.from_str("🎅"))
        users = [user for user in await reaction.users().flatten() if user != self.bot.user]

        if len(users) < 2:
            await ctx.send("Il n'y a pas assez de participants ! :cry:", ephemeral=True)
            return

        banned_pairs = self.read_banned_pairs(ctx.guild.id)
        draw_results = self.generate_valid_assignments(users, banned_pairs)

        if draw_results is None:
            await ctx.send(
                "Impossible de générer un tirage valide avec les restrictions actuelles. "
                "Veuillez vérifier les paires interdites.",
                ephemeral=True
            )
            return

        server = str(ctx.guild.id)
        discord2name_data = discord2name.get(server, {})
        description = (
            "Ho, ho, ho, c'est Mich... le Père Noël.\n"
            "Cette année, tu dois offrir un cadeau à {mention} ! A toi de voir s'il a été sage.\n"
            "\u200b\n"
            "Signé : *Le vrai Père Noël, évidemment :disguised_face:*"
        )

        for giver_id, receiver_id in draw_results:
            giver = await self.bot.fetch_user(giver_id)
            receiver = await self.bot.fetch_user(receiver_id)
            embed = self.create_embed(description.format(
                mention=discord2name_data.get(receiver_id, receiver.mention)
            ))
            try:
                await giver.send(embed=embed)
            except Exception as e:
                logger.error(f"Failed to send DM to {giver.username}: {e}")
                await ctx.send(f"Impossible d'envoyer un message privé à {giver.mention}. Assurez-vous que vos DMs sont ouverts.", ephemeral=True)

        self.save_draw_results(ctx.guild.id, draw_results)
        self.update_secret_santa_data(ctx.guild.id)

        participants = ", ".join(user.mention for user in sorted(users, key=lambda u: u.id))
        embed = self.create_embed(
            f"Le tirage au sort a été effectué pour les {len(users)} participants ! :santa:\n"
            f"({participants})\n"
            f"Allez voir dans vos DMs !\n\n"
            f"Signé : *le Père Noël*"
        )
        await message.edit(embed=embed)
        await ctx.send(embed=embed)