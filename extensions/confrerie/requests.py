"""`/demande`: members submit a text (or an update request) for the owner to review.

The Notion page tells members to use ``/demande`` to send « la fiche de votre
texte ». The form collects what a fiche needs — title, link, type, genre,
défi — and becomes a draft: the owner creates the Notion page in one click,
marks the request handled, or declines it, and the member is told which.
"""

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
from features.confrerie.drafts import SOURCE_DEMANDE, Draft, demande_draft_id, resolve_author
from features.confrerie.stats import COL_DEFI, COL_GENRE, COL_TYPE
from src.core import logging as logutil
from src.core.errors import ValidationError
from src.discord_ext.messages import send_error

from ._common import autocomplete_options, enabled_servers, guild_id, module_config

logger = logutil.init_logger(os.path.basename(__file__))

_AUTOCOMPLETE_COLUMNS = {"type": COL_TYPE, "genre": COL_GENRE, "defi": COL_DEFI}


def _normalise_link(link: str) -> str:
    link = link.strip()
    if not link:
        return ""
    if " " in link:
        raise ValidationError("Le lien ne doit pas contenir d'espace.")
    return link if link.startswith(("http://", "https://")) else f"https://{link}"


class RequestsMixin:
    """Collect ``/demande`` submissions and hand them to the review flow."""

    _pending_demandes: dict[int, dict]

    @slash_command(
        name="demande",
        description="Proposer un texte (ou une mise à jour) pour la base de la confrérie",
        scopes=[int(s) for s in enabled_servers],
    )
    @slash_option(
        name="type",
        description="Type de texte (nouvelle, roman, poésie…)",
        required=False,
        opt_type=OptionType.STRING,
        argument_name="type_",
    )
    @slash_option(
        name="genre",
        description="Genre principal",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="defi",
        description="Défi auquel ce texte répond",
        required=False,
        opt_type=OptionType.STRING,
    )
    async def demande(self, ctx: SlashContext, type_: str = "", genre: str = "", defi: str = ""):
        schema = await self._oeuvres_schema()
        try:
            choices = {
                "types": np.canonical_choices(
                    [type_], np.schema_options(schema, COL_TYPE), "Type", strict=True
                ),
                "genres": np.canonical_choices(
                    [genre], np.schema_options(schema, COL_GENRE), "Genre", strict=True
                ),
                "defi": np.canonical_choices(
                    [defi], np.schema_options(schema, COL_DEFI), "Défi", strict=True
                ),
            }
        except ValidationError as e:
            await send_error(ctx, str(e))
            return

        self._pending_demandes[ctx.author.id] = choices
        modal = Modal(
            ShortText(
                label="Titre du texte",
                custom_id="title",
                placeholder="Titre (ou objet de votre demande)",
                max_length=100,
            ),
            ShortText(
                label="Lien vers le texte",
                custom_id="lien",
                placeholder="https://… (Pluminium, Google Docs, fil Discord…)",
                required=False,
                max_length=500,
            ),
            ParagraphText(
                label="Détails",
                custom_id="details",
                placeholder="Résumé, mise à jour à faire, précisions pour la fiche…",
                required=False,
                max_length=1000,
            ),
            title="Demande pour la confrérie",
            custom_id="demande",
        )
        await ctx.send_modal(modal)

    @demande.autocomplete("type")
    @demande.autocomplete("genre")
    @demande.autocomplete("defi")
    async def demande_autocomplete(self, ctx: AutocompleteContext):
        column = _AUTOCOMPLETE_COLUMNS.get(str(ctx.focussed_option.name), COL_TYPE)
        await autocomplete_options(
            ctx, self.notion_client, module_config.get("confrerieNotionDbOeuvresId"), column
        )

    @modal_callback("demande")
    async def demande_callback(
        self, ctx: ModalContext, title: str = "", lien: str = "", details: str = ""
    ):
        choices = self._pending_demandes.pop(ctx.author.id, {})
        title = title.strip()
        if not title:
            await send_error(ctx, "Le titre est obligatoire.")
            return
        try:
            link = _normalise_link(lien)
        except ValidationError as e:
            await send_error(ctx, str(e))
            return

        author, mapped = resolve_author(
            int(ctx.author.id),
            ctx.author.display_name,
            module_config.get("confrerieAuthorMap") or {},
        )
        defi = choices.get("defi") or []
        draft = Draft(
            id=demande_draft_id(),
            source=SOURCE_DEMANDE,
            guild_id=str(guild_id),
            submitter_id=int(ctx.author.id),
            title=title[:100],
            authors=[author],
            types=choices.get("types", []),
            genres=choices.get("genres", []),
            defi=defi[0] if defi else None,
            link=link,
            link_label="Lien",
            details=details.strip(),
            unmapped_author=not mapped,
        )

        await ctx.defer(ephemeral=True)
        try:
            sent = await self.submit_draft(draft)
        except Exception as e:  # noqa: BLE001 — surface a clean error to the member
            logger.error("Demande de %s non enregistrée: %s", ctx.author, e)
            sent = False
        if not sent:
            await self._drafts().delete(draft.id)
            await send_error(ctx, "Impossible de transmettre votre demande, réessayez plus tard.")
            return

        await ctx.send(
            "✅ Demande envoyée ! Vous recevrez un message privé quand elle aura été traitée.",
            ephemeral=True,
        )
        logger.info("Demande « %s » envoyée par %s", title, ctx.author)
