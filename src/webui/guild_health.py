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

from interactions import IntervalTrigger, OrTrigger, Permissions, Task, ThreadChannel, TimeTrigger

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


# What each periodic task does, as the "Santé" card shows it to a guild's admins
# (the name, ``Class.method``, means nothing to them). Keyed by
# ``task_name()``; ``tests/test_webui_guild_health.py`` fails on a task without one.
TASK_LABELS: dict[str, str] = {
    "BackupExtension.daily_backup": "Sauvegarde de la base",
    "BirthdayExtension.anniversaire_check": "Vœux d'anniversaire",
    "VotingMixin.close_due_comparisons": "Clôture des votes de /ask",
    "StatsMixin.confrerie": "Statistiques de la Confrérie",
    "UpdatesMixin.autoupdate": "Mise à jour des fiches Notion",
    "DataPurgeExtension.purge_left_guilds": "Purge des serveurs quittés",
    "GiveawayExtension.check_giveaways": "Tirage des giveaways",
    "WelcomeExtension.close_greetings": "Fin des 👋 de bienvenue",
    "SyncMixin.sync_roles": "Synchronisation des groupes LLDAP",
    "NotificationsMixin.schedule": "Programme des matchs",
    "NotificationsMixin.live_update": "Matchs en direct",
    "MetricsExtension.refresh_tick": "Métriques",
    "StatusMixin.status": "Statut du serveur Minecraft",
    "StatsMixin.stats": "Statistiques Minecraft",
    "MyDataExtension.forget_departed_members": "Purge des données des membres partis",
    "TasksMixin.check_medals": "Médailles olympiques",
    "PollsExtension.check_closing_polls": "Clôture des sondages",
    "RemindersExtension.check_reminders": "Envoi des rappels",
    "RssExtension.check_feeds": "Relevé des flux RSS",
    "Satisfactory.update_message": "Statut du serveur Satisfactory",
    "ExchangeMixin.parcel_reminders": "Rappels des colis du Père Noël Secret",
    "SpeedonsExtension.get_speedons_schedule": "Planning Speedons",
    "PlaylistMixin.check_playlist_changes": "Suivi de la playlist",
    "PlaylistMixin.new_titles_playlist": "Playlist des nouveautés",
    "VotesMixin.randomvote": "Vote du jour",
    "VotesMixin.reminder_check": "Rappels de vote",
    "VotesMixin.check_for_end": "Clôture des votes Spotify",
    "StreamlabsCharityExtension.streamlabscharity": "Suivi de la cagnotte",
    "RecurringMixin.recurring_tick": "Dépenses récurrentes",
    "EventSubMixin.eventsub_watchdog": "Connexion aux alertes Twitch",
    "ScheduleMixin.update": "Planning des streamers",
    "EmotesMixin.check_new_emotes": "Nouvelles emotes",
    "TasksMixin.poll_kuma": "Statut Uptime Kuma",
    "TasksMixin.push_status": "Signal de vie vers Uptime Kuma",
    "XpExtension._archive_old_xp_events": "Archivage de l'historique d'XP",
    "XpExtension._cache_cleanup_task": "Nettoyage du cache d'XP",
    "VoiceMixin._voice_xp_tick": "XP vocale",
    "LeaderboardMixin.leaderboardpermanent": "Classement XP",
    "SeasonsMixin._close_seasons": "Fin de saison XP (podium, rôle du champion)",
    "GazetteExtension.publish_due": "Publication de la Gazette",
    "YoutubeExtension.check_youtube": "Nouvelles vidéos YouTube",
    "TasksMixin.zevent": "Suivi du Zevent",
    "RemindersMixin.daily_journa_check": "Rappel /journa",
    "RemindersMixin.reminder_checker": "Rappels ZUnivers",
    "EventsMixin.events_checker": "Événements ZUnivers",
    "CorporationMixin.corporation_recap": "Récap de la corporation",
    "Zerator247Mixin.zerator247_checker": "ZeratoR 24/7",
}


def _every(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "toutes les heures" if hours == 1 else f"toutes les {hours} h"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return "toutes les minutes" if minutes == 1 else f"toutes les {minutes} min"
    return f"toutes les {seconds} s"


def task_schedule(task: Any) -> str:
    """When *task* runs, in words ("toutes les 5 min", "chaque jour à 03:00"), or ``""``."""
    trigger = getattr(task, "trigger", None)
    if isinstance(trigger, IntervalTrigger):
        return _every(int(trigger.delta.total_seconds()))
    times = []
    for part in trigger.triggers if isinstance(trigger, OrTrigger) else [trigger]:
        if not isinstance(part, TimeTrigger):
            return ""
        hour, minute, _ = part.target_time
        times.append(f"{hour:02d}:{minute:02d}{' UTC' if part.tz is not None else ''}")
    if not times:
        return ""
    if len(times) >= 24:
        return "toutes les heures"
    if len(times) > 4:
        return f"{len(times)} fois par jour"
    return "chaque jour à " + ", ".join(sorted(times))


def guild_tasks(bot: Any, enabled_modules: set[str]) -> dict[str, Any]:
    """The periodic tasks of the loaded extensions that read one of *enabled_modules*."""
    tasks: dict[str, Any] = {}
    for ext in list(getattr(bot, "ext", {}).values()):
        if not set(declared_modules(ext)) & enabled_modules:
            continue
        # Class attributes only: nothing on the instance (a property) is evaluated.
        for klass in type(ext).__mro__:
            for value in vars(klass).values():
                if isinstance(value, Task):
                    tasks.setdefault(task_name(value), value)
    return dict(sorted(tasks.items()))


def guild_task_names(bot: Any, enabled_modules: set[str]) -> list[str]:
    return list(guild_tasks(bot, enabled_modules))


def task_report(tasks: dict[str, Any] | list[str], snapshot: HealthSnapshot) -> list[dict]:
    """Each task's runs, with what it does and when it runs, for the "Santé" card."""
    items = tasks.items() if isinstance(tasks, dict) else ((name, None) for name in tasks)
    report = []
    for name, task in items:
        record = snapshot.tasks.get(name)
        report.append(
            {
                "name": name,
                "label": TASK_LABELS.get(name, name),
                "schedule": task_schedule(task) if task is not None else "",
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
    "TASK_LABELS",
    "guild_task_names",
    "guild_tasks",
    "missing_permissions",
    "rss_report",
    "task_schedule",
    "task_report",
]
