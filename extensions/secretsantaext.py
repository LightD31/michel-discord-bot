import json
import os
import random
from typing import Dict, List, Optional, Tuple

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

    def write_secret_santa_data(self, data: Dict[str, int]) -> None:
        with open(SECRET_SANTA_FILE, "w", encoding="utf-8") as f:
            json.dump({SECRET_SANTA_KEY: data}, f)
        logger.info(f"Secret Santa data updated: {data}")

    def update_secret_santa_data(self, guild_id: int, message_id: Optional[int] = None) -> None:
        data = self.read_secret_santa_data()
        if message_id is None:
            data.pop(str(guild_id), None)
        else:
            data[str(guild_id)] = message_id
        self.write_secret_santa_data(data)

    def save_draw_results(self, guild_id: int, results: List[Tuple[int, int]]) -> None:
        try:
            with open(DRAW_RESULTS_FILE, "r+", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        data[str(guild_id)] = results
        
        with open(DRAW_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"Draw results saved for guild {guild_id}")

    async def fetch_message(self, ctx: SlashContext, message_id: int) -> Optional[Message]:
        try:
            return await ctx.channel.fetch_message(message_id)
        except Exception as e:
            logger.error(f"Error fetching message: {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Il n'y a pas de Père Noël Secret en cours !\n(Le message n'a pas été trouvé)"
                )
            )
            self.update_secret_santa_data(ctx.guild.id)
            return None

    @slash_command(
        name="secretsanta",
        description="Les commandes du Père Noël Secret",
        sub_cmd_name="create",
        sub_cmd_description="Crée un Père Noël Secret",
        scopes=enabled_servers,
    )
    @slash_option(
        name="infos",
        description="Informations sur le secret Santa (facultatif)",
        required=False,
        opt_type=OptionType.STRING,
    )
    async def secret_santa(self, ctx: SlashContext, infos: Optional[str] = None) -> None:
        data = self.read_secret_santa_data()
        if str(ctx.guild.id) in data:
            await ctx.send(
                "Le Père Noël Secret est déjà en cours ! :santa:", ephemeral=True
            )
            return

        embed = self.create_embed(
            f"Ho, ho, ho, ce n'est pas Michel mais le Père Noël qui vous écrit.\n"
            f"Si vous souhaitez participer au Secret Santa de **{ctx.guild.name}**, "
            f"cliquez sur la réaction :santa: ci-dessous.\n"
            f"{infos + '\\n' if infos else ''}\u200b\n"
            f"Signé : *le Père Noël*\n"
            f"PS : Vérifiez que vous avez vos DMs ouverts aux membres de ce serveur"
        )
        message = await ctx.channel.send(content="@everyone", embed=embed)
        await message.add_reaction(":santa:")
        self.update_secret_santa_data(ctx.guild.id, message.id)
        await ctx.send("Le Père Noël Secret a été créé ! :santa:", ephemeral=True)

    @secret_santa.subcommand(
        sub_cmd_name="draw",
        sub_cmd_description="Effectue le tirage au sort du Père Noël Secret",
    )
    async def secret_santa_draw(self, ctx: SlashContext) -> None:
        await ctx.defer()
        
        data = self.read_secret_santa_data()
        message_id = data.get(str(ctx.guild.id))
        if not message_id:
            await ctx.send(
                embed=self.create_embed(
                    "Il n'y a pas de Père Noël Secret en cours !\n(Serveur non trouvé)"
                ),
                ephemeral=True,
            )
            return

        message = await self.fetch_message(ctx, message_id)
        if message is None:
            return

        reaction = get(message.reactions, emoji=PartialEmoji.from_str("🎅"))
        users = [user for user in await reaction.users().flatten() if user != self.bot.user]

        if len(users) < 2:
            await ctx.send("Il n'y a pas assez de participants ! :cry:", ephemeral=True)
            return

        random.shuffle(users)

        server = str(ctx.guild.id)
        discord2name_data = discord2name.get(server, {})
        description = (
            "Ho, ho, ho, c'est Mich... le Père Noël.\n"
            "Cette année, tu dois offrir un cadeau à {mention} ! A toi de voir s'il a été sage.\n"
            "\u200b\n"
            "Signé : *Le vrai Père Noël, évidemment :disguised_face:*"
        )

        draw_results = []
        for giver, receiver in zip(users, users[1:] + [users[0]]):
            embed = self.create_embed(description.format(
                mention=discord2name_data.get(receiver.id, receiver.mention)
            ))
            try:
                await giver.send(embed=embed)
                draw_results.append((giver.id, receiver.id))
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

    @secret_santa.subcommand(
        sub_cmd_name="cancel",
        sub_cmd_description="Annule le Père Noël Secret",
    )
    async def secret_santa_cancel(self, ctx: SlashContext) -> None:
        data = self.read_secret_santa_data()
        message_id = data.get(str(ctx.guild.id))
        if not message_id:
            await ctx.send(
                embed=self.create_embed(
                    "Il n'y a pas de Père Noël Secret en cours !\n(Serveur non trouvé)"
                ),
                ephemeral=True,
            )
            return

        message = await self.fetch_message(ctx, message_id)
        if message is None:
            return

        await message.edit(
            embed=self.create_embed("Le Père Noël Secret a été annulé !")
        )
        await message.clear_reactions()
        self.update_secret_santa_data(ctx.guild.id)
        await ctx.send(
            embed=self.create_embed("Le Père Noël Secret a été annulé !"),
            ephemeral=True,
        )

    @secret_santa.subcommand(
        sub_cmd_name="check",
        sub_cmd_description="Vérifie le résultat du tirage pour un utilisateur",
    )
    @slash_option(
        name="user",
        description="L'utilisateur pour lequel vérifier le résultat",
        required=True,
        opt_type=OptionType.USER,
    )
    async def check_draw(self, ctx: SlashContext, user: Member) -> None:
        try:
            with open(DRAW_RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            await ctx.send("Aucun résultat de tirage n'a été enregistré.", ephemeral=True)
            return

        guild_results = data.get(str(ctx.guild.id), [])
        for giver_id, receiver_id in guild_results:
            if giver_id == user.id:
                receiver = await self.bot.fetch_user(receiver_id)
                await ctx.send(f"{user.mention} doit offrir un cadeau à {receiver.mention}", ephemeral=True)
                return

        await ctx.send(f"Aucun résultat trouvé pour {user.mention}", ephemeral=True)