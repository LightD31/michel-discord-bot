"""Read commands over the Notion bases: `/texte`, `/auteur`, `/outil`."""

import os
import random
import time

from interactions import (
    AutocompleteContext,
    Embed,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from features.confrerie.browse import (
    COL_TOOL_THEME,
    author_profile,
    filter_tools,
    find_work,
    member_links,
    parse_tool,
    search_works,
    top_level_works,
)
from features.confrerie.stats import COL_AUTHOR
from features.links import shorten_url
from src.core import logging as logutil
from src.core.text import take_within_budget
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import send_error

from ._common import autocomplete_options, enabled_servers, module_config
from .stats import plural
from .updates import build_work_embed, shorten_work_links

logger = logutil.init_logger(os.path.basename(__file__))

# Autocomplete must answer within 3 s: keep the Œuvres list around instead of
# paging through Notion on every keystroke.
WORKS_CACHE_SECONDS = 600
DESCRIPTION_BUDGET = 4000


def _private_option():
    return slash_option(
        name="prive",
        description="Réponse visible par vous seul·e",
        required=False,
        opt_type=OptionType.BOOLEAN,
    )


class BrowseMixin:
    """Look up works, authors and tools from Discord."""

    _works_cache: tuple[float, list[dict]] | None

    async def _oeuvres_pages(self) -> list[dict]:
        cached = self._works_cache
        if cached and time.monotonic() - cached[0] < WORKS_CACHE_SECONDS:
            return cached[1]
        pages = await self.notion_client.query_data_source(
            database_id=module_config["confrerieNotionDbOeuvresId"]
        )
        self._works_cache = (time.monotonic(), pages)
        return pages

    # ── /texte ───────────────────────────────────────────────────────────

    @slash_command(
        name="texte",
        description="Afficher la fiche d'un texte de la confrérie (au hasard sans titre)",
        scopes=[int(s) for s in enabled_servers],
    )
    @slash_option(
        name="titre",
        description="Titre du texte",
        required=False,
        opt_type=OptionType.STRING,
        autocomplete=True,
    )
    @_private_option()
    async def texte(self, ctx: SlashContext, titre: str = "", prive: bool = False):
        await ctx.defer(ephemeral=prive)
        works = top_level_works(await self._oeuvres_pages())
        if titre:
            work = find_work(works, titre)
        else:
            work = random.choice(works) if works else None
        if work is None:
            await send_error(
                ctx, f"Aucun texte trouvé pour « {titre} »." if titre else "Aucun texte."
            )
            return

        notion_url, links = await shorten_work_links(work)
        embed = build_work_embed(
            work,
            "📖 Fiche du texte" if titre else "📖 Un texte de la confrérie au hasard",
            notion_url=notion_url,
            links=links,
            footer=await self._create_embed_footer(),
        )
        await ctx.send(embed=embed, ephemeral=prive)

    @texte.autocomplete("titre")
    async def texte_autocomplete(self, ctx: AutocompleteContext):
        try:
            works = top_level_works(await self._oeuvres_pages())
        except Exception as e:  # noqa: BLE001 — an empty list beats a failed interaction
            logger.warning("Liste des œuvres indisponible: %s", e)
            works = []
        choices = [
            {
                "name": (w.title + (f" — {', '.join(w.authors)}" if w.authors else ""))[:100],
                "value": w.page_id,
            }
            for w in search_works(works, ctx.input_text)
        ]
        await ctx.send(choices=choices)

    # ── /auteur ──────────────────────────────────────────────────────────

    @slash_command(
        name="auteur",
        description="Profil d'un·e auteur·ice de la confrérie",
        scopes=[int(s) for s in enabled_servers],
    )
    @slash_option(
        name="nom",
        description="Nom d'auteur·ice",
        required=True,
        opt_type=OptionType.STRING,
        autocomplete=True,
    )
    @_private_option()
    async def auteur(self, ctx: SlashContext, nom: str, prive: bool = False):
        await ctx.defer(ephemeral=prive)
        profile = author_profile(await self._oeuvres_pages(), nom)
        if profile is None:
            await send_error(ctx, "Aucun·e auteur·ice de ce nom dans la base.")
            return

        embed = Embed(
            title=f"✍️ {profile.name}",
            description=(
                f"**{plural(profile.works, 'œuvre')}** · "
                f"**{plural(profile.defis, 'participation')}** aux défis"
            ),
            color=Colors.CONFRERIE,
        )
        lines = []
        for work in profile.latest:
            url = await shorten_url(work.url, title=work.title, tags=["confrerie"])
            label = f"[{work.title}]({url})" if url else work.title
            detail = work.type_genre or work.status or ""
            lines.append(label + (f" — {detail}" if detail else ""))
        if lines:
            kept, _ = take_within_budget(lines, 1024, separator="\n")
            embed.add_field(name="Dernières œuvres", value="\n".join(kept), inline=False)

        membres_db = module_config.get("confrerieNotionDbMembresId")
        if membres_db:
            try:
                rows = await self.notion_client.query_data_source(database_id=membres_db)
                links = [
                    f"[{link.site or link.label}]"
                    f"({await shorten_url(link.url, title=link.label, tags=['confrerie'])})"
                    for link in member_links(rows, profile.name)
                ]
            except Exception as e:  # noqa: BLE001 — the profile is useful without links
                logger.warning("Répertoire des membres indisponible: %s", e)
                links = []
            if links:
                kept, _ = take_within_budget(links, 1024, separator=" · ")
                embed.add_field(name="Liens", value=" · ".join(kept), inline=False)

        footer = await self._create_embed_footer()
        embed.set_footer(text=footer.text, icon_url=footer.icon_url)
        await ctx.send(embed=embed, ephemeral=prive)

    @auteur.autocomplete("nom")
    async def auteur_autocomplete(self, ctx: AutocompleteContext):
        await autocomplete_options(
            ctx, self.notion_client, module_config.get("confrerieNotionDbOeuvresId"), COL_AUTHOR
        )

    # ── /outil ───────────────────────────────────────────────────────────

    @slash_command(
        name="outil",
        description="Chercher une ressource dans la boîte à outils de la confrérie",
        scopes=[int(s) for s in enabled_servers],
    )
    @slash_option(
        name="theme",
        description="Thème (carte, personnage, dictionnaire…)",
        required=False,
        opt_type=OptionType.STRING,
        autocomplete=True,
    )
    @slash_option(
        name="recherche",
        description="Mot à chercher dans le nom ou la description",
        required=False,
        opt_type=OptionType.STRING,
    )
    @_private_option()
    async def outil(
        self, ctx: SlashContext, theme: str = "", recherche: str = "", prive: bool = False
    ):
        db_id = module_config.get("confrerieNotionDbOutilsId")
        if not db_id:
            await send_error(ctx, "La boîte à outils n'est pas configurée.")
            return
        await ctx.defer(ephemeral=prive)
        pages = await self.notion_client.query_data_source(database_id=db_id)
        tools = filter_tools([parse_tool(p) for p in pages], theme, recherche)
        if not tools:
            await send_error(ctx, "Aucune ressource ne correspond.")
            return

        entries = []
        for tool in tools:
            url = await shorten_url(tool.url, title=tool.name, tags=["confrerie"])
            entry = f"**[{tool.name}]({url})**" if url else f"**{tool.name}**"
            if tool.tested:
                entry += " ✅"
            if tool.themes:
                entry += f" · {', '.join(tool.themes)}"
            if tool.description:
                entry += f"\n{tool.description[:200]}"
            if tool.review:
                entry += f"\n> {tool.review[:200]}"
            entries.append(entry)
        kept, _ = take_within_budget(entries, DESCRIPTION_BUDGET, separator="\n\n")

        embed = Embed(
            title="🧰 Boîte à outils" + (f" — {theme}" if theme else ""),
            description="\n\n".join(kept),
            color=Colors.CONFRERIE,
        )
        hidden = len(tools) - len(kept)
        count = plural(len(tools), "ressource")
        embed.set_footer(
            text=count
            + (f" · {hidden} non affichée(s), affinez la recherche" if hidden else "")
            + " · ✅ = testée par la confrérie"
        )
        await ctx.send(embed=embed, ephemeral=prive)

    @outil.autocomplete("theme")
    async def outil_autocomplete(self, ctx: AutocompleteContext):
        await autocomplete_options(
            ctx,
            self.notion_client,
            module_config.get("confrerieNotionDbOutilsId"),
            COL_TOOL_THEME,
        )
