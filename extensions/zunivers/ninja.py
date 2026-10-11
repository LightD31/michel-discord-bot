"""NinjaMixin — /ninja: a player's best ZUnivers plan of the day, from ZUnivers Ninja.

ZUnivers Ninja runs as its own service; the bot queries its
``/api/plan/{pseudo}/discord`` route and posts the ready-made message. The
ZUnivers pseudo is the player's Discord username.
"""

import io

from interactions import (
    ActionRow,
    AllowedMentions,
    AutocompleteContext,
    Button,
    ButtonStyle,
    Embed,
    File,
    Member,
    OptionType,
    SlashContext,
    User,
    slash_command,
    slash_option,
)

from features.zunivers_ninja import (
    NinjaClient,
    NinjaError,
    NinjaNotFoundError,
    build_web_url,
)

from ._common import enabled_servers, module_config


class NinjaMixin:
    """On-demand /ninja command."""

    @slash_command(
        name="ninja",
        description="Affiche le meilleur plan ZUnivers du jour calculé par ZUnivers Ninja",
        scopes=enabled_servers,
    )
    @slash_option(
        name="membre",
        description="Membre dont afficher le plan (par défaut : toi)",
        opt_type=OptionType.USER,
        required=False,
    )
    @slash_option(
        name="hardcore",
        description="Plan du mode hardcore (par défaut : mode normal)",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    @slash_option(
        name="strategie",
        description="Stratégie du planificateur (par défaut : celle de ZUnivers Ninja)",
        opt_type=OptionType.STRING,
        required=False,
        autocomplete=True,
    )
    @slash_option(
        name="public",
        description="Affiche le plan à tout le salon (par défaut : visible par toi seul)",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    async def ninja_command(
        self,
        ctx: SlashContext,
        membre: User | Member | None = None,
        hardcore: bool = False,
        strategie: str | None = None,
        public: bool = False,
    ):
        url = module_config.get("ninjaUrl")
        if not url:
            await ctx.send("ZUnivers Ninja n'est pas configuré sur ce serveur.", ephemeral=True)
            return

        user = membre or ctx.author
        ephemeral = not public
        await ctx.defer(ephemeral=ephemeral)
        try:
            plan = await NinjaClient(url).get_plan(
                user.username,
                ruleset="HARDCORE" if hardcore else "NORMAL",
                strategy=strategie,
            )
        except NinjaNotFoundError as e:
            await ctx.send(f"❌ {e}", ephemeral=ephemeral)
            return
        except NinjaError as e:
            await ctx.send(f"⚠️ {e}", ephemeral=ephemeral)
            return

        components = _web_link(user.username, hardcore, strategie)
        if plan.empty:
            await ctx.send(
                f"✅ Rien de plus à faire aujourd'hui pour **{plan.player_display_name}**.",
                components=components,
                ephemeral=ephemeral,
            )
            return

        embeds = [Embed.from_dict(e) for e in plan.embeds]
        files = (
            [File(io.BytesIO(plan.attachment.content.encode()), file_name=plan.attachment.name)]
            if plan.attachment
            else None
        )
        await ctx.send(
            content=plan.content or None,
            embeds=embeds,
            files=files,
            components=components,
            allowed_mentions=AllowedMentions.none(),
            ephemeral=ephemeral,
        )

    @ninja_command.autocomplete("strategie")
    async def ninja_strategy_autocomplete(self, ctx: AutocompleteContext) -> None:
        """Offer the strategies the Ninja server lists (``GET /api/strategies``)."""
        url = module_config.get("ninjaUrl")
        try:
            strategies = await NinjaClient(url).get_strategies() if url else []
        except NinjaError:
            strategies = []
        typed = ctx.input_text.lower()
        choices = [
            {"name": _choice_name(st.label, st.description), "value": st.id}
            for st in strategies
            if typed in st.id.lower() or typed in st.label.lower()
        ]
        await ctx.send(choices=choices[:25])


def _web_link(username: str, hardcore: bool, strategy: str | None) -> list[ActionRow] | None:
    """Link button to the player's plan on the Ninja web UI, when ``ninjaWebUrl`` is set.

    Not shortened: the player's page is a stable URL, as are the Ninja links in the embed.
    """
    web_url = module_config.get("ninjaWebUrl")
    if not web_url:
        return None
    link = build_web_url(web_url, username, hardcore=hardcore, strategy=strategy)
    return [ActionRow(Button(style=ButtonStyle.LINK, label="Voir sur ZUnivers Ninja", url=link))]


def _choice_name(label: str, description: str) -> str:
    """Autocomplete entry: label plus description, within Discord's 100 characters."""
    name = f"{label} — {description}" if description else label
    return name if len(name) <= 100 else name[:99] + "…"
