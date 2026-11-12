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

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
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
from interactions.client.errors import BadRequest

from features.links import shorten_url
from features.rss import RssEntry, RssFeedState, RssRepository
from features.rss.network import fetch_feed
from features.rss.scheduling import is_due, next_poll_at
from src.core.errors import DatabaseError, IntegrationError
from src.core.http import BlockedUrlError
from src.core.logging import log_failure
from src.core.text import (
    EMBED_AUTHOR_LIMIT,
    EMBED_DESCRIPTION_LIMIT,
    EMBED_FOOTER_LIMIT,
    EMBED_TITLE_LIMIT,
    clip,
    format_template,
)
from src.core.urls import redact_url
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.mentions import mentions_written_in
from src.discord_ext.messages import require_guild, send_error, send_success
from src.discord_ext.scopes import guild_scopes

from ._common import (
    DEFAULT_TEMPLATE,
    MAX_CONCURRENT_FETCHES,
    MAX_NEW_PER_POLL,
    RSS,
    logger,
)


@dataclass(frozen=True)
class FeedJob:
    """One guild's feed due this cycle, with its state as read at the start of it."""

    guild_id: str
    feed_id: str
    feed_cfg: dict
    default_channel_id: str
    state: RssFeedState | None


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


def _published_tokens(entry: RssEntry) -> dict[str, str]:
    """Return Discord timestamp tags for ``entry.published``.

    Each style mirrors a Discord timestamp format identifier — clients render
    them in the viewer's locale and timezone. Empty strings if the feed entry
    didn't expose a publish/updated date.
    """
    if not entry.published:
        return {
            "published": "",
            "published_short": "",
            "published_long": "",
            "published_relative": "",
            "published_date": "",
            "published_iso": "",
        }
    ts = int(entry.published.timestamp())
    return {
        "published": f"<t:{ts}:F>",
        "published_short": f"<t:{ts}:f>",
        "published_long": f"<t:{ts}:F>",
        "published_relative": f"<t:{ts}:R>",
        "published_date": f"<t:{ts}:D>",
        "published_iso": entry.published.isoformat(),
    }


def _format_template(template: str, *, label: str, entry: RssEntry, fallback: str) -> str:
    """Fill the guild's template with the entry; a template that can't be filled gives *fallback*.

    ``{name}`` placeholders only (:func:`format_template`): any other mistake
    (``{title:d}``, ``{title.x}``, an unknown name) falls back instead of
    failing the post, which left the entry unseen and retried forever.
    """
    try:
        return format_template(
            template,
            title=entry.title,
            link=entry.link,
            summary=entry.summary,
            author=entry.author,
            label=label,
            image=entry.image_url,
            **_published_tokens(entry),
        )
    except (KeyError, ValueError):
        return fallback


def _render_text(feed_id: str, feed_cfg: dict, entry: RssEntry) -> str:
    """Render a plain-text/markdown message for ``mode="text"``.

    Clipped to Discord's 2000-char message-content limit; the parser caps the
    summary near the embed limit so the same summary fed into a text template
    can occasionally need clipping here.
    """
    label = feed_cfg.get("label") or feed_id
    template = feed_cfg.get("template") or DEFAULT_TEMPLATE
    fallback = DEFAULT_TEMPLATE.format(
        title=entry.title, link=entry.link, label=label, summary="", author="", image=""
    )
    rendered = _format_template(template, label=label, entry=entry, fallback=fallback)
    if len(rendered) > 2000:
        rendered = rendered[:1999].rstrip() + "…"
    return rendered


async def _with_short_link(entry: RssEntry, feed_id: str) -> RssEntry:
    """Return *entry* with its link routed through Shlink, when enabled.

    Shortening happens once per entry, before rendering, so the text template,
    the embed URL and the link button all point at the same short URL. Returns
    the entry untouched when shortening is off or the call fails.
    """
    if not entry.link:
        return entry
    short = await shorten_url(entry.link, title=entry.title or None, tags=["rss", feed_id])
    if short == entry.link:
        return entry
    return entry.model_copy(update={"link": short})


def _parse_color(value: object) -> int | None:
    """Decode a hex/integer color into an ``int``. ``None`` if unparseable."""
    if isinstance(value, int) and 0 <= value <= 0xFFFFFF:
        return value
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if not text:
            return None
        try:
            parsed = int(text, 16)
        except ValueError:
            return None
        if 0 <= parsed <= 0xFFFFFF:
            return parsed
    return None


def _render_embed(feed_id: str, feed_cfg: dict, entry: RssEntry) -> Embed:
    """Render a Discord ``Embed`` for ``mode="embed"``.

    Templates are independent: ``embed_title`` (default ``{title}``),
    ``embed_description`` (default ``{summary}``). The entry's link drops
    onto ``embed.url`` so the title becomes a clickable header. When
    ``embed_show_image`` is true and the entry has an image, it's set as the
    embed image.
    """
    label = feed_cfg.get("label") or feed_id
    title_tpl = feed_cfg.get("embed_title") or "{title}"
    desc_tpl = feed_cfg.get("embed_description") or "{summary}"
    title = _format_template(title_tpl, label=label, entry=entry, fallback=entry.title)
    description = _format_template(desc_tpl, label=label, entry=entry, fallback=entry.summary)

    embed = Embed(
        title=clip(title, EMBED_TITLE_LIMIT) if title else "(sans titre)",
        description=clip(description, EMBED_DESCRIPTION_LIMIT) if description else None,
        url=entry.link or None,
        color=_parse_color(feed_cfg.get("embed_color")) or Colors.UTIL,
    )
    if feed_cfg.get("embed_show_image", True) and entry.image_url:
        try:
            embed.set_image(url=entry.image_url)
        except Exception as e:  # noqa: BLE001 — bad URLs shouldn't lose the post
            logger.debug("Could not set embed image %s: %s", entry.image_url, e)
    if entry.author:
        embed.set_author(name=clip(entry.author, EMBED_AUTHOR_LIMIT))
    if entry.published:
        embed.timestamp = entry.published
    embed.set_footer(text=clip(label, EMBED_FOOTER_LIMIT))
    return embed


def _build_link_button(feed_cfg: dict, entry: RssEntry) -> list[ActionRow] | None:
    """Build a Discord link-button row for the entry URL when enabled.

    Returns ``None`` when the feature is off or the entry has no link, since
    Discord rejects link buttons without a URL.
    """
    if not feed_cfg.get("embed_show_link_button"):
        return None
    if not entry.link:
        return None
    raw_label = (feed_cfg.get("embed_link_button_label") or "").strip()
    label = raw_label[:80] if raw_label else "Voir l'article"
    try:
        return [ActionRow(Button(style=ButtonStyle.LINK, url=entry.link, label=label))]
    except Exception as e:  # noqa: BLE001 — bad URL shouldn't lose the post
        logger.debug("Could not build RSS link button for %s: %s", entry.link, e)
        return None


def _poll_minutes(srv_config: dict) -> int:
    """Pull the configured poll interval, clamped to a sane floor of 5 minutes."""
    raw = srv_config.get("rssPollMinutes", 15)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 15
    return max(5, value)


class RssExtension(Extension):
    MODULE_KEYS = ("moduleRss",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._poll_lock = asyncio.Lock()

    def _repo(self, guild_id: str | int) -> RssRepository:
        return RssRepository(str(guild_id))

    @listen()
    async def on_startup(self):
        logger.info("RSS extension ready (%d guild(s))", len(RSS.guild_ids()))
        if not self.check_feeds.running:
            self.check_feeds.start()

    # ------------------------------------------------------------------
    # Polling task
    # ------------------------------------------------------------------

    @Task.create(IntervalTrigger(minutes=5))
    async def check_feeds(self) -> None:
        """Poll every guild's feeds that are due.

        Runs every 5 minutes; a feed is fetched once its guild's
        ``rssPollMinutes`` have passed since its last poll, longer after
        consecutive failures (:mod:`features.rss.scheduling`). A URL several
        guilds follow is fetched once, and up to ``MAX_CONCURRENT_FETCHES``
        URLs are fetched at a time. A cycle still running when the next one
        fires (slow feeds) makes the next one a no-op rather than a second
        round of posts.
        """
        if self._poll_lock.locked():
            logger.info("Previous RSS cycle still running, skipping this one")
            return
        async with self._poll_lock:
            jobs = await self._due_jobs(datetime.now())
            if not jobs:
                return
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
            await asyncio.gather(
                *(self._poll_url(url, url_jobs, semaphore) for url, url_jobs in jobs.items())
            )

    async def _due_jobs(self, now: datetime) -> dict[str, list[FeedJob]]:
        """The feeds to fetch this cycle, grouped by URL."""
        jobs: dict[str, list[FeedJob]] = {}
        for guild_id, srv_cfg in RSS.items():
            default_channel_id = srv_cfg.get("ChannelId")
            feeds = [(fid, cfg) for fid, cfg in _iter_feeds(srv_cfg) if cfg.get("url")]
            if not default_channel_id or not feeds:
                continue
            try:
                states = await self._repo(guild_id).states()
            except DatabaseError as e:
                logger.warning("Could not read the RSS state of guild %s: %s", guild_id, e)
                continue
            interval = timedelta(minutes=_poll_minutes(srv_cfg))
            for feed_id, feed_cfg in feeds:
                state = states.get(feed_id)
                if not is_due(state, interval, now):
                    continue
                jobs.setdefault(str(feed_cfg["url"]), []).append(
                    FeedJob(
                        guild_id=str(guild_id),
                        feed_id=feed_id,
                        feed_cfg=feed_cfg,
                        default_channel_id=str(default_channel_id),
                        state=state,
                    )
                )
        return jobs

    async def _poll_url(self, url: str, jobs: list[FeedJob], semaphore: asyncio.Semaphore) -> None:
        """Fetch *url* once and hand its entries to every feed following it."""
        error: str | None = None
        entries: list[RssEntry] = []
        async with semaphore:
            try:
                entries = await fetch_feed(url)
            except IntegrationError as e:
                logger.warning("RSS fetch failed for %s: %s", redact_url(url), e)
                error = str(e)[:300]
            except Exception as e:  # a parser bug must not stop the other feeds
                logger.exception("Unexpected error fetching %s: %s", redact_url(url), e)
                error = "Erreur inattendue (détails dans les logs du bot)"
        if error is None and not entries:
            error = "Feed returned no entries"
        for job in jobs:
            try:
                if error is not None:
                    await self._repo(job.guild_id).record_error(job.feed_id, error)
                else:
                    await self._deliver(job, entries)
            except DatabaseError as e:
                logger.warning("RSS state of %s/%s not saved: %s", job.guild_id, job.feed_id, e)
            except Exception as e:
                logger.exception("Unhandled error delivering feed %s: %s", job.feed_id, e)

    async def _deliver(self, job: FeedJob, entries: list[RssEntry]) -> None:
        """Post the entries *job*'s feed hasn't seen yet, oldest first."""
        repo = self._repo(job.guild_id)
        if job.state is None or not job.state.initialized:
            # First successful poll for this feed — seed the dedupe cache and
            # don't post the existing backlog.
            await repo.initialize(job.feed_id, [e.entry_id for e in entries])
            logger.info(
                "Initialized RSS feed %s for guild %s with %d back-entries",
                job.feed_id,
                job.guild_id,
                len(entries),
            )
            return

        seen = set(job.state.seen_ids)
        # Feeds usually expose newest first; we want to *post* oldest-new first
        # so the channel reads chronologically.
        new_entries = [e for e in entries if e.entry_id not in seen][:MAX_NEW_PER_POLL]
        if not new_entries:
            await repo.record_poll(job.feed_id, [])
            return
        new_entries.reverse()

        channel_id = job.feed_cfg.get("channel_id") or job.default_channel_id
        try:
            channel: BaseChannel = await self.bot.fetch_channel(int(channel_id))
        except Exception as e:
            log_failure(logger, "Could not fetch RSS channel %s: %s", channel_id, e)
            await repo.record_error(job.feed_id, f"Salon {channel_id} introuvable")
            return
        if not hasattr(channel, "send"):
            logger.error("Channel %s does not accept sends", channel_id)
            await repo.record_error(job.feed_id, f"Le salon {channel_id} n'accepte pas de messages")
            return

        mode = (job.feed_cfg.get("mode") or "text").strip().lower()
        posted_ids: list[str] = []
        for entry in new_entries:
            try:
                entry = await _with_short_link(entry, job.feed_id)
                components = _build_link_button(job.feed_cfg, entry)
                send_kwargs: dict = {}
                if components is not None:
                    send_kwargs["components"] = components
                if mode == "embed":
                    embed = _render_embed(job.feed_id, job.feed_cfg, entry)
                    await channel.send(embeds=[embed], **send_kwargs)  # type: ignore[union-attr]
                else:
                    # Only what the admin wrote in the template pings: a feed
                    # title saying "@everyone" must not.
                    template = job.feed_cfg.get("template") or DEFAULT_TEMPLATE
                    await channel.send(
                        _render_text(job.feed_id, job.feed_cfg, entry),
                        allowed_mentions=mentions_written_in(template),
                        **send_kwargs,  # type: ignore[union-attr]
                    )
                posted_ids.append(entry.entry_id)
            except BadRequest as e:
                # Discord refuses this entry's message itself (too long, an
                # invalid URL...): retrying it every cycle can only fail again.
                logger.warning("Discord refused RSS entry %s, skipping it: %s", entry.entry_id, e)
                posted_ids.append(entry.entry_id)
            except Exception as e:
                logger.warning("Failed to send RSS entry %s: %s", entry.entry_id, e)

        # Recorded in feed order (most recent first) so the head of seen_ids
        # always reflects the latest known entry. An entry that failed to send
        # for another reason (no permission, Discord down) stays unseen and is
        # tried again at the next poll.
        posted_ids.reverse()
        await repo.record_poll(job.feed_id, posted_ids)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @slash_command(
        name="rss",
        description="Outils RSS",
        sub_cmd_name="test",
        sub_cmd_description="Aperçu des dernières entrées d'un flux sans le configurer",
        scopes=guild_scopes(RSS.guild_ids()),
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
        except BlockedUrlError:
            await send_error(
                ctx,
                "Cette URL n'est pas autorisée : seules les adresses http(s) publiques le sont.",
            )
            return
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
            if entry.image_url:
                value_lines.append(f"🖼️ image détectée : {entry.image_url[:120]}")
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
        srv_cfg = RSS.get(ctx.guild_id)
        feeds = list(_iter_feeds(srv_cfg))
        if not feeds:
            await ctx.send("Aucun flux configuré.", ephemeral=True)
            return
        states = await self._repo(ctx.guild_id).states()
        interval = timedelta(minutes=_poll_minutes(srv_cfg))
        lines: list[str] = []
        for feed_id, feed_cfg in feeds:
            state = states.get(feed_id)
            label = feed_cfg.get("label") or feed_id
            url = feed_cfg.get("url", "?")
            status = "—"
            if state is not None:
                if state.last_error:
                    status = f"⚠️ {state.last_error[:80]}"
                    retry = next_poll_at(state, interval)
                    if state.failures > 1 and retry is not None:
                        status += (
                            f" ({state.failures} échecs de suite, nouvel essai "
                            f"{format_discord_timestamp(retry, 'R')})"
                        )
                elif state.last_poll_at:
                    status = format_discord_timestamp(state.last_poll_at, "R")
            lines.append(f"`{feed_id}` — **{label}**\n  <{url}>\n  Dernière relève : {status}")
        embed = Embed(
            title="Flux RSS configurés",
            description=clip("\n\n".join(lines), EMBED_DESCRIPTION_LIMIT),
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
