"""RSS / Atom Discord extension — generic feed poller.

Posts new entries from any RSS / Atom feed into a configured channel. The same
infrastructure powers Steam / Epic free-game alerts, subreddit ``.rss`` feeds,
news sites, and any other syndication source.

Slash commands (admin-only):
- ``/rss test url:<url>`` — preview the latest entries from a URL without
  registering it.
- ``/rss list`` — list configured feeds and their last-seen state.
- ``/rss reset feed_id:<id>`` — clear the dedupe state so the next poll
  re-initializes from scratch (useful if a feed swapped its guid scheme).

Per-guild config: ``moduleRss``. The polling task respects per-feed channel
overrides and per-feed message templates; otherwise it falls back to the
module-level default channel.
"""

from __future__ import annotations

from interactions import (
    BaseChannel,
    Client,
    Embed,
    Extension,
    IntervalTrigger,
    OptionType,
    Permissions,
    SlashContext,
    Task,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.rss import RssEntry, RssRepository
from features.rss.network import fetch_feed
from src.core.errors import IntegrationError
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import require_guild, send_error, send_success

from ._common import (
    DEFAULT_TEMPLATE,
    MAX_NEW_PER_POLL,
    enabled_servers,
    enabled_servers_int,
    logger,
    module_config,
)


def _iter_feeds(srv_config: dict):
    """Yield ``(feed_id, feed_cfg)`` from a guild's ``rssFeeds`` config field."""
    raw = srv_config.get("rssFeeds")
    if not isinstance(raw, dict):
        return
    for feed_id, cfg in raw.items():
        if not feed_id:
            continue
        if isinstance(cfg, dict):
            yield str(feed_id), cfg
        elif isinstance(cfg, str):
            # Shorthand: value is just a URL.
            yield str(feed_id), {"url": cfg}


def _render(template: str, *, feed_id: str, feed_cfg: dict, entry: RssEntry) -> str:
    """Render *template* against an entry, falling back to the default on error."""
    label = feed_cfg.get("label") or feed_id
    try:
        return template.format(
            title=entry.title,
            link=entry.link,
            summary=entry.summary,
            author=entry.author,
            label=label,
        )
    except (KeyError, IndexError):
        return DEFAULT_TEMPLATE.format(title=entry.title, link=entry.link, label=label)


def _poll_minutes(srv_config: dict) -> int:
    """Pull the configured poll interval, clamped to a sane floor of 5 minutes."""
    raw = srv_config.get("rssPollMinutes", 15)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 15
    return max(5, value)


class RssExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._repos: dict[str, RssRepository] = {}

    def _repo(self, guild_id: str | int) -> RssRepository:
        gid = str(guild_id)
        repo = self._repos.get(gid)
        if repo is None:
            repo = RssRepository(gid)
            self._repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self):
        logger.info("RSS extension ready (%d guild(s))", len(enabled_servers))
        self.check_feeds.start()

    # ------------------------------------------------------------------
    # Polling task
    # ------------------------------------------------------------------

    @Task.create(IntervalTrigger(minutes=5))
    async def check_feeds(self) -> None:
        """Poll every guild's configured feeds.

        Runs every 5 minutes — individual feeds with a longer ``rssPollMinutes``
        are skipped until enough time has elapsed since their last successful
        poll. This lets the WebUI tune cadence per guild without spawning a
        task per feed.
        """
        from datetime import datetime, timedelta

        now = datetime.now()
        for guild_id in enabled_servers:
            srv_cfg = module_config.get(str(guild_id), {})
            default_channel_id = srv_cfg.get("ChannelId")
            if not default_channel_id:
                continue
            poll_interval = timedelta(minutes=_poll_minutes(srv_cfg))
            for feed_id, feed_cfg in _iter_feeds(srv_cfg):
                url = feed_cfg.get("url")
                if not url:
                    continue
                state = await self._repo(guild_id).get(feed_id)
                if state and state.last_poll_at and (now - state.last_poll_at) < poll_interval:
                    continue
                try:
                    await self._poll_one(
                        guild_id=str(guild_id),
                        feed_id=feed_id,
                        feed_cfg=feed_cfg,
                        default_channel_id=str(default_channel_id),
                    )
                except Exception as e:
                    logger.exception("Unhandled error polling feed %s: %s", feed_id, e)

    async def _poll_one(
        self,
        *,
        guild_id: str,
        feed_id: str,
        feed_cfg: dict,
        default_channel_id: str,
    ) -> None:
        url = feed_cfg["url"]
        try:
            entries = await fetch_feed(url)
        except IntegrationError as e:
            logger.warning("RSS fetch failed for %s (%s): %s", feed_id, url, e)
            await self._repo(guild_id).record_error(feed_id, str(e)[:300])
            return

        if not entries:
            await self._repo(guild_id).record_error(feed_id, "Feed returned no entries")
            return

        state = await self._repo(guild_id).get(feed_id)
        if state is None or not state.initialized:
            # First successful poll for this feed — seed the dedupe cache and
            # don't post the existing backlog.
            await self._repo(guild_id).initialize(feed_id, [e.entry_id for e in entries])
            logger.info(
                "Initialized RSS feed %s for guild %s with %d back-entries",
                feed_id,
                guild_id,
                len(entries),
            )
            return

        seen = set(state.seen_ids)
        # Feeds usually expose newest first; we want to *post* oldest-new first
        # so the channel reads chronologically.
        new_entries = [e for e in entries if e.entry_id not in seen][:MAX_NEW_PER_POLL]
        if not new_entries:
            await self._repo(guild_id).record_seen(feed_id, [])
            return
        new_entries.reverse()

        channel_id = feed_cfg.get("channel_id") or default_channel_id
        try:
            channel: BaseChannel = await self.bot.fetch_channel(int(channel_id))
        except Exception as e:
            logger.error("Could not fetch RSS channel %s: %s", channel_id, e)
            return
        if not hasattr(channel, "send"):
            logger.error("Channel %s does not accept sends", channel_id)
            return

        template = feed_cfg.get("template") or DEFAULT_TEMPLATE
        posted_ids: list[str] = []
        for entry in new_entries:
            try:
                await channel.send(
                    _render(template, feed_id=feed_id, feed_cfg=feed_cfg, entry=entry)
                )  # type: ignore[union-attr]
                posted_ids.append(entry.entry_id)
            except Exception as e:
                logger.warning("Failed to send RSS entry %s: %s", entry.entry_id, e)

        if posted_ids:
            # Record in feed-order (most recent first) so the head of seen_ids
            # always reflects the latest known entry.
            posted_ids.reverse()
            await self._repo(guild_id).record_seen(feed_id, posted_ids)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @slash_command(
        name="rss",
        description="Outils RSS",
        sub_cmd_name="test",
        sub_cmd_description="Aperçu des dernières entrées d'un flux sans le configurer",
        scopes=enabled_servers_int,  # type: ignore[arg-type]
    )
    @slash_option(
        "url",
        "URL du flux RSS / Atom",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def rss_test(self, ctx: SlashContext, url: str) -> None:
        if not await require_guild(ctx):
            return
        await ctx.defer(ephemeral=True)
        try:
            entries = await fetch_feed(url)
        except IntegrationError as e:
            await send_error(ctx, f"Impossible de lire ce flux : {e}")
            return
        if not entries:
            await send_error(ctx, "Le flux est vide ou illisible.")
            return

        embed = Embed(
            title=f"📰 Aperçu — {url}",
            description=f"{len(entries)} entrée(s) détectée(s). Voici les 3 plus récentes :",
            color=Colors.UTIL,
        )
        for entry in entries[:3]:
            value_lines = []
            if entry.published:
                value_lines.append(format_discord_timestamp(entry.published, "R"))
            if entry.author:
                value_lines.append(f"par *{entry.author}*")
            if entry.summary:
                value_lines.append(entry.summary[:200])
            value_lines.append(f"[Lien]({entry.link})" if entry.link else "")
            embed.add_field(
                name=entry.title[:240] or "(sans titre)",
                value="\n".join(filter(None, value_lines)) or "—",
                inline=False,
            )
        await ctx.send(embeds=[embed], ephemeral=True)

    @rss_test.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Lister les flux RSS configurés",
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def rss_list(self, ctx: SlashContext) -> None:
        if not await require_guild(ctx):
            return
        srv_cfg = module_config.get(str(ctx.guild_id), {})
        feeds = list(_iter_feeds(srv_cfg))
        if not feeds:
            await ctx.send("Aucun flux configuré.", ephemeral=True)
            return
        lines: list[str] = []
        for feed_id, feed_cfg in feeds:
            state = await self._repo(ctx.guild_id).get(feed_id)
            label = feed_cfg.get("label") or feed_id
            url = feed_cfg.get("url", "?")
            status = "—"
            if state is not None:
                if state.last_error:
                    status = f"⚠️ {state.last_error[:80]}"
                elif state.last_poll_at:
                    status = format_discord_timestamp(state.last_poll_at, "R")
            lines.append(f"`{feed_id}` — **{label}**\n  <{url}>\n  Dernière relève : {status}")
        embed = Embed(
            title="Flux RSS configurés",
            description="\n\n".join(lines)[:4000],
            color=Colors.UTIL,
        )
        await ctx.send(embeds=[embed], ephemeral=True)

    @rss_test.subcommand(
        sub_cmd_name="reset",
        sub_cmd_description="Réinitialiser l'historique d'un flux (re-seed sans poster)",
    )
    @slash_option(
        "feed_id",
        "Identifiant interne du flux (visible via /rss list)",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def rss_reset(self, ctx: SlashContext, feed_id: str) -> None:
        if not await require_guild(ctx):
            return
        deleted = await self._repo(ctx.guild_id).delete(feed_id)
        if deleted == 0:
            await send_error(ctx, "Aucun état trouvé pour ce flux.")
            return
        await send_success(ctx, "État réinitialisé. Le prochain cycle re-seed le flux.")


def setup(bot: Client) -> None:
    RssExtension(bot)


__all__ = ["RssExtension", "setup"]
