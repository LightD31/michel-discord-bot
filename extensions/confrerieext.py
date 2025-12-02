"""
Extension Discord pour la gestion de la confrérie littéraire.

Cette extension gère les statistiques, les défis, et les éditeurs
via l'intégration avec l'API Notion (version 2025-09-03).
"""

import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Any

import aiohttp
import interactions
from notion_client import AsyncClient, APIResponseError

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

# Configuration loading
config, module_config, enabled_servers = load_config("moduleConfrerie")
module_config = module_config[enabled_servers[0]]

# Notion API version
NOTION_VERSION = "2025-09-03"


class ConfrerieError(Exception):
    """Base exception for Confrérie extension errors."""
    pass


class NotionAPIError(ConfrerieError):
    """Exception raised when Notion API calls fail."""
    pass


class ValidationError(ConfrerieError):
    """Exception raised when data validation fails."""
    pass


class DataSourceNotFoundError(NotionAPIError):
    """Exception raised when no data source is found for a database."""
    pass
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
    """Extension Discord pour la gestion de la confrérie littéraire.
    
    Cette extension gère les statistiques, les défis, et les éditeurs
    via l'intégration avec Notion (API version 2025-09-03).
    """
    
    def __init__(self, bot: interactions.Client):
        self.bot: interactions.Client = bot
        self.data: Dict[str, Any] = {}
        self.notion = AsyncClient(auth=config["notion"]["notionSecret"])
        self._stats_cache: Dict[str, Any] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_duration = 300  # 5 minutes cache
        self._data_source_cache: Dict[str, str] = {}  # database_id -> data_source_id

    @interactions.listen()
    async def on_startup(self):
        """Initialise les tâches au démarrage du bot."""
        logger.info("Démarrage de l'extension Confrérie")
        try:
            # Pre-cache data source IDs
            await self._initialize_data_sources()
            self.confrerie.start()
            self.autoupdate.start()
            logger.info("Tâches de l'extension Confrérie démarrées avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du démarrage des tâches: {e}")

    async def _initialize_data_sources(self):
        """Pre-cache data source IDs for configured databases."""
        databases = [
            module_config.get("confrerieNotionDbOeuvresId"),
            module_config.get("confrerieNotionDbIdEditorsId"),
        ]
        for db_id in databases:
            if db_id:
                try:
                    await self._get_data_source_id(db_id)
                except Exception as e:
                    logger.warning(f"Failed to cache data source for {db_id}: {e}")

    async def _get_data_source_id(self, database_id: str) -> str:
        """Get the data source ID for a database (required for 2025-09-03 API).
        
        Args:
            database_id: The Notion database ID
            
        Returns:
            The data source ID for the database
            
        Raises:
            DataSourceNotFoundError: If no data source is found
        """
        # Check cache first
        if database_id in self._data_source_cache:
            return self._data_source_cache[database_id]
        
        try:
            # Retrieve database to get data sources list
            database = await self.notion.databases.retrieve(database_id=database_id)
            data_sources = database.get("data_sources", [])
            
            if not data_sources:
                raise DataSourceNotFoundError(
                    f"No data sources found for database {database_id}"
                )
            
            # Use the first data source (most databases have only one)
            data_source_id = data_sources[0]["id"]
            self._data_source_cache[database_id] = data_source_id
            logger.debug(f"Cached data_source_id {data_source_id} for database {database_id}")
            return data_source_id
            
        except APIResponseError as e:
            logger.error(f"API error retrieving database {database_id}: {e}")
            raise NotionAPIError(f"Failed to retrieve database: {e}")
        except Exception as e:
            logger.error(f"Unexpected error getting data source for {database_id}: {e}")
            raise NotionAPIError(f"Unexpected error: {e}")

    async def _safe_notion_query(
        self, database_id: str, filter_params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Query Notion data source with error handling (2025-09-03 API).
        
        Args:
            database_id: ID de la base de données Notion
            filter_params: Paramètres de filtrage
            
        Returns:
            Liste des résultats
            
        Raises:
            NotionAPIError: En cas d'erreur API Notion
        """
        try:
            # Get the data source ID for this database
            data_source_id = await self._get_data_source_id(database_id)
            
            # Use the new data_sources.query endpoint
            response = await self.notion.data_sources.query(
                data_source_id=data_source_id,
                filter=filter_params
            )
            return response.get("results", [])
        except APIResponseError as e:
            logger.error(f"Erreur API Notion: {e}")
            raise NotionAPIError(f"Erreur lors de la requête Notion: {e}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors de la requête Notion: {e}")
            raise NotionAPIError(f"Erreur inattendue: {e}")

    async def _create_embed_footer(self) -> interactions.EmbedFooter:
        """Crée un footer standard pour les embeds.
        
        Returns:
            Footer d'embed configuré
        """
        try:
            bot = await self.bot.fetch_member(self.bot.user.id, enabled_servers[0])
            guild = await self.bot.fetch_guild(enabled_servers[0])
            
            return interactions.EmbedFooter(
                text=bot.display_name if bot else "Michel",
                icon_url=guild.icon.url if guild and guild.icon else None,
            )
        except Exception as e:
            logger.warning(f"Impossible de créer le footer: {e}")
            return interactions.EmbedFooter(text="Michel")

    def _is_cache_valid(self) -> bool:
        """Vérifie si le cache des statistiques est encore valide."""
        if not self._cache_timestamp:
            return False
        return (datetime.now() - self._cache_timestamp).total_seconds() < self._cache_duration

    @interactions.Task.create(interactions.TimeTrigger(utc=False))
    async def confrerie(self):
        """Tâche principale pour mettre à jour les statistiques de la confrérie."""
        logger.debug("Début de la tâche de statistiques de la confrérie")
        
        try:
            # Utiliser le cache si disponible
            if self._is_cache_valid():
                logger.debug("Utilisation du cache pour les statistiques")
                stats_data = self._stats_cache
            else:
                stats_data = await self._fetch_statistics()
                
            await self._update_statistics_message(stats_data)
            logger.debug("Statistiques de la confrérie mises à jour avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour des statistiques: {e}")

    async def _fetch_statistics(self) -> Dict[str, Any]:
        """Récupère les statistiques depuis Notion et met à jour le cache.
        
        Returns:
            Dictionnaire contenant les statistiques
        """
        logger.debug("Récupération des statistiques depuis Notion")
        
        # Récupérer les données depuis Notion
        results = await self._safe_notion_query(
            database_id=module_config["confrerieNotionDbOeuvresId"],
            filter_params={"property": "Défi", "select": {"is_not_empty": True}}
        )
        
        # Traitement des données
        authors = defaultdict(int)
        defis = defaultdict(int)

        for result in results:
            # Compter les auteurs
            for author in result["properties"]["Auteur"]["multi_select"]:
                authors[author["name"]] += 1
            
            # Compter les défis (avec vérification de sécurité)
            defi_data = result["properties"]["Défi"]["select"]
            if defi_data:
                defis[defi_data["name"]] += 1

        # Trier les données
        sorted_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)
        sorted_defis = sorted(defis.items(), key=lambda x: x[1], reverse=True)
        
        # Mettre à jour le cache
        stats_data = {
            "authors": sorted_authors,
            "defis": sorted_defis,
            "timestamp": datetime.now()
        }
        
        self._stats_cache = stats_data
        self._cache_timestamp = datetime.now()
        
        return stats_data

    async def _update_statistics_message(self, stats_data: Dict[str, Any]):
        """Met à jour le message des statistiques dans Discord.
        
        Args:
            stats_data: Données statistiques à afficher
        """
        try:
            channel = await self.bot.fetch_channel(module_config["confrerieRecapChannelId"])
            if not channel:
                raise ConfrerieError("Canal de récapitulatif introuvable")
                
            # Vérifier que c'est un canal texte
            if not hasattr(channel, 'fetch_message'):
                raise ConfrerieError("Le canal configuré n'est pas un canal texte")
                
            message = await channel.fetch_message(module_config["confrerieRecapMessageId"])
            if not message:
                raise ConfrerieError("Message de récapitulatif introuvable")

            embed = await self._create_statistics_embed(stats_data)
            footer = await self._create_embed_footer()
            embed.set_footer(
                text=footer.text,
                icon_url=footer.icon_url
            )
            
            await message.edit(
                content="Retrouvez tous les textes en [cliquant ici](https://drndvs.link/Confrerie 'Notion de la confrérie')",
                embed=embed,
            )
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du message: {e}")
            raise

    async def _create_statistics_embed(self, stats_data: Dict[str, Any]) -> interactions.Embed:
        """Crée l'embed des statistiques.
        
        Args:
            stats_data: Données statistiques
            
        Returns:
            Embed formaté
        """
        embed = interactions.Embed(
            title="Statistiques de la confrérie",
            color=0x9B462E,
            timestamp=stats_data["timestamp"],
        )
        
        # Formater les auteurs
        authors_text = "\n".join(
            f"{author} : **{count}** défi{'s' if count > 1 else ''}"
            for author, count in stats_data["authors"][:10]  # Limiter à 10 pour éviter les messages trop longs
        )
        
        # Formater les défis
        defis_text = "\n".join(
            f"{defi} : **{count}** texte{'s' if count > 1 else ''}"
            for defi, count in stats_data["defis"][:10]  # Limiter à 10
        )
        
        embed.add_field(
            name="Auteurs les plus prolifiques",
            value=authors_text or "Aucun auteur trouvé",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Espacement
        embed.add_field(
            name="Défis les plus populaires",
            value=defis_text or "Aucun défi trouvé",
            inline=True,
        )
        
        return embed

    async def update(self, page_id: str):
        """Met à jour un message Discord avec le contenu d'une page Notion.

        Args:
            page_id: L'ID de la page Notion à récupérer
            
        Raises:
            NotionAPIError: En cas d'erreur lors de l'accès à Notion
            ConfrerieError: En cas d'erreur de configuration ou de validation
        """
        try:
            # Récupérer le contenu de la page Notion
            content = await self._safe_notion_page_retrieve(page_id)
            
            # Déterminer le canal et le titre
            channel_info = self._determine_channel_and_title(content)
            channel = await self.bot.fetch_channel(channel_info["channel_id"])
            
            if not channel or not hasattr(channel, 'send'):
                raise ConfrerieError(f"Canal introuvable ou invalide: {channel_info['channel_id']}")

            # Créer l'embed
            embed = await self._create_update_embed(content, channel_info["title"])
            
            # Récupérer le message de mise à jour si présent
            update_message = self._extract_update_message(content)
            
            # Envoyer le message
            await channel.send(update_message, embed=embed)
            logger.info(f"Message de mise à jour envoyé pour la page {page_id}")
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour de la page {page_id}: {e}")
            raise

    async def _safe_notion_page_retrieve(self, page_id: str) -> Dict[str, Any]:
        """Récupère une page Notion avec gestion d'erreurs.
        
        Args:
            page_id: ID de la page Notion
            
        Returns:
            Contenu de la page
            
        Raises:
            NotionAPIError: En cas d'erreur API
        """
        try:
            return await self.notion.pages.retrieve(page_id=page_id)
        except APIResponseError as e:
            logger.error(f"Erreur API Notion lors de la récupération de la page {page_id}: {e}")
            raise NotionAPIError(f"Impossible de récupérer la page: {e}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors de la récupération de la page {page_id}: {e}")
            raise NotionAPIError(f"Erreur inattendue: {e}")

    def _determine_channel_and_title(self, content: Dict[str, Any]) -> Dict[str, str]:
        """Détermine le canal et le titre selon le type de contenu.
        
        Args:
            content: Contenu de la page Notion
            
        Returns:
            Dictionnaire avec channel_id et title
        """
        defi_data = content["properties"].get("Défi", {}).get("select")
        
        if defi_data:
            return {
                "channel_id": module_config["confrerieDefiChannelId"],
                "title": f"Nouvelle participation au {defi_data['name']}"
            }
        else:
            return {
                "channel_id": module_config["confrerieNewTextChannelId"],
                "title": "Texte mis à jour"
            }

    async def _create_update_embed(self, content: Dict[str, Any], title: str) -> interactions.Embed:
        """Crée l'embed pour une mise à jour.
        
        Args:
            content: Contenu de la page Notion
            title: Titre de l'embed
            
        Returns:
            Embed formaté
        """
        footer = await self._create_embed_footer()
        
        embed = interactions.Embed(
            title=title,
            color=0x9B462E,
            footer=footer,
            timestamp=datetime.now(),
        )

        # Ajouter le titre du texte
        titre_data = content["properties"].get("Titre", {}).get("title", [])
        if titre_data:
            embed.add_field(
                name="Titre",
                value=titre_data[0]["plain_text"],
                inline=True,
            )

        # Ajouter les auteurs
        auteurs_data = content["properties"].get("Auteur", {}).get("multi_select", [])
        if auteurs_data:
            embed.add_field(
                name="Auteur",
                value=", ".join(author["name"] for author in auteurs_data),
                inline=True,
            )

        # Ajouter le type et genre
        self._add_genre_field(embed, content)

        # Ajouter le lien Notion
        embed.add_field(
            name="Notion",
            value=f"[Lien vers Notion]({content['public_url']})",
            inline=True,
        )

        # Ajouter les liens de consultation
        self._add_consultation_links(embed, content)

        return embed

    def _add_genre_field(self, embed: interactions.Embed, content: Dict[str, Any]):
        """Ajoute le champ Type/Genre à l'embed.
        
        Args:
            embed: Embed à modifier
            content: Contenu de la page Notion
        """
        genre_texte = ""
        
        # Ajouter le type
        type_data = content["properties"].get("Type", {}).get("select")
        if type_data:
            genre_texte = type_data["name"] + " "
        
        # Ajouter les genres
        genres_data = content["properties"].get("Genre", {}).get("multi_select", [])
        if genres_data:
            genre_texte += ", ".join(genre["name"] for genre in genres_data)
        
        if genre_texte.strip():
            embed.add_field(
                name="Type / Genre",
                value=genre_texte.strip(),
                inline=False,
            )

    def _add_consultation_links(self, embed: interactions.Embed, content: Dict[str, Any]):
        """Ajoute les liens de consultation à l'embed.
        
        Args:
            embed: Embed à modifier
            content: Contenu de la page Notion
        """
        files_data = content["properties"].get("Lien / Fichier", {}).get("files", [])
        first_link = True
        
        for file in files_data:
            external_data = file.get("external")
            if external_data:
                link = f"[{file.get('name', 'Lien')}]({external_data['url']})"
                embed.add_field(
                    name="Consulter" if first_link else "\u200b",
                    value=link,
                    inline=True,
                )
                first_link = False

    def _extract_update_message(self, content: Dict[str, Any]) -> str:
        """Extrait le message de mise à jour du contenu Notion.
        
        Args:
            content: Contenu de la page Notion
            
        Returns:
            Message de mise à jour ou chaîne vide
        """
        update_data = content["properties"].get("Note de mise à jour", {}).get("rich_text", [])
        return update_data[0]["plain_text"] if update_data else ""

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
        """Tâche de mise à jour automatique des textes marqués pour update."""
        logger.debug("Début de la tâche de mise à jour automatique")
        
        try:
            # Récupérer les pages marquées pour mise à jour
            updated_pages = await self._safe_notion_query(
                database_id=module_config["confrerieNotionDbOeuvresId"],
                filter_params={"property": "Update", "checkbox": {"equals": True}}
            )
            
            if not updated_pages:
                logger.debug("Aucune page à mettre à jour")
                return
            
            logger.info(f"Traitement de {len(updated_pages)} page(s) à mettre à jour")
            
            # Traiter chaque page
            for page in updated_pages:
                try:
                    await self.update(page["id"])
                    await self._mark_page_as_updated(page["id"])
                    logger.debug(f"Page {page['id']} mise à jour avec succès")
                except Exception as e:
                    logger.error(f"Erreur lors de la mise à jour de la page {page['id']}: {e}")
                    
        except Exception as e:
            logger.error(f"Erreur lors de la tâche d'auto-update: {e}")

    async def _mark_page_as_updated(self, page_id: str):
        """Marque une page comme mise à jour dans Notion.
        
        Args:
            page_id: ID de la page à marquer
            
        Raises:
            NotionAPIError: En cas d'erreur API
        """
        try:
            await self.notion.pages.update(
                page_id=page_id, 
                properties={"Update": {"checkbox": False}}
            )
        except APIResponseError as e:
            logger.error(f"Erreur lors du marquage de la page {page_id}: {e}")
            raise NotionAPIError(f"Impossible de marquer la page comme mise à jour: {e}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors du marquage de la page {page_id}: {e}")
            raise NotionAPIError(f"Erreur inattendue: {e}")

    def _validate_editor_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Valide et nettoie les données d'éditeur.
        
        Args:
            data: Données de l'éditeur à valider
            
        Returns:
            Données validées et nettoyées
            
        Raises:
            ValidationError: Si les données sont invalides
        """
        # Validation du nom (obligatoire)
        name = data.get("name", "").strip()
        if not name:
            raise ValidationError("Le nom de l'éditeur est obligatoire")
        
        # Validation de la note
        note = data.get("note", -1)
        if note != -1:
            try:
                note = float(note)
                if not (0 <= note <= 5):
                    raise ValidationError("La note doit être comprise entre 0 et 5")
            except (ValueError, TypeError):
                raise ValidationError("La note doit être un nombre valide")
        
        # Validation de la date
        date_str = data.get("date", "").strip()
        if date_str:
            try:
                # Vérifier le format YYYY-MM-DD
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                raise ValidationError("La date doit être au format YYYY-MM-DD")
        
        # Validation de l'URL du site
        site = data.get("site", "").strip()
        if site and not (site.startswith("http://") or site.startswith("https://")):
            data["site"] = f"https://{site}"
        
        return data

    @interactions.slash_command(
        name="demande",
        description="Demander à actualiser le site de la confrérie",
        scopes=[int(s) for s in enabled_servers],
    )
    async def demande(self, ctx: interactions.SlashContext):
        """Commande pour faire une demande d'actualisation du site."""
        modal = interactions.Modal(
            interactions.ShortText(
                label="Titre", 
                custom_id="title",
                placeholder="Titre court de votre demande",
                max_length=100
            ),
            interactions.ParagraphText(
                label="Détails", 
                custom_id="details",
                placeholder="Décrivez en détail votre demande d'actualisation",
                max_length=1000
            ),
            title="Demande d'actualisation",
            custom_id="demande",
        )
        await ctx.send_modal(modal)

    @interactions.modal_callback("demande")
    async def demande_callback(
        self, ctx: interactions.ModalContext, title: str, details: str
    ):
        """Callback pour traiter une demande d'actualisation.
        
        Args:
            ctx: Contexte du modal
            title: Titre de la demande
            details: Détails de la demande
        """
        try:
            # Validation des entrées
            if not title.strip() or not details.strip():
                await ctx.send("❌ Le titre et les détails sont obligatoires.", ephemeral=True)
                return

            # Créer l'embed de demande
            embed = await self._create_request_embed(ctx, title.strip(), details.strip())
            
            # Envoyer aux propriétaires
            await self._send_request_to_owners(embed)
            
            await ctx.send("✅ Demande envoyée avec succès ! Vous recevrez une réponse prochainement.", ephemeral=True)
            logger.info(f"Demande d'actualisation envoyée par {ctx.author}: {title}")
            
        except Exception as e:
            await ctx.send("❌ Une erreur est survenue lors de l'envoi de votre demande.", ephemeral=True)
            logger.error(f"Erreur lors de l'envoi de la demande: {e}")

    async def _create_request_embed(
        self, ctx: interactions.ModalContext, title: str, details: str
    ) -> interactions.Embed:
        """Crée l'embed pour une demande d'actualisation.
        
        Args:
            ctx: Contexte du modal
            title: Titre de la demande
            details: Détails de la demande
            
        Returns:
            Embed formaté
        """
        embed = interactions.Embed(
            title="📝 Nouvelle demande d'actualisation",
            color=0x9B462E,
            timestamp=datetime.now(),
        )
        
        embed.add_field(
            name="👤 Auteur",
            value=f"{ctx.author.mention} ({ctx.author.username})",
            inline=False,
        )
        
        embed.add_field(
            name="📋 Titre",
            value=title,
            inline=False,
        )
        
        embed.add_field(
            name="📝 Détails",
            value=details,
            inline=False,
        )
        
        # Ajouter des informations contextuelles
        embed.add_field(
            name="🌐 Serveur",
            value=ctx.guild.name if ctx.guild else "Inconnu",
            inline=True,
        )
        
        embed.add_field(
            name="📅 Date",
            value=f"<t:{int(datetime.now().timestamp())}:F>",
            inline=True,
        )
        
        return embed

    async def _send_request_to_owners(self, embed: interactions.Embed):
        """Envoie la demande aux propriétaires.
        
        Args:
            embed: Embed de la demande
            
        Raises:
            ConfrerieError: Si aucun propriétaire n'est trouvé
        """
        owners_sent = 0
        
        # Envoyer au propriétaire de la confrérie
        try:
            owner_id = module_config.get("confrerieOwnerId")
            if owner_id:
                user = await self.bot.fetch_user(owner_id)
                if user:
                    await user.send(embed=embed)
                    owners_sent += 1
        except Exception as e:
            logger.warning(f"Impossible d'envoyer à l'owner confrérie: {e}")

        # Envoyer au propriétaire général du bot
        try:
            general_owner_id = config["discord"].get("ownerId")
            if general_owner_id and general_owner_id != module_config.get("confrerieOwnerId"):
                user2 = await self.bot.fetch_user(general_owner_id)
                if user2:
                    await user2.send(embed=embed)
                    owners_sent += 1
        except Exception as e:
            logger.warning(f"Impossible d'envoyer à l'owner général: {e}")

        if owners_sent == 0:
            raise ConfrerieError("Aucun propriétaire n'a pu être contacté")


def load(bot: interactions.Client):
    """Charge l'extension Confrérie dans le bot.
    
    Args:
        bot: Instance du bot Discord
    """
    logger.info("Chargement de l'extension Confrérie")
    ConfrerieClass(bot)

    @interactions.slash_command(
        name="editeur",
        description="Ajouter un éditeur à la liste",
        scopes=[int(s) for s in enabled_servers],
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
        """Commande pour ajouter un éditeur à la base de données.
        
        Args:
            ctx: Contexte de la commande
            name: Nom de l'éditeur
            genre_1: Premier genre (obligatoire)
            public_1: Premier public (obligatoire)
            genre_2: Deuxième genre (optionnel)
            genre_3: Troisième genre (optionnel)
            groupe: Groupe éditorial (optionnel)
            site: Site web de l'éditeur (optionnel)
            note: Note sur 5 (optionnel)
            public_2: Deuxième public (optionnel)
            public_3: Troisième public (optionnel)
            date: Date de création (optionnel)
            taille: Taille de l'éditeur (optionnel)
        """
        try:
            # Préparer les données
            editor_data = {
                "name": name,
                "genres": f"{genre_1}, {genre_2}, {genre_3}",
                "groupe": groupe,
                "site": site,
                "note": note,
                "publics": f"{public_1}, {public_2}, {public_3}",
                "date": date,
                "taille": taille,
            }
            
            # Valider les données
            validated_data = self._validate_editor_data(editor_data)
            
            # Créer et afficher le modal
            modal = interactions.Modal(
                interactions.ParagraphText(
                    label="Présentation",
                    custom_id="presentation",
                    placeholder="Présentation de l'éditeur",
                    required=False,
                ),
                interactions.ParagraphText(
                    label="Commentaire",
                    custom_id="commentaire",
                    placeholder="Commentaire sur l'éditeur",
                    required=False,
                ),
                title=f"Ajout de {name}",
                custom_id="ajouterediteur",
            )
            
            # Sauvegarder les données temporairement
            self.data = validated_data
            await ctx.send_modal(modal)
            
        except ValidationError as e:
            await ctx.send(f"❌ Erreur de validation: {e}", ephemeral=True)
            logger.warning(f"Validation échouée pour l'éditeur {name}: {e}")
        except Exception as e:
            await ctx.send("❌ Une erreur est survenue lors de la préparation du formulaire.", ephemeral=True)
            logger.error(f"Erreur lors de la préparation du formulaire éditeur: {e}")

    @interactions.modal_callback("ajouterediteur")
    async def ajouterediteur_callback(
        self,
        ctx: interactions.ModalContext,
        commentaire: str,
        presentation: str,
    ):
        """Callback pour traiter les données du modal d'ajout d'éditeur.
        
        Args:
            ctx: Contexte du modal
            commentaire: Commentaire sur l'éditeur
            presentation: Présentation de l'éditeur
        """
        try:
            if not self.data:
                await ctx.send("❌ Données manquantes, veuillez recommencer.", ephemeral=True)
                return

            # Créer les propriétés Notion
            properties = await self._build_editor_properties(
                self.data, commentaire, presentation
            )
            
            # Créer la page dans Notion
            page = await self._create_notion_editor_page(properties)
            
            # Nettoyer les données temporaires
            self.data = {}
            
            # Confirmer l'ajout
            await ctx.send(
                f"✅ Éditeur **{self.data.get('name', 'Inconnu')}** ajouté avec succès !\n"
                f"📋 [Voir dans Notion]({page['public_url']})", 
                ephemeral=True
            )
            
            logger.info(f"Éditeur {self.data.get('name')} ajouté par {ctx.author}")
            
        except NotionAPIError as e:
            await ctx.send(f"❌ Erreur lors de l'ajout à Notion: {e}", ephemeral=True)
        except Exception as e:
            await ctx.send("❌ Une erreur est survenue lors de l'ajout de l'éditeur.", ephemeral=True)
            logger.error(f"Erreur lors de l'ajout de l'éditeur: {e}")
        finally:
            # S'assurer que les données temporaires sont nettoyées
            self.data = {}

    async def _build_editor_properties(
        self, data: Dict[str, Any], commentaire: str, presentation: str
    ) -> Dict[str, Any]:
        """Construit les propriétés Notion pour un éditeur.
        
        Args:
            data: Données de l'éditeur
            commentaire: Commentaire
            presentation: Présentation
            
        Returns:
            Propriétés formatées pour Notion
        """
        properties = {
            "Nom": {"title": [{"text": {"content": data["name"]}}]},
            "Genre(s)": {
                "multi_select": [
                    {"name": genre.strip()}
                    for genre in data["genres"].split(",")
                    if genre.strip()
                ]
            },
            "Publics": {
                "multi_select": [
                    {"name": public.strip()}
                    for public in data["publics"].split(",")
                    if public.strip()
                ]
            },
        }

        # Ajouter les champs optionnels
        if data.get("groupe"):
            properties["Groupe éditorial"] = {"select": {"name": data["groupe"]}}
            
        if data.get("site"):
            properties["Site"] = {"url": data["site"]}
            
        if data.get("note", -1) != -1:
            properties["Note"] = {"number": data["note"]}
            
        if commentaire.strip():
            properties["Commentaire"] = {
                "rich_text": [{"text": {"content": commentaire.strip()}}]
            }
            
        if presentation.strip():
            properties["Présentation"] = {
                "rich_text": [{"text": {"content": presentation.strip()}}]
            }
            
        if data.get("taille"):
            properties["Taille"] = {
                "rich_text": [{"text": {"content": data["taille"]}}]
            }
            
        if data.get("date"):
            properties["Date création"] = {"date": {"start": data["date"]}}

        return properties

    async def _create_notion_editor_page(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Crée une page éditeur dans Notion.
        
        Args:
            properties: Propriétés de la page
            
        Returns:
            Page créée
            
        Raises:
            NotionAPIError: En cas d'erreur API
        """
        try:
            # Get data source ID for the editors database
            database_id = module_config["confrerieNotionDbIdEditorsId"]
            data_source_id = await self._get_data_source_id(database_id)
            
            # Use data_source_id as parent (2025-09-03 API requirement)
            return await self.notion.pages.create(
                parent={"type": "data_source_id", "data_source_id": data_source_id},
                properties=properties,
            )
        except APIResponseError as e:
            logger.error(f"Erreur API Notion lors de la création de l'éditeur: {e}")
            raise NotionAPIError(f"Impossible de créer l'éditeur dans Notion: {e}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors de la création de l'éditeur: {e}")
            raise NotionAPIError(f"Erreur inattendue: {e}")
