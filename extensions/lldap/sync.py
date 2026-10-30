"""Periodic role → LLDAP group sync."""

import asyncio
from collections.abc import Iterable

from interactions import IntervalTrigger, Task

from features.lldap import GuildRoster, SyncReport, merge_rosters, plan_sync
from src.core import logging as logutil
from src.integrations.lldap import LldapError, LldapSettings

from ._common import (
    CHUNK_TIMEOUT_SECONDS,
    SYNC_MINUTES,
    enabled_servers,
    guild_role_groups,
    guild_settings,
)

logger = logutil.init_logger(__name__)


class SyncMixin:
    """Keeps each LLDAP server's mapped groups in line with the Discord roles."""

    def _warn_once(self, key: str, message: str, *args: object) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(message, *args)

    @Task.create(IntervalTrigger(minutes=SYNC_MINUTES))
    async def sync_roles(self):
        await self.run_sync()

    async def run_sync(self, guild_ids: Iterable[str] | None = None) -> SyncReport:
        """Sync the LLDAP servers of *guild_ids* (default: every enabled guild).

        Every enabled guild sharing one of those servers takes part too, so a
        shared group is never synced from a partial set of guilds.
        """
        async with self._sync_lock:
            configured: dict[str, LldapSettings] = {}
            for guild_id in enabled_servers:
                settings = guild_settings(guild_id)
                if settings.configured:
                    configured[guild_id] = settings
                else:
                    self._warn_once(
                        f"unconfigured:{guild_id}", "Guild %s: LLDAP connection unset", guild_id
                    )
            targets = (
                configured
                if guild_ids is None
                else {g: configured[g] for g in guild_ids if g in configured}
            )
            keys = {settings.instance_key for settings in targets.values()}

            servers: dict[str, LldapSettings] = {}
            rosters: list[GuildRoster] = []
            for guild_id, settings in configured.items():
                if settings.instance_key not in keys:
                    continue
                servers.setdefault(settings.instance_key, settings)
                rosters.append(await self._guild_roster(guild_id, settings.instance_key))

            desired, blocked = merge_rosters(rosters)
            report = SyncReport()
            for groups in blocked.values():
                report.blocked_groups.update(groups)
            for key, groups in desired.items():
                if groups:
                    await self._sync_server(servers[key], groups, report)
            if report.added or report.removed or report.failed:
                logger.info(
                    "LLDAP sync: %d added, %d removed, %d failed",
                    report.added,
                    report.removed,
                    report.failed,
                )
            return report

    async def _guild_roster(self, guild_id: str, instance_key: str) -> GuildRoster:
        """The guild's role holders per LLDAP group, with the groups it couldn't read."""
        mapping, invalid = guild_role_groups(guild_id)
        if invalid:
            self._warn_once(
                f"invalid:{guild_id}:{invalid}",
                "Guild %s: ignoring invalid LLDAP role mappings %s",
                guild_id,
                invalid,
            )
        roster = GuildRoster(instance_key)
        if not mapping:
            return roster
        groups = set(mapping.values())
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            self._warn_once(
                f"guild:{guild_id}", "Guild %s: not in cache, LLDAP sync skipped", guild_id
            )
            roster.blocked |= groups
            return roster
        if not guild.chunked.is_set():
            # The member cache is only complete once chunked; GUILD_MEMBERS events keep it so.
            try:
                await asyncio.wait_for(
                    guild.gateway_chunk(presences=False), timeout=CHUNK_TIMEOUT_SECONDS
                )
            except TimeoutError:
                logger.warning("Guild %s: member chunking timed out, LLDAP sync skipped", guild_id)
                roster.blocked |= groups
                return roster
        members = [member for member in guild.members if not member.bot]
        for role_id, group_id in mapping.items():
            if guild.get_role(role_id) is None:
                # A deleted role (or a typo) must not empty its group.
                self._warn_once(
                    f"role:{guild_id}:{role_id}",
                    "Guild %s: role %s not found, LLDAP group %s left alone",
                    guild_id,
                    role_id,
                    group_id,
                )
                roster.blocked.add(group_id)
                continue
            holders = roster.holders.setdefault(group_id, set())
            holders.update(str(member.id) for member in members if member.has_role(role_id))
        return roster

    async def _sync_server(
        self, settings: LldapSettings, desired: dict[int, set[str]], report: SyncReport
    ) -> None:
        key = settings.instance_key
        try:
            users = await self.lldap.list_users(settings)
        except LldapError as e:
            if key not in self._failing:
                logger.warning("LLDAP %s: %s; retrying quietly", key, e)
            self._failing.add(key)
            report.errors.append(str(e))
            return
        if key in self._failing:
            logger.info("LLDAP %s reachable again", key)
            self._failing.discard(key)
        for change in plan_sync(users, desired):
            try:
                if change.add:
                    await self.lldap.add_to_group(settings, change.user_id, change.group_id)
                    report.added += 1
                else:
                    await self.lldap.remove_from_group(settings, change.user_id, change.group_id)
                    report.removed += 1
            except LldapError as e:
                logger.error(
                    "LLDAP %s: could not %s %s %s group %s: %s",
                    key,
                    "add" if change.add else "remove",
                    change.user_id,
                    "to" if change.add else "from",
                    change.group_id,
                    e,
                )
                report.failed += 1
                continue
            logger.info(
                "LLDAP %s: %s %s %s group %s",
                key,
                "added" if change.add else "removed",
                change.user_id,
                "to" if change.add else "from",
                change.group_id,
            )
