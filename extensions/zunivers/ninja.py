"""NinjaMixin — ZUnivers Ninja plans: automatic posts when a plan changes, and /ninja.

ZUnivers Ninja runs as its own service; the bot only queries its
``/api/plan/{pseudo}/discord`` route and posts the ready-made message. The last
ETag handled per pseudo is stored in MongoDB, so a plan is posted once and a
restart does not repost it. Leave ``ZU_USERS`` / ``WEBHOOK_URL`` empty on the
Ninja side to avoid duplicates.
"""

import io

from interactions import (
    AllowedMentions,
    Embed,
    File,
    GuildText,
    IntervalTrigger,
    Member,
    OptionType,
    SlashContext,
    Task,
    User,
    slash_command,
    slash_option,
)

from features.zunivers_ninja import (
    NinjaClient,
    NinjaError,
    NinjaNotFoundError,
    NinjaPlan,
    NinjaRepository,
)
from src.core.errors import DatabaseError
from src.discord_ext.links import shorten_embeds

from ._common import enabled_servers, logger, module_config


def _ninja_users() -> list[str]:
    return [u.strip() for u in module_config.get("ninjaUsers") or [] if u and u.strip()]


def _ninja_ruleset() -> str:
    return "HARDCORE" if module_config.get("ninjaHardcore") else "NORMAL"


class NinjaMixin:
    """Posts ZUnivers Ninja plans when they change + on-demand /ninja command."""

    def _ninja_client(self) -> NinjaClient | None:
        url = module_config.get("ninjaUrl")
        return NinjaClient(url) if url else None

    async def _get_ninja_channel(self) -> GuildText | None:
        channel_id = module_config.get("ninjaChannelId")
        if not channel_id:
            return await self._get_zunivers_channel()
        try:
            channel = await self.bot.fetch_channel(channel_id)
        except Exception as e:
            logger.error(f"Could not fetch ZUnivers Ninja channel: {e}")
            return None
        return channel if isinstance(channel, GuildText) else None

    @staticmethod
    async def _plan_message(plan: NinjaPlan, *, mention: bool) -> dict:
        """Keyword arguments for ``send()`` built from a Ninja plan."""
        embeds = await shorten_embeds(Embed.from_dict(e) for e in plan.embeds)
        files = (
            [File(io.BytesIO(plan.attachment.content.encode()), file_name=plan.attachment.name)]
            if plan.attachment
            else None
        )
        allowed = (
            AllowedMentions(users=[plan.discord_id])
            if mention and plan.discord_id
            else AllowedMentions.none()
        )
        return {
            "content": plan.content or None,
            "embeds": embeds,
            "files": files,
            "allowed_mentions": allowed,
        }

    @Task.create(IntervalTrigger(minutes=30))
    async def ninja_checker(self):
        """Post each followed player's plan when it changed since the last post."""
        client = self._ninja_client()
        users = _ninja_users()
        if client is None or not users or not enabled_servers:
            return
        channel = await self._get_ninja_channel()
        if channel is None:
            logger.warning("No channel for ZUnivers Ninja plans, skipping")
            return

        repo = NinjaRepository(enabled_servers[0])
        mention = bool(module_config.get("ninjaMention"))
        for pseudo in users:
            try:
                etag = await repo.get_etag(pseudo)
                plan = await client.get_plan(
                    pseudo, etag=etag, ruleset=_ninja_ruleset(), mention=mention
                )
                if plan is None:
                    continue  # 304: unchanged since the last post
                if not plan.empty:
                    await channel.send(**await self._plan_message(plan, mention=mention))
                    logger.info(f"ZUnivers Ninja plan posted for {pseudo}")
                await repo.save_etag(pseudo, plan.etag, plan.hash)
            except NinjaError as e:
                logger.warning(f"ZUnivers Ninja plan for {pseudo} unavailable: {e}")
            except DatabaseError as e:
                logger.error(f"Could not read/save the ZUnivers Ninja state for {pseudo}: {e}")

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
    async def ninja_command(self, ctx: SlashContext, membre: User | Member | None = None):
        client = self._ninja_client()
        if client is None:
            await ctx.send("ZUnivers Ninja n'est pas configuré sur ce serveur.", ephemeral=True)
            return

        # The ZUnivers pseudo is the player's Discord username.
        user = membre or ctx.author
        pseudo = user.username
        await ctx.defer()
        try:
            plan = await client.get_plan(pseudo, ruleset=_ninja_ruleset())
        except NinjaNotFoundError as e:
            await ctx.send(f"❌ {e}")
            return
        except NinjaError as e:
            await ctx.send(f"⚠️ {e}")
            return
        if plan is None or plan.empty:
            name = plan.player_display_name if plan else user.display_name
            await ctx.send(f"✅ Rien de plus à faire aujourd'hui pour **{name}**.")
            return
        await ctx.send(**await self._plan_message(plan, mention=False))
