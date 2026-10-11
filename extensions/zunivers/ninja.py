"""NinjaMixin — /ninja: a player's best ZUnivers plan of the day, from ZUnivers Ninja.

ZUnivers Ninja runs as its own service; the bot queries its
``/api/plan/{pseudo}/discord`` route and posts the ready-made message. The
ZUnivers pseudo is the player's Discord username.
"""

import io

from interactions import (
    ActionRow,
    AllowedMentions,
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

from features.links import shorten_url
from features.zunivers_ninja import NinjaClient, NinjaError, NinjaNotFoundError, build_web_url
from src.discord_ext.links import shorten_embeds

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
                user.username, ruleset="HARDCORE" if hardcore else "NORMAL"
            )
        except NinjaNotFoundError as e:
            await ctx.send(f"❌ {e}", ephemeral=ephemeral)
            return
        except NinjaError as e:
            await ctx.send(f"⚠️ {e}", ephemeral=ephemeral)
            return

        components = await _web_link(user.username, hardcore)
        if plan.empty:
            await ctx.send(
                f"✅ Rien de plus à faire aujourd'hui pour **{plan.player_display_name}**.",
                components=components,
                ephemeral=ephemeral,
            )
            return

        embeds = await shorten_embeds(Embed.from_dict(e) for e in plan.embeds)
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


async def _web_link(username: str, hardcore: bool) -> list[ActionRow] | None:
    """Link button to the player's plan on the Ninja web UI, when ``ninjaWebUrl`` is set."""
    web_url = module_config.get("ninjaWebUrl")
    if not web_url:
        return None
    link = await shorten_url(build_web_url(web_url, username, hardcore=hardcore))
    return [ActionRow(Button(style=ButtonStyle.LINK, label="Voir sur ZUnivers Ninja", url=link))]
