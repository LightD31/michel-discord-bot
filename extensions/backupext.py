"""
Backup Extension

Automatically backs up all MongoDB databases (global + per-guild) to local
JSON files once a day.  Also exposes a ``/backup`` slash command so an
admin can trigger a backup manually.

Backups are stored under ``data/backups/<timestamp>/`` and the most recent
N are kept (older ones are pruned automatically).

All parameters (directory, retention count, hour) are configurable via the
global config section ``backup`` in the Web UI.
"""

import os
from datetime import datetime

from interactions import (
    Client,
    Embed,
    Extension,
    OrTrigger,
    SlashContext,
    Task,
    TimeTrigger,
    listen,
    slash_command,
    slash_option,
    OptionType,
    Permissions,
)

from src import logutil
from src.helpers import Colors
from src.mongodb import mongo_manager
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

# ── Defaults (overridden by config) ──────────────────────────────────
DEFAULT_BACKUP_DIR = "data/backups"
DEFAULT_MAX_BACKUPS = 7
DEFAULT_BACKUP_HOUR = 4  # 04:00 local time


def _load_backup_config() -> dict:
    """Load the backup section from the global config."""
    try:
        config, _, _ = load_config()
        return config.get("backup", {})
    except Exception:
        return {}


class BackupExtension(Extension):
    """Daily MongoDB backup extension."""

    def __init__(self, bot: Client) -> None:
        self.bot = bot
        self._refresh_config()

    def _refresh_config(self) -> None:
        """(Re-)read settings from the global config file."""
        cfg = _load_backup_config()
        self.backup_enabled: bool = cfg.get("enabled", True)
        self.backup_dir: str = cfg.get("backupDir", DEFAULT_BACKUP_DIR)
        self.max_backups: int = int(cfg.get("maxBackups", DEFAULT_MAX_BACKUPS))
        self.backup_hour: int = int(cfg.get("backupHour", DEFAULT_BACKUP_HOUR))

    @listen()
    async def on_startup(self) -> None:
        """Start the hourly check task once the bot is ready."""
        self.daily_backup.start()
        if self.backup_enabled:
            logger.info(
                "Backup extension loaded – daily backup scheduled at %02d:00",
                self.backup_hour,
            )
        else:
            logger.info("Backup extension loaded – automatic backups DISABLED")

    # Run every hour; the task itself checks if it's the configured hour
    # so the backup hour can be changed at runtime via the config UI.
    @Task.create(OrTrigger(*[TimeTrigger(hour=h, utc=False) for h in range(24)]))
    async def daily_backup(self) -> None:
        """Run the daily automated backup if the current hour matches config."""
        self._refresh_config()

        if not self.backup_enabled:
            return

        if datetime.now().hour != self.backup_hour:
            return

        logger.info("Starting scheduled daily backup…")
        try:
            path = await mongo_manager.backup_all(
                backup_dir=self.backup_dir, max_backups=self.max_backups
            )
            logger.info("Scheduled backup saved to %s", path)
        except Exception as e:
            logger.error("Scheduled backup failed: %s", e, exc_info=True)

    @slash_command(
        name="backup",
        description="Créer une sauvegarde manuelle de la base de données",
        default_member_permissions=Permissions.ADMINISTRATOR,
    )
    @slash_option(
        name="max_backups",
        description="Nombre de sauvegardes à conserver (par défaut : config)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=30,
    )
    async def backup_command(
        self, ctx: SlashContext, max_backups: int | None = None
    ) -> None:
        """Slash command to trigger a backup manually (admin only)."""
        self._refresh_config()
        effective_max = max_backups if max_backups is not None else self.max_backups

        await ctx.defer()
        try:
            start = datetime.now()
            path = await mongo_manager.backup_all(
                backup_dir=self.backup_dir, max_backups=effective_max
            )
            elapsed = (datetime.now() - start).total_seconds()

            embed = Embed(
                title="✅ Sauvegarde terminée",
                description=f"Sauvegarde enregistrée dans `{path}`",
                color=Colors.BACKUP_SUCCESS,
            )
            embed.add_field(name="Durée", value=f"{elapsed:.1f}s", inline=True)
            embed.add_field(
                name="Rétention", value=f"{effective_max} sauvegardes", inline=True
            )
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error("Manual backup failed: %s", e, exc_info=True)
            embed = Embed(
                title="❌ Erreur de sauvegarde",
                description=str(e),
                color=Colors.BACKUP_ERROR,
            )
            await ctx.send(embed=embed)
