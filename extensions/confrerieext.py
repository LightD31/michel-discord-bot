import os
from collections import defaultdict
from datetime import datetime

import interactions
from notion_client import Client

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleConfrerie")
# Server specific module
module_config = module_config[enabled_servers[0]]
genres = [
    interactions.SlashCommandChoice(name="Art/Beaux livres", value="Art/Beaux livres"),
    interactions.SlashCommandChoice(name="Aventure/voyage", value="Aventure/voyage"),
    interactions.SlashCommandChoice(name="BD/Manga", value="BD/Manga"),
    interactions.SlashCommandChoice(name="Conte", value="Conte"),
    interactions.SlashCommandChoice(name="Documentaire", value="Documentaire"),
    interactions.SlashCommandChoice(name="Essai", value="Essai"),
    interactions.SlashCommandChoice(name="Fantasy", value="Fantasy"),
    interactions.SlashCommandChoice(name="Feel good", value="Feel good"),
    interactions.SlashCommandChoice(name="Historique", value="Historique"),
    interactions.SlashCommandChoice(name="Horreur", value="Horreur"),
    interactions.SlashCommandChoice(name="Nouvelles", value="Nouvelles"),
    interactions.SlashCommandChoice(name="Poésie", value="Poésie"),
    interactions.SlashCommandChoice(name="Roman", value="Roman"),
    interactions.SlashCommandChoice(name="Science-fiction", value="Science-fiction"),
]
# Create liste of publics
publics = [
    interactions.SlashCommandChoice(name="Adulte", value="Adulte"),
    interactions.SlashCommandChoice(name="New Adult", value="New Adult"),
    interactions.SlashCommandChoice(name="Young Adult", value="Young Adult"),
]
# Create liste of groupe éditorial
groupes = [
    interactions.SlashCommandChoice(name="Editis", value="Editis"),
    interactions.SlashCommandChoice(name="Hachette", value="Hachette"),
    interactions.SlashCommandChoice(name="Indépendant", value="Indépendant"),
    interactions.SlashCommandChoice(name="Madrigall", value="Madrigall"),
]


class ConfrerieClass(interactions.Extension):
    def __init__(self, bot: interactions.client):
        self.bot: interactions.Client = bot
        self.data = {}
        self.notion = Client(auth=config["notion"]["notionSecret"])
        # Create liste of genres

    @interactions.listen()
    async def on_startup(self):
        self.confrerie.start()
        self.autoupdate.start()

    @interactions.Task.create(interactions.TimeTrigger(utc=False))
    async def confrerie(self):
        logger.debug("Confrérie task started")
        channel = await self.bot.fetch_channel(module_config["confrerieRecapChannelId"])
        message = await channel.fetch_message(module_config["confrerieRecapMessageId"])
        # Step 2: Notion API (using the Notion API)

        results = self.notion.databases.query(
            database_id=module_config["confrerieNotionDbOeuvresId"],
            filter={"property": "Défi", "select": {"is_not_empty": True}},
        ).get("results")
        # Initialize two empty dictionaries
        authors = defaultdict(int)
        defis = defaultdict(int)

        # Iterate over all the results
        for result in results:
            # Increment the count of the author and the 'Défi'
            for author in result["properties"]["Auteur"]["multi_select"]:
                authors[author["name"]] += 1
            logger.debug(result["properties"]["Défi"]["select"])
            defis[result["properties"]["Défi"]["select"]["name"]] += 1

        # Sort the dictionaries by value in descending order
        sorted_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)
        sorted_defis = sorted(defis.items(), key=lambda x: x[1], reverse=True)
        # Step 4: Date & Time (Python equivalent)
        now = datetime.now()
        embed = interactions.Embed(
            title="Statistiques de la confrérie",
            color=0x9B462E,
            timestamp=now,
        )
        embed.add_field(
            name="Auteurs les plus prolifiques",
            value="\n".join(
                f"{author} : **{count}** défi{'(s)' if count > 1 else ''}"
                for author, count in sorted_authors
            ),
            inline=True,
        )
        embed.add_field(name=" ", value=" ", inline=True)
        embed.add_field(
            name="Défis les plus populaires",
            value="\n".join(
                f"{defi} : **{count}** texte{'(s)' if count > 1 else ''}"
                for defi, count in sorted_defis
            ),
            inline=True,
        )
        bot = await self.bot.fetch_member(self.bot.user.id, enabled_servers[0])
        guild = await self.bot.fetch_guild(enabled_servers[0])
        embed.set_footer(
            text=bot.display_name,
            icon_url=guild.icon.url,
        )
        await message.edit(
            content="Retrouvez tous les textes en [cliquant ici](https://drndvs.link/Confrerie 'Notion de la confrérie')",
            embed=embed,
        )

    async def update(self, page_id):
        """
        Updates a Discord message with the content of a Notion page.

        Args:
            page_id (str): The ID of the Notion page to retrieve content from.
        """
        content = self.notion.pages.retrieve(page_id=page_id)
        bot = await self.bot.fetch_member(self.bot.user.id, enabled_servers[0])
        guild = await self.bot.fetch_guild(enabled_servers[0])
        logger.debug(content)
        channel: interactions.BaseChannel = ""
        if content["properties"]["Défi"]["select"] is not None:
            title = f"Nouvelle participation au {content['properties']['Défi']['select']['name']}"
            channel = await self.bot.fetch_channel(
                module_config["confrerieDefiChannelId"]
            )
        else:
            title = "Texte mis à jour"
            channel = await self.bot.fetch_channel(
                module_config["confrerieNewTextChannelId"]
            )

        embed = interactions.Embed(
            title=title,
            color=0x9B462E,
            footer=interactions.EmbedFooter(
                text=bot.display_name,
                icon_url=guild.icon.url,
            ),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Titre",
            value=content["properties"]["Titre"]["title"][0]["plain_text"],
            inline=True,
        )
        embed.add_field(
            name="Auteur",
            value=", ".join(
                author["name"]
                for author in content["properties"]["Auteur"]["multi_select"]
            ),
            inline=True,
        )
        genre_texte = ""
        if content["properties"]["Type"]["select"] is not None:
            genre_texte = content["properties"]["Type"]["select"]["name"] + " "
        if content["properties"]["Genre"]["multi_select"] is not None:
            genre_texte += ", ".join(
                genre["name"]
                for genre in content["properties"]["Genre"]["multi_select"]
            )
        if genre_texte != "":
            embed.add_field(
                name="Type / Genre",
                value=genre_texte,
                inline=False,
            )
        embed.add_field(
            name="Notion",
            value=f"[Lien vers Notion]({content['public_url']})",
            inline=True,
        )
        first_link = True
        if content["properties"]["Lien / Fichier"] is not None:
            for file in content["properties"]["Lien / Fichier"]["files"]:
                if file.get("external") is not None:
                    link = f"[{file.get('name')}]({file['external']['url']})\n"
                else:
                    link = f"[{file.get('name')}]({file['file']['url']})\n"
                embed.add_field(
                    name="Consulter" if first_link else "\u200b",
                    value=link,
                    inline=True,
                )
                if first_link:
                    first_link = False
        message = ""
        logger.info(content["properties"]["Note de mise à jour"])
        if content["properties"]["Note de mise à jour"]["rich_text"] != []:
            message = content["properties"]["Note de mise à jour"]["rich_text"][0][
                "plain_text"
            ]
        await channel.send(message, embed=embed)

    @interactions.Task.create(
        interactions.OrTrigger(
            interactions.TimeTrigger(hour=0, utc=False),
            interactions.TimeTrigger(hour=8, utc=False),
            interactions.TimeTrigger(hour=10, utc=False),
            interactions.TimeTrigger(hour=14, utc=False),
            interactions.TimeTrigger(hour=18, utc=False),
            interactions.TimeTrigger(hour=20, utc=False),
            interactions.TimeTrigger(hour=22, utc=False),
        )
    )
    async def autoupdate(self):
        logger.debug("Auto-update task started")
        updated = self.notion.databases.query(
            database_id=module_config["confrerieNotionDbOeuvresId"],
            filter={"property": "Update", "checkbox": {"equals": True}},
        ).get("results")
        for update in updated:
            await self.update(update["id"])
            self.notion.pages.update(
                page_id=update["id"], properties={"Update": {"checkbox": False}}
            )

    @interactions.slash_command(
        name="demande",
        description="Demander à actualiser le site de la confrérie",
        scopes=enabled_servers,
    )
    async def demande(self, ctx: interactions.SlashContext):
        modal = interactions.Modal(
            interactions.ShortText(label="Titre", custom_id="title"),
            interactions.ParagraphText(label="Détails", custom_id="details"),
            title="Demande",
            custom_id="demande",
        )
        await ctx.send_modal(modal)

    @interactions.modal_callback("demande")
    async def demande_callback(
        self, ctx: interactions.ModalContext, title: str, details: str
    ):
        user = await self.bot.fetch_user(module_config["confrerieOwnerId"])
        user2 = await self.bot.fetch_user(config["discord"].get("ownerId"))
        embed = interactions.Embed(
            title="Nouvelle demande d'actualisation",
            color=0x9B462E,
        )
        embed.add_field(
            name="Auteur",
            value=ctx.author.mention,
            inline=False,
        )
        embed.add_field(
            name="Titre",
            value=title,
            inline=False,
        )
        embed.add_field(
            name="Détails",
            value=details,
        )
        await user.send(embed=embed)
        await user2.send(embed=embed)
        await ctx.send("Demande envoyée !", ephemeral=True)

    @interactions.slash_command(
        name="editeur",
        description="Ajouter un éditeur à la liste",
        scopes=enabled_servers,
    )
    @interactions.slash_option(
        name="name",
        description="Nom de l'éditeur",
        required=True,
        opt_type=interactions.OptionType.STRING,
    )
    @interactions.slash_option(
        name="genre_1",
        description="Genre",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=genres,
    )
    @interactions.slash_option(
        name="public_1",
        description="Public visé",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=publics,
    )
    @interactions.slash_option(
        name="genre_2",
        description="Genre",
        required=False,
        opt_type=interactions.OptionType.STRING,
        choices=genres,
    )
    @interactions.slash_option(
        name="genre_3",
        description="Genre",
        required=False,
        opt_type=interactions.OptionType.STRING,
        choices=genres,
    )
    @interactions.slash_option(
        name="groupe",
        description="Nom du groupe éditorial",
        required=False,
        opt_type=interactions.OptionType.STRING,
        choices=groupes,
    )
    @interactions.slash_option(
        name="site",
        description="Site web de l'éditeur",
        required=False,
        opt_type=interactions.OptionType.STRING,
    )
    @interactions.slash_option(
        name="note",
        description="Note sur 5, entre 0 et 5",
        required=False,
        opt_type=interactions.OptionType.NUMBER,
        min_value=0,
        max_value=5,
    )
    @interactions.slash_option(
        name="public_2",
        description="Public visé",
        required=False,
        opt_type=interactions.OptionType.STRING,
        choices=publics,
    )
    @interactions.slash_option(
        name="public_3",
        description="Public visé",
        required=False,
        opt_type=interactions.OptionType.STRING,
        choices=publics,
    )
    @interactions.slash_option(
        name="date",
        description="Date de création de l'éditeur (format : YYYY-MM-DD)",
        required=False,
        opt_type=interactions.OptionType.STRING,
        min_length=10,
        max_length=10,
    )
    @interactions.slash_option(
        name="taille",
        description="Taille de l'éditeur",
        required=False,
        opt_type=interactions.OptionType.STRING,
    )
    async def ajouterediteur(
        self,
        ctx: interactions.SlashContext,
        name: str,
        genre_1: str,
        public_1: str,
        genre_2: str = "",
        genre_3: str = "",
        groupe: str = "",
        site: str = "",
        note: float = -1,
        public_2: str = "",
        public_3: str = "",
        date: str = "",
        taille: str = "",
    ):
        modal = interactions.Modal(
            interactions.ParagraphText(
                label="Présentation",
                custom_id="presentation",
                placeholder="Présentation de l'éditeur",
            ),
            interactions.ParagraphText(
                label="Commentaire",
                custom_id="commentaire",
                placeholder="Commentaire sur l'éditeur",
            ),
            title=f"Ajout de {name}",
            custom_id="ajouterediteur",
        )
        await ctx.send_modal(modal)
        # Save the data in a variable
        self.data = {
            "name": name,
            "genres": f"{genre_1}, {genre_2}, {genre_3}",
            "groupe": groupe,
            "site": site,
            "note": note,
            "publics": f"{public_1}, {public_2}, {public_3}",
            "date": date,
            "taille": taille,
        }

    @interactions.modal_callback("ajouterediteur")
    async def ajouterediteur_callback(
        self,
        ctx: interactions.ModalContext,
        commentaire: str,
        presentation: str,
    ):
        properties = {
            "Nom": {"title": [{"text": {"content": self.data["name"]}}]},
            "Genre(s)": {
                "multi_select": [
                    {"name": genre.strip()}
                    for genre in self.data["genres"].split(",")
                    if genre.strip() != ""
                ]
            },
            "Publics": {
                "multi_select": [
                    {"name": public.strip()}
                    for public in self.data["publics"].split(",")
                    if public.strip() != ""
                ]
            },
        }

        if self.data["groupe"] != "":
            properties["Groupe éditorial"] = {"select": {"name": self.data["groupe"]}}
        if self.data["site"] != "":
            properties["Site"] = {"url": self.data["site"]}
        if self.data["note"] != -1 and self.data["note"] != "":
            properties["Note"] = {"number": self.data["note"]}
        if commentaire != "":
            properties["Commentaire"] = {
                "rich_text": [{"text": {"content": commentaire}}]
            }
        if presentation != "":
            properties["Présentation"] = {
                "rich_text": [{"text": {"content": presentation}}]
            }
        if self.data["taille"] != "":
            properties["Taille"] = {
                "rich_text": [{"text": {"content": self.data["taille"]}}]
            }
        if self.data["date"] != "":
            properties["Date création"] = {"date": {"start": self.data["date"]}}

        page = self.notion.pages.create(
            parent={"database_id": module_config["confrerieNotionDbIdEditorsId"]},
            properties=properties,
        )
        self.data = {}
        await ctx.send(f"Éditeur ajouté !\n<{page['public_url']}>", ephemeral=True)
