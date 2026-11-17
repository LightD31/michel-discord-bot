"""What is wrong on a guild, for its admins to see (F08).

An admin whose RSS feed went silent, or whose channel lost the bot's rights,
used to see nothing: only a developer reading the logs could tell. The
server's "Santé" card gathers, from data the bot already keeps:

* each channel the guild's enabled modules point at, and the rights the bot
  lacks there (it can't post, embed, attach or read back its own messages);
* each RSS feed's last error and consecutive failures, and when it is next
  polled (the wait doubles with each failure, :mod:`features.rss.scheduling`);
* the periodic tasks of the extensions serving the guild: last success, last
  failure (:mod:`src.core.health`, which counts runs for every guild at once).

The channel check runs on the bot loop (:func:`channel_report`); the rest only
reads stored state.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from interactions import Permissions, Task, ThreadChannel

from features.rss import RssFeedState
from features.rss.scheduling import next_poll_at
from src.core.health import HealthSnapshot
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.task_health import task_name
from src.webui.context import declared_modules

# What every module needs from the bot in its channel, labelled as in Discord.
REQUIRED_PERMISSIONS: tuple[tuple[Permissions, str], ...] = (
    (Permissions.VIEW_CHANNEL, "Voir le salon"),
    (Permissions.SEND_MESSAGES, "Envoyer des messages"),
    (Permissions.EMBED_LINKS, "Intégrer des liens"),
    (Permissions.ATTACH_FILES, "Joindre des fichiers"),
    (Permissions.READ_MESSAGE_HISTORY, "Voir les anciens messages"),
)
THREAD_SEND = (Permissions.SEND_MESSAGES_IN_THREADS, "Envoyer des messages dans les fils")
MAX_CHANNELS = 50
DEFAULT_RSS_MINUTES = 15
MIN_RSS_MINUTES = 5


def missing_permissions(permissions: Permissions, *, thread: bool) -> list[str]:
    """The labels of the rights the bot lacks among :data:`REQUIRED_PERMISSIONS`."""
    if Permissions.ADMINISTRATOR in permissions:
        return []
    required = list(REQUIRED_PERMISSIONS)
    if thread:
        required = [
            THREAD_SEND if flag == Permissions.SEND_MESSAGES else (flag, label)
            for flag, label in required
        ]
    return [label for flag, label in required if flag not in permissions]


def configured_channels(
    server: dict[str, Any], models: dict[str, Any], channel_ids: Any
) -> dict[str, list[str]]:
    """Channel id → the enabled modules that post there."""
    found: dict[str, list[str]] = {}
    for module, model in models.items():
        settings = server.get(module)
        if not isinstance(settings, dict) or not settings.get("enabled"):
            continue
        for channel_id in channel_ids(model, settings):
            found.setdefault(channel_id, []).append(module)
    return found


async def channel_report(bot: Any, guild_id: str, wanted: dict[str, list[str]]) -> list[dict]:
    """For each configured channel, whether it exists and what the bot lacks there (bot loop)."""
    guild = bot.get_guild(int(guild_id))
    me = getattr(guild, "me", None)
    report = []
    for channel_id, modules in sorted(wanted.items())[:MAX_CHANNELS]:
        channel = await fetch_guild_channel(bot, guild_id, channel_id)
        entry: dict[str, Any] = {"channelId": channel_id, "modules": sorted(modules)}
        if channel is None:
            report.append({**entry, "found": False, "name": None, "missing": []})
            continue
        missing: list[str] = []
        if me is not None and hasattr(channel, "permissions_for"):
            permissions = channel.permissions_for(me)
            missing = missing_permissions(permissions, thread=isinstance(channel, ThreadChannel))
        report.append(
            {**entry, "found": True, "name": getattr(channel, "name", None), "missing": missing}
        )
    return report


def _poll_minutes(settings: dict[str, Any]) -> int:
    try:
        return max(MIN_RSS_MINUTES, int(settings.get("rssPollMinutes", DEFAULT_RSS_MINUTES)))
    except (TypeError, ValueError):
        return DEFAULT_RSS_MINUTES


def rss_report(settings: dict[str, Any], states: dict[str, RssFeedState]) -> list[dict]:
    """Each configured feed's last error, failures in a row and next poll."""
    feeds = settings.get("rssFeeds") if isinstance(settings, dict) else None
    interval = timedelta(minutes=_poll_minutes(settings or {}))
    report = []
    for feed_id, feed in (feeds or {}).items():
        if not isinstance(feed, dict):
            continue
        state = states.get(feed_id)
        due = next_poll_at(state, interval)
        report.append(
            {
                "feedId": feed_id,
                "label": feed.get("label") or feed_id,
                "lastPollAt": _iso(state.last_poll_at if state else None),
                "lastError": state.last_error if state else None,
                "failures": state.failures if state else 0,
                "nextPollAt": _iso(due),
            }
        )
    return sorted(report, key=lambda f: (-f["failures"], f["label"]))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def guild_task_names(bot: Any, enabled_modules: set[str]) -> list[str]:
    """The periodic tasks of the loaded extensions that read one of *enabled_modules*."""
    names: set[str] = set()
    for ext in list(getattr(bot, "ext", {}).values()):
        if not set(declared_modules(ext)) & enabled_modules:
            continue
        # Class attributes only: nothing on the instance (a property) is evaluated.
        for klass in type(ext).__mro__:
            for value in vars(klass).values():
                if isinstance(value, Task):
                    names.add(task_name(value))
    return sorted(names)


def task_report(names: list[str], snapshot: HealthSnapshot) -> list[dict]:
    report = []
    for name in names:
        record = snapshot.tasks.get(name)
        report.append(
            {
                "name": name,
                "successes": record.successes if record else 0,
                "failures": record.failures if record else 0,
                "skipped": record.skipped if record else 0,
                "lastSuccess": record.last_success if record else None,
                "lastFailure": record.last_failure if record else None,
            }
        )
    return report


__all__ = [
    "REQUIRED_PERMISSIONS",
    "channel_report",
    "configured_channels",
    "guild_task_names",
    "missing_permissions",
    "rss_report",
    "task_report",
]
