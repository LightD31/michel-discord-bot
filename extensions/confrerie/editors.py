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
from src.core.cache import ExpiringMap
from src.core.errors import ValidationError
from src.discord_ext.messages import guild_id_of, send_error
from src.discord_ext.scopes import guild_scopes
from src.integrations.notion import NotionAPIError

from ._base import ConfrerieBase
from ._common import CONFRERIE, autocomplete_options

logger = logutil.init_logger(__name__)

_AUTOCOMPLETE_COLUMNS = {
    "genre-1": COL_GENRES,
    "genre-2": COL_GENRES,
    "genre-3": COL_GENRES,
    "public-1": COL_PUBLICS,
    "public-2": COL_PUBLICS,
    "public-3": COL_PUBLICS,
    "groupe": COL_GROUPE,
}


class EditorsMixin(ConfrerieBase):
    """Collect, validate, and persist new editor entries to the Notion DB."""

    _pending_editors: ExpiringMap[EditorInput]

    @slash_command(
        name="editeur",
        description="Ajouter un éditeur à la liste",
        scopes=guild_scopes(CONFRERIE.guild_ids()),
    )
    @slash_option(
        name="nom",
        description="Nom de l'éditeur",
        required=True,
        opt_type=OptionType.STRING,
        max_length=200,
        argument_name="name",
    )
    @slash_option(
        name="genre-1",
        argument_name="genre_1",
        description="Genre",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="public-1",
        argument_name="public_1",
        description="Public visé",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="genre-2",
        argument_name="genre_2",
        description="Genre",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="genre-3",
        argument_name="genre_3",
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
        name="public-2",
        argument_name="public_2",
        description="Public visé",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="public-3",
        argument_name="public_3",
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
        db_id = CONFRERIE.get(guild_id_of(ctx)).get("confrerieNotionDbIdEditorsId")
        if not db_id:
            await send_error(ctx, "La base Notion des éditeurs n'est pas configurée.")
            return
        try:
            # The modal is the interaction response: leave room in Discord's 3 s.
            schema = await self._editor_schema(CONFRERIE.get(guild_id_of(ctx)), db_id, timeout=2.0)
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
                placeholder="Ton avis, ton expérience avec cet éditeur…",
                required=False,
                max_length=2000,
            ),
            title=f"Ajout de {editor.name}"[:45],
            custom_id="ajouterediteur",
        )
        await ctx.send_modal(modal)

    @ajouterediteur.autocomplete("genre-1")
    @ajouterediteur.autocomplete("genre-2")
    @ajouterediteur.autocomplete("genre-3")
    @ajouterediteur.autocomplete("public-1")
    @ajouterediteur.autocomplete("public-2")
    @ajouterediteur.autocomplete("public-3")
    @ajouterediteur.autocomplete("groupe")
    async def ajouterediteur_autocomplete(self, ctx: AutocompleteContext):
        column = _AUTOCOMPLETE_COLUMNS.get(str(ctx.focussed_option.name), COL_GENRES)
        await autocomplete_options(
            ctx,
            self.notion,
            CONFRERIE.get(guild_id_of(ctx)),
            "confrerieNotionDbIdEditorsId",
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
        editor = self._pending_editors.pop(ctx.author.id)
        if editor is None:
            await send_error(ctx, "Il me manque des données : relance /editeur.")
            return

        editor.ligne_editoriale = ligne_editoriale
        editor.fondateurs = fondateurs
        editor.exemples = exemples
        editor.commentaire = commentaire

        db_id = CONFRERIE.get(guild_id_of(ctx)).get("confrerieNotionDbIdEditorsId")
        if not db_id:
            await send_error(ctx, "La base Notion des éditeurs n'est pas configurée.")
            return
        try:
            schema = await self._editor_schema(CONFRERIE.get(guild_id_of(ctx)), db_id)
            properties, dropped = np.filter_to_schema(build_editor_properties(editor), schema)
            if dropped:
                logger.warning("Colonnes absentes de la base Éditeurs, ignorées : %s", dropped)
            page = await self.notion(CONFRERIE.get(guild_id_of(ctx))).create_page(
                database_id=db_id, properties=properties
            )
        except NotionAPIError as e:
            logger.error("Erreur Notion lors de l'ajout de l'éditeur %s: %s", editor.name, e)
            await send_error(ctx, "Notion a refusé l'ajout de l'éditeur : réessaie plus tard.")
            return

        link = np.page_url(page)
        await ctx.send(
            f"✅ Éditeur **{editor.name}** ajouté avec succès !"
            + (f"\n📋 [Voir dans Notion]({link})" if link else ""),
            ephemeral=True,
        )
        logger.info("Éditeur %s ajouté par %s", editor.name, ctx.author)

    async def _editor_schema(
        self, settings: dict, db_id: str, timeout: float | None = None
    ) -> dict:
        """Live schema of the Éditeurs data source ({} when Notion is unreachable)."""
        try:
            return await asyncio.wait_for(
                self.notion(settings).get_properties_schema(db_id), timeout=timeout
            )
        except (NotionAPIError, TimeoutError) as e:
            logger.warning("Schéma Notion des éditeurs indisponible: %s", e)
            return {}
