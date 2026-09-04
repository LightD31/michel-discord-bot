"""Mirror the tracked edition as a Discord scheduled event.

Discord announces a guild scheduled event to members, lets them RSVP and shows
its dates in their own timezone — everything the pinned tracker message cannot
do. The event is created once, kept in step with the edition's phase, and ended
when the edition is over.
"""

import contextlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from interactions import (
    Client,
    Guild,
    Message,
    ScheduledEvent,
    ScheduledEventStatus,
    ScheduledEventType,
)
from interactions.client.errors import NotFound

from features.zevent.discord_event import (
    ACTIVE,
    COMPLETED,
    SCHEDULED,
    ScheduledEventPlan,
    plan_scheduled_event,
)
from src.core import logging as logutil
from src.core.http import http_client

from ._common import (
    EVENT_COVER_URL,
    GUILD_ID,
    MANAGE_DISCORD_EVENT,
    STATS_SITE_URL,
    TWITCH_URL,
    WEBSITE_URL,
)

logger = logutil.init_logger(os.path.basename(__file__))

# Discord refuses a start in the past, so an edition already under way gets an
# event that starts a few seconds out and is switched to ACTIVE right after.
START_LEAD = timedelta(seconds=5)
# Shortest event Discord is asked to hold, for the pathological case where the
# edition ends within seconds of the event being created.
MIN_DURATION = timedelta(minutes=1)
# Discord echoes back the timestamps it stored, which need not match ours to
# the microsecond; only a real schedule change should trigger an edit.
SAME_INSTANT_TOLERANCE = timedelta(seconds=30)
# Discord rejects an oversized cover outright; refusing to upload one is a
# clearer failure than a 400 buried in the refresh loop.
MAX_COVER_BYTES = 8 * 1024 * 1024

# Statuses a recovered event can still be driven from. A completed or canceled
# one is history: adopting it would only produce failing edits.
LIVE_STATUSES = frozenset({ScheduledEventStatus.SCHEDULED, ScheduledEventStatus.ACTIVE})

# Both inputs are process-level config, so the mismatch is worth saying once at
# startup rather than on every refresh cycle.
if MANAGE_DISCORD_EVENT and not (TWITCH_URL or WEBSITE_URL):
    logger.warning(
        "Zevent: événement Discord demandé mais ni chaîne Twitch ni site configurés — ignoré."
    )

_STATUS_BY_DISCORD = {
    ScheduledEventStatus.SCHEDULED: SCHEDULED,
    ScheduledEventStatus.ACTIVE: ACTIVE,
    ScheduledEventStatus.COMPLETED: COMPLETED,
}


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    """Compare two timestamps loosely enough to ignore storage rounding."""
    if left is None or right is None:
        return left is right
    return abs(left - right) <= SAME_INSTANT_TOLERANCE


class DiscordEventMixin:
    """Create, refresh and end the guild scheduled event for the edition."""

    # Declared here (rather than inferred from ``Zevent.__init__``) so mypy
    # sees the nullable cache slots for what they are.
    _scheduled_event: ScheduledEvent | None
    _applied_plan: ScheduledEventPlan | None
    _applied_cover_url: str | None
    _last_event_total: float | None
    _event_finished: bool
    _event_title: str
    _event_start: datetime
    _main_event_start: datetime
    _event_end: datetime | None
    client: Client
    message: Message | None

    # ─── Location ─────────────────────────────────────────────────────

    @staticmethod
    def _event_location(event: ScheduledEvent) -> str:
        """The external location of an event, or ``""`` when it carries none.

        Read straight off ``entity_metadata`` (a plain dict here) rather than
        through ``ScheduledEvent.location``, which raises on an event whose
        metadata has no location at all.
        """
        metadata = event.entity_metadata
        if isinstance(metadata, dict):
            return str(metadata.get("location") or "")
        return ""

    def _event_enabled(self) -> bool:
        """True when the dashboard asked for the event *and* it can be located.

        An external Discord event must declare a location, and the only ones
        the tracker knows are the two configured URLs — no URL is hardcoded, so
        having neither simply turns the feature off.
        """
        return bool(MANAGE_DISCORD_EVENT and (TWITCH_URL or WEBSITE_URL) and GUILD_ID is not None)

    @staticmethod
    def _known_locations() -> set[str]:
        """Every place a bot-owned event may point at, lowercased.

        The location moves with the edition's phase, so recovery has to accept
        either: matching only the current one would miss an event created
        during the concert and leave the loop creating a duplicate.
        """
        return {url.lower() for url in (TWITCH_URL, WEBSITE_URL) if url}

    async def _fetch_cover(self) -> bytes | None:
        """Download the configured cover, or ``None`` when there is none to use.

        Recorded as attempted either way by the caller: the cover is decoration,
        so a URL that 404s or times out must not be re-fetched on every refresh.
        """
        if not EVENT_COVER_URL:
            return None
        try:
            session = await http_client.session()
            async with session.get(EVENT_COVER_URL) as response:
                if response.status != 200:
                    logger.warning(
                        f"Zevent: couverture indisponible (HTTP {response.status}), ignorée."
                    )
                    return None
                data = await response.read()
        except Exception as e:
            logger.warning(f"Zevent: couverture inaccessible ({e}), ignorée.")
            return None

        if len(data) > MAX_COVER_BYTES:
            logger.warning(f"Zevent: couverture trop lourde ({len(data)} octets), ignorée.")
            return None
        return data

    async def _pending_cover(self) -> bytes | None:
        """The cover to upload now, or ``None`` when the event already has it.

        The configured URL is read at import, so this fires at most once per
        process — on creation, or on the first refresh after a restart or a
        dashboard reload picked up a new URL.
        """
        if not EVENT_COVER_URL or self._applied_cover_url == EVENT_COVER_URL:
            return None
        cover = await self._fetch_cover()
        # Stamped even on failure, so a broken URL costs one attempt, not one
        # per refresh cycle.
        self._applied_cover_url = EVENT_COVER_URL
        return cover

    async def _event_guild(self) -> Guild | None:
        if GUILD_ID is None:
            return None
        try:
            return await self.client.fetch_guild(GUILD_ID)
        except Exception as e:
            logger.error(f"Zevent: serveur {GUILD_ID} introuvable pour l'événement Discord : {e}")
            return None

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def recover_scheduled_event(self) -> None:
        """Re-attach the event this bot already created, across restarts.

        Matched on the external location (the configured Twitch channel) the
        same way the Twitch extension disambiguates its own events — the bot
        creates exactly one Zevent event per guild.
        """
        self._scheduled_event = None
        self._applied_plan = None
        self._applied_cover_url = None
        if not self._event_enabled():
            return

        guild = await self._event_guild()
        if guild is None:
            return

        expected = self._known_locations()
        try:
            events = await guild.list_scheduled_events()
        except Exception as e:
            logger.error(f"Zevent: impossible de lister les événements du serveur : {e}")
            return

        for event in events:
            if event.status not in LIVE_STATUSES:
                continue
            if self._event_location(event).lower() not in expected:
                continue
            try:
                creator = await event.creator
            except Exception:
                continue
            if creator and self.client.user and creator.id == self.client.user.id:
                self._scheduled_event = event
                logger.info(f"Zevent: événement Discord existant récupéré ({event.name})")
                return

    # ─── Refresh ──────────────────────────────────────────────────────

    def _build_plan(
        self, data: Any, streamlabs_data: Any, concert_active: bool, finished: bool = False
    ) -> ScheduledEventPlan:
        if isinstance(data, dict) and self._is_event_started():
            _, self._last_event_total = self.get_total_amount(
                data, streamlabs_data if isinstance(streamlabs_data, dict) else None
            )
        # A failed fetch must not walk the announced total back: the figure
        # only ever grows, so the last one known stays truthful.
        total = self._last_event_total
        # ``/zevent_finish`` sticks: the refresh loop keeps running afterwards,
        # and it must not resurrect the event it just closed.
        self._event_finished = self._event_finished or finished
        return plan_scheduled_event(
            title=self._event_title,
            event_start=self._event_start,
            main_event_start=self._main_event_start,
            event_end=self._event_end,
            now=datetime.now(UTC),
            total=total,
            concert_active=concert_active,
            finished=self._event_finished,
            tracker_url=self.message.jump_url if self.message else None,
            stats_url=STATS_SITE_URL or None,
            twitch_url=TWITCH_URL,
            website_url=WEBSITE_URL,
        )

    async def sync_scheduled_event(
        self, data: Any, streamlabs_data: Any, concert_active: bool, finished: bool = False
    ) -> None:
        """Bring the guild's scheduled event in line with the current phase.

        Never lets the event break the refresh loop: the pinned message is what
        members actually read, and it must render even if Discord refuses an
        event edit.
        """
        if not self._event_enabled():
            return
        try:
            plan = self._build_plan(data, streamlabs_data, concert_active, finished)
            if plan.status == COMPLETED:
                await self._end_scheduled_event()
            elif self._scheduled_event is None:
                await self._create_scheduled_event(plan)
            else:
                await self._update_scheduled_event(plan)
        except Exception as e:
            logger.error(f"Zevent: mise à jour de l'événement Discord impossible : {e}")

    async def _create_scheduled_event(self, plan: ScheduledEventPlan) -> None:
        guild = await self._event_guild()
        if guild is None:
            return

        start = plan.start
        if plan.status == ACTIVE:
            start = datetime.now(UTC) + START_LEAD
        end = max(plan.end, start + MIN_DURATION)

        cover = await self._pending_cover()
        event = await guild.create_scheduled_event(
            name=plan.name,
            event_type=ScheduledEventType.EXTERNAL,
            external_location=plan.location,
            start_time=start,
            end_time=end,
            description=plan.description,
            # ``None`` is what the library itself treats as "no cover".
            cover_image=cover,
        )
        if plan.status == ACTIVE:
            await event.edit(status=ScheduledEventStatus.ACTIVE)
        self._scheduled_event = event
        self._applied_plan = plan
        logger.info(f"Zevent: événement Discord créé ({plan.name}, {plan.status})")

    def _known_state(
        self, event: ScheduledEvent
    ) -> tuple[str, str, str, datetime, datetime | None, str]:
        """What Discord is believed to hold for the event right now.

        ``ScheduledEvent.edit()`` does not refresh the model it is called on,
        so the fetched object is only trustworthy until the first edit — after
        that the last plan pushed is the accurate record. Diffing against the
        stale model instead would re-send the same edit every refresh cycle.
        """
        applied = self._applied_plan
        if applied is not None:
            return (
                applied.name,
                applied.description,
                applied.location,
                applied.start,
                applied.end,
                applied.status,
            )
        return (
            event.name or "",
            event.description or "",
            self._event_location(event),
            event.start_time,
            event.end_time,
            _STATUS_BY_DISCORD.get(event.status, SCHEDULED),
        )

    async def _update_scheduled_event(self, plan: ScheduledEventPlan) -> None:
        """Edit only what actually changed, then start the event when due."""
        event = self._scheduled_event
        if event is None:
            return

        name, description, location, start, end, status = self._known_state(event)

        changes: dict[str, Any] = {}
        if name != plan.name:
            changes["name"] = plan.name
        if description != plan.description:
            changes["description"] = plan.description
        # Moves once per edition, when the marathon takes over from the concert.
        if plan.location and location != plan.location:
            changes["external_location"] = plan.location
        # Discord rejects moving the start of an event already running, so the
        # start is only corrected while the event is still announced.
        if status == SCHEDULED and not _same_instant(start, plan.start):
            changes["start_time"] = plan.start
        if not _same_instant(end, plan.end):
            changes["end_time"] = plan.end
        cover = await self._pending_cover()
        if cover is not None:
            changes["cover_image"] = cover

        try:
            if changes:
                await event.edit(**changes)
                logger.debug(f"Zevent: événement Discord mis à jour ({', '.join(changes)})")
            if plan.status == ACTIVE and status == SCHEDULED:
                await event.edit(status=ScheduledEventStatus.ACTIVE)
                logger.info("Zevent: événement Discord démarré")
            self._applied_plan = plan
        except NotFound:
            # Deleted from Discord's side; the next cycle recreates it.
            logger.warning("Zevent: événement Discord supprimé, il sera recréé")
            self._scheduled_event = None
            self._applied_plan = None

    async def _end_scheduled_event(self) -> None:
        """Close the event: complete a running one, cancel an announced one."""
        event = self._scheduled_event
        if event is None:
            return
        # The model does not reflect our own edits, so the status we pushed is
        # what says whether the event is running — and a running event is
        # completed rather than deleted, so it stays in the guild's history.
        *_, status = self._known_state(event)
        self._scheduled_event = None
        self._applied_plan = None
        try:
            if status == ACTIVE:
                await event.edit(status=ScheduledEventStatus.COMPLETED)
                logger.info("Zevent: événement Discord terminé")
            else:
                with contextlib.suppress(NotFound):
                    await event.delete()
                logger.info("Zevent: événement Discord annulé")
        except NotFound:
            pass
        except Exception as e:
            # Discord ends an external event on its own once end_time passes,
            # so a refused transition here is expected rather than alarming.
            logger.debug(f"Zevent: clôture de l'événement Discord refusée : {e}")
