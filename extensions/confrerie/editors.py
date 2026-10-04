"""`/editeur` slash command: modal-driven editor entry pushed to Notion."""

import asyncio
import os

from interactions import (
    AutocompleteContext,
    Modal,
    ModalContext,
    OptionType,
    ParagraphText,
    ShortText,
    SlashContext,
    modal_callback,
    slash_command,
    slash_option,
)

from features.confrerie import notion_props as np
from features.confrerie.editors import (
    COL_GENRES,
    COL_GROUPE,
    COL_PUBLICS,
    EditorInput,
    build_editor_properties,
    validate_editor,
)
from src.core import logging as logutil
from src.core.errors import ValidationError
from src.discord_ext.messages import send_error
from src.integrations.notion import NotionAPIError

from ._common import autocomplete_options, enabled_servers, module_config

logger = logutil.init_logger(__name__)

_AUTOCOMPLETE_COLUMNS = {
    "genre_1": COL_GENRES,
    "genre_2": COL_GENRES,
    "genre_3": COL_GENRES,
    "public_1": COL_PUBLICS,
    "public_2": COL_PUBLICS,
    "public_3": COL_PUBLICS,
    "groupe": COL_GROUPE,
}


class EditorsMixin:
    """Collect, validate, and persist new editor entries to the Notion DB."""

    _pending_editors: dict[int, EditorInput]

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
        max_length=200,
    )
    @slash_option(
        name="genre_1",
        description="Genre",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="public_1",
        description="Public visé",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="genre_2",
        description="Genre",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="genre_3",
        description="Genre",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe éditorial",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="site",
        description="Site web de l'éditeur",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="public_2",
        description="Public visé",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="public_3",
        description="Public visé",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="date",
        description="Date de création de l'éditeur (format : AAAA-MM-JJ)",
        required=False,
        opt_type=OptionType.STRING,
        min_length=10,
        max_length=10,
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
        public_2: str = "",
        public_3: str = "",
        date: str = "",
    ):
        """Validate the options, remember them for this user, then open the modal."""
        db_id = module_config.get("confrerieNotionDbIdEditorsId")
        if not db_id:
            await send_error(ctx, "La base Notion des éditeurs n'est pas configurée.")
            return
        try:
            # The modal is the interaction response: leave room in Discord's 3 s.
            schema = await self._editor_schema(db_id, timeout=2.0)
            editor = validate_editor(
                name=name,
                genres=[genre_1, genre_2, genre_3],
                publics=[public_1, public_2, public_3],
                groupe=groupe,
                site=site,
                date=date,
                schema=schema,
            )
        except ValidationError as e:
            await send_error(ctx, str(e))
            return

        self._pending_editors[ctx.author.id] = editor
        modal = Modal(
            ParagraphText(
                label="Ligne éditoriale",
                custom_id="ligne_editoriale",
                placeholder="Ce que publie l'éditeur, sa ligne, ses collections…",
                required=False,
                max_length=2000,
            ),
            ShortText(
                label="Fondateur(s)",
                custom_id="fondateurs",
                required=False,
                max_length=200,
            ),
            ParagraphText(
                label="Exemples d'auteur·ices publié·es",
                custom_id="exemples",
                required=False,
                max_length=1000,
            ),
            ParagraphText(
                label="Commentaire",
                custom_id="commentaire",
                placeholder="Votre avis, votre expérience avec cet éditeur…",
                required=False,
                max_length=2000,
            ),
            title=f"Ajout de {editor.name}"[:45],
            custom_id="ajouterediteur",
        )
        await ctx.send_modal(modal)

    @ajouterediteur.autocomplete("genre_1")
    @ajouterediteur.autocomplete("genre_2")
    @ajouterediteur.autocomplete("genre_3")
    @ajouterediteur.autocomplete("public_1")
    @ajouterediteur.autocomplete("public_2")
    @ajouterediteur.autocomplete("public_3")
    @ajouterediteur.autocomplete("groupe")
    async def ajouterediteur_autocomplete(self, ctx: AutocompleteContext):
        column = _AUTOCOMPLETE_COLUMNS.get(str(ctx.focussed_option.name), COL_GENRES)
        await autocomplete_options(
            ctx,
            self.notion_client,
            module_config.get("confrerieNotionDbIdEditorsId"),
            column,
        )

    @modal_callback("ajouterediteur")
    async def ajouterediteur_callback(
        self,
        ctx: ModalContext,
        ligne_editoriale: str = "",
        fondateurs: str = "",
        exemples: str = "",
        commentaire: str = "",
    ):
        """Create the Notion page from this user's pending options + modal text."""
        editor = self._pending_editors.pop(ctx.author.id, None)
        if editor is None:
            await send_error(ctx, "Données manquantes, veuillez relancer /editeur.")
            return

        editor.ligne_editoriale = ligne_editoriale
        editor.fondateurs = fondateurs
        editor.exemples = exemples
        editor.commentaire = commentaire

        db_id = module_config["confrerieNotionDbIdEditorsId"]
        try:
            schema = await self._editor_schema(db_id)
            properties, dropped = np.filter_to_schema(build_editor_properties(editor), schema)
            if dropped:
                logger.warning("Colonnes absentes de la base Éditeurs, ignorées : %s", dropped)
            page = await self.notion_client.create_page(database_id=db_id, properties=properties)
        except NotionAPIError as e:
            logger.error("Erreur Notion lors de l'ajout de l'éditeur %s: %s", editor.name, e)
            await send_error(ctx, "Notion a refusé l'ajout de l'éditeur, réessayez plus tard.")
            return

        link = np.page_url(page)
        await ctx.send(
            f"✅ Éditeur **{editor.name}** ajouté avec succès !"
            + (f"\n📋 [Voir dans Notion]({link})" if link else ""),
            ephemeral=True,
        )
        logger.info("Éditeur %s ajouté par %s", editor.name, ctx.author)

    async def _editor_schema(self, db_id: str, timeout: float | None = None) -> dict:
        """Live schema of the Éditeurs data source ({} when Notion is unreachable)."""
        try:
            return await asyncio.wait_for(
                self.notion_client.get_properties_schema(db_id), timeout=timeout
            )
        except (NotionAPIError, TimeoutError) as e:
            logger.warning("Schéma Notion des éditeurs indisponible: %s", e)
            return {}
