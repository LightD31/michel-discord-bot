"""NinjaMixin — /zunivers ninja: a player's best ZUnivers plan of the day, from ZUnivers Ninja.

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
    slash_option,
)

from features.zunivers_ninja import (
    NinjaClient,
    NinjaError,
    NinjaNotFoundError,
    NinjaStrategy,
    build_web_url,
)

from ._common import ninja_config, zunivers_cmd


class NinjaMixin:
    """On-demand /zunivers ninja command."""

    @zunivers_cmd.subcommand(
        sub_cmd_name="ninja",
        sub_cmd_description="Affiche le meilleur plan ZUnivers du jour calculé par ZUnivers Ninja",
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
        url = ninja_config.get("apiUrl")
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
        await ctx.send(choices=await strategy_choices(ctx.input_text))


# /zunivers journa set value that clears a stored strategy (Ninja's own default applies again).
DEFAULT_STRATEGY_CHOICE = "defaut"


async def ninja_strategies() -> list[NinjaStrategy]:
    """Strategies listed by the configured Ninja server (``GET /api/strategies``).

    Empty when Ninja isn't configured, is too old to list them, or can't be reached.
    """
    url = ninja_config.get("apiUrl")
    if not url:
        return []
    try:
        return await NinjaClient(url).get_strategies()
    except NinjaError:
        return []


async def strategy_choices(typed: str, *, with_default: bool = False) -> list[dict[str, str]]:
    """Autocomplete choices for a strategy option, filtered by what the user typed."""
    typed = typed.lower()
    choices = [
        {"name": _choice_name(st.label, st.description), "value": st.id}
        for st in await ninja_strategies()
        if typed in st.id.lower() or typed in st.label.lower()
    ]
    if with_default and (typed in "par défaut" or typed in DEFAULT_STRATEGY_CHOICE):
        choices.insert(
            0,
            {"name": "Par défaut (celle de ZUnivers Ninja)", "value": DEFAULT_STRATEGY_CHOICE},
        )
    return choices[:25]


def _web_link(username: str, hardcore: bool, strategy: str | None) -> list[ActionRow] | None:
    """Link button to the player's plan on the Ninja web UI, when ``zuniversNinja.webUrl`` is set.

    Not shortened: the player's page is a stable URL, as are the Ninja links in the embed.
    """
    web_url = ninja_config.get("webUrl")
    if not web_url:
        return None
    link = build_web_url(web_url, username, hardcore=hardcore, strategy=strategy)
    return [ActionRow(Button(style=ButtonStyle.LINK, label="Voir sur ZUnivers Ninja", url=link))]


def _choice_name(label: str, description: str) -> str:
    """Autocomplete entry: label plus description, within Discord's 100 characters."""
    name = f"{label} — {description}" if description else label
    return name if len(name) <= 100 else name[:99] + "…"
