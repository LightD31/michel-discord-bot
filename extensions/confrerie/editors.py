"""`/editeur` slash command: modal-driven editor entry pushed to Notion."""

import os
from datetime import datetime
from typing import Any

from interactions import (
    Modal,
    ModalContext,
    OptionType,
    ParagraphText,
    SlashContext,
    modal_callback,
    slash_command,
    slash_option,
)

from src.core import logging as logutil
from src.discord_ext.messages import send_error
from src.integrations.notion import NotionAPIError

from ._common import (
    ValidationError,
    enabled_servers,
    genres,
    groupes,
    module_config,
    publics,
)

logger = logutil.init_logger(os.path.basename(__file__))


class EditorsMixin:
    """Collect, validate, and persist new editor entries to the Notion DB."""

    def _validate_editor_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalise and validate the free-form fields coming from ``/editeur``."""
        name = data.get("name", "").strip()
        if not name:
            raise ValidationError("Le nom de l'éditeur est obligatoire")

        note = data.get("note", -1)
        if note != -1:
            try:
                note = float(note)
                if not (0 <= note <= 5):
                    raise ValidationError("La note doit être comprise entre 0 et 5")
            except (ValueError, TypeError) as e:
                raise ValidationError("La note doit être un nombre valide") from e

        date_str = data.get("date", "").strip()
        if date_str:
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError as e:
                raise ValidationError("La date doit être au format YYYY-MM-DD") from e

        site = data.get("site", "").strip()
        if site and not (site.startswith("http://") or site.startswith("https://")):
            data["site"] = f"https://{site}"

        return data

    @slash_command(
        name="editeur",
        description="Ajouter un éditeur à la liste",
        scopes=[int(s) for s in enabled_servers],
    )
    @slash_option(
        name="name",
        description="Nom de l'éditeur",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="genre_1",
        description="Genre",
        required=True,
        opt_type=OptionType.STRING,
        choices=genres,
    )
    @slash_option(
        name="public_1",
        description="Public visé",
        required=True,
        opt_type=OptionType.STRING,
        choices=publics,
    )
    @slash_option(
        name="genre_2",
        description="Genre",
        required=False,
        opt_type=OptionType.STRING,
        choices=genres,
    )
    @slash_option(
        name="genre_3",
        description="Genre",
        required=False,
        opt_type=OptionType.STRING,
        choices=genres,
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe éditorial",
        required=False,
        opt_type=OptionType.STRING,
        choices=groupes,
    )
    @slash_option(
        name="site",
        description="Site web de l'éditeur",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="note",
        description="Note sur 5, entre 0 et 5",
        required=False,
        opt_type=OptionType.NUMBER,
        min_value=0,
        max_value=5,
    )
    @slash_option(
        name="public_2",
        description="Public visé",
        required=False,
        opt_type=OptionType.STRING,
        choices=publics,
    )
    @slash_option(
        name="public_3",
        description="Public visé",
        required=False,
        opt_type=OptionType.STRING,
        choices=publics,
    )
    @slash_option(
        name="date",
        description="Date de création de l'éditeur (format : YYYY-MM-DD)",
        required=False,
        opt_type=OptionType.STRING,
        min_length=10,
        max_length=10,
    )
    @slash_option(
        name="taille",
        description="Taille de l'éditeur",
        required=False,
        opt_type=OptionType.STRING,
    )
    async def ajouterediteur(
        self,
        ctx: SlashContext,
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
        """Validate inputs, stash them on ``self.data``, then open the modal."""
        try:
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

            validated_data = self._validate_editor_data(editor_data)

            modal = Modal(
                ParagraphText(
                    label="Présentation",
                    custom_id="presentation",
                    placeholder="Présentation de l'éditeur",
                    required=False,
                ),
                ParagraphText(
                    label="Commentaire",
                    custom_id="commentaire",
                    placeholder="Commentaire sur l'éditeur",
                    required=False,
                ),
                title=f"Ajout de {name}",
                custom_id="ajouterediteur",
            )

            self.data = validated_data
            await ctx.send_modal(modal)
        except ValidationError as e:
            await send_error(ctx, f"Erreur de validation: {e}")
            logger.warning(f"Validation échouée pour l'éditeur {name}: {e}")
        except Exception as e:
            await send_error(ctx, "Une erreur est survenue lors de la préparation du formulaire.")
            logger.error(f"Erreur lors de la préparation du formulaire éditeur: {e}")

    @modal_callback("ajouterediteur")
    async def ajouterediteur_callback(
        self,
        ctx: ModalContext,
        commentaire: str,
        presentation: str,
    ):
        """Build the Notion properties from ``self.data`` and create the page."""
        try:
            if not self.data:
                await send_error(ctx, "Données manquantes, veuillez recommencer.")
                return

            properties = await self._build_editor_properties(self.data, commentaire, presentation)
            page = await self.notion_client.create_page(
                database_id=module_config["confrerieNotionDbIdEditorsId"],
                properties=properties,
            )

            self.data = {}

            await ctx.send(
                f"✅ Éditeur **{self.data.get('name', 'Inconnu')}** ajouté avec succès !\n"
                f"📋 [Voir dans Notion]({page['public_url']})",
                ephemeral=True,
            )

            logger.info(f"Éditeur {self.data.get('name')} ajouté par {ctx.author}")
        except NotionAPIError as e:
            await send_error(ctx, f"Erreur lors de l'ajout à Notion: {e}")
        except Exception as e:
            await send_error(ctx, "Une erreur est survenue lors de l'ajout de l'éditeur.")
            logger.error(f"Erreur lors de l'ajout de l'éditeur: {e}")
        finally:
            self.data = {}

    async def _build_editor_properties(
        self, data: dict[str, Any], commentaire: str, presentation: str
    ) -> dict[str, Any]:
        """Shape ``data`` + modal text into the Notion property payload."""
        properties: dict[str, Any] = {
            "Nom": {"title": [{"text": {"content": data["name"]}}]},
            "Genre(s)": {
                "multi_select": [
                    {"name": genre.strip()} for genre in data["genres"].split(",") if genre.strip()
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

        if data.get("groupe"):
            properties["Groupe éditorial"] = {"select": {"name": data["groupe"]}}

        if data.get("site"):
            properties["Site"] = {"url": data["site"]}

        if data.get("note", -1) != -1:
            properties["Note"] = {"number": data["note"]}

        if commentaire.strip():
            properties["Commentaire"] = {"rich_text": [{"text": {"content": commentaire.strip()}}]}

        if presentation.strip():
            properties["Présentation"] = {
                "rich_text": [{"text": {"content": presentation.strip()}}]
            }

        if data.get("taille"):
            properties["Taille"] = {"rich_text": [{"text": {"content": data["taille"]}}]}

        if data.get("date"):
            properties["Date création"] = {"date": {"start": data["date"]}}

        return properties
