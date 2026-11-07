# Runbook

Operating the bot in production: backups, restoring them, incidents and secret
rotation. Commands assume the Docker Compose layout of the repository
(`docker-compose.yml`, service and container `discord_bot`, run from the
directory that holds `config/`, `data/` and `logs/`).

## Backups

**What is backed up.** Every database the bot owns (`global` and each
`guild_<id>`), except the dashboard sessions (`global.webui_sessions`), plus a
copy of `config/config.json`. The configuration copy holds every secret the bot
uses: treat a backup folder as sensitive.

**Where and when.** The daily task writes `data/backups/<YYYY-mm-dd_HH-MM-SS>/`
at the hour set in the dashboard (global config → *Sauvegarde BDD*, 04:00 by
default) and keeps the last `maxBackups` folders (7 by default). `/backup` on
the dev server runs one on demand. It can keep more folders for that run, never
fewer.

**Format.** Each collection is `<database>/<collection>.jsonl.gz`: gzip-compressed
JSON Lines in MongoDB Canonical Extended JSON, one document per line, with every
BSON type kept (ObjectId, dates, 64-bit integers…). `manifest.json` records the
format version and the number of documents in each collection.

**Off-site copy and encryption.** The bot only writes local backups. `./data` is
a host directory, so copy the backups off the machine from the host, encrypted.
For example, with [restic](https://restic.net/), run from the host's crontab an
hour after the backup:

```bash
# /etc/cron.d/michel-backup: every day at 05:00
0 5 * * * debian RESTIC_REPOSITORY=sftp:backup@storage:/michel RESTIC_PASSWORD_FILE=/home/debian/.restic-pass restic backup /home/debian/michel-discord-bot/data/backups --quiet
```

`rclone copy` to an encrypted (`crypt`) remote works the same way. Keep the
restic password somewhere other than this server.

## Restoring a backup

1. Stop the bot so nothing writes during the restore:
   ```bash
   docker compose stop discord_bot
   ```
2. See what the backup holds:
   ```bash
   docker compose run --rm discord_bot python scripts/restore_backup.py data/backups/<folder> --dry-run
   ```
3. Restore. Without `--drop`, the script refuses to touch a collection that
   already holds documents, and in that case writes nothing at all. `--db`
   (repeatable) limits the restore to some databases, e.g. a single guild:
   ```bash
   docker compose run --rm discord_bot python scripts/restore_backup.py data/backups/<folder> --drop
   docker compose run --rm discord_bot python scripts/restore_backup.py data/backups/<folder> --drop --db guild_123456789
   ```
   The script checks that each collection received as many documents as the
   manifest lists.
4. The configuration is not restored automatically. If it is needed too, copy
   `data/backups/<folder>/config.json` over `config/config.json`.
5. Start the bot again and check its logs:
   ```bash
   docker compose start discord_bot
   docker compose logs -f --tail 100 discord_bot
   ```

**Test a restore regularly** (once a month is plenty): restore the latest backup
into a scratch MongoDB with `--mongo-url mongodb://…/` and look at a few
collections. A backup that has never been restored is not a backup.

## Incidents

**The bot is offline or not answering.**
- `docker compose ps` shows the container state and health.
- The bot touches a heartbeat file every 45 s. If its event loop hangs, the
  watchdog exits the process and Docker restarts it (`restart: unless-stopped`).
  Repeated restarts show up in `docker compose ps` and the logs.
- `docker compose logs --tail 200 discord_bot` usually names the cause: an
  invalid token, MongoDB unreachable, or an extension failing to load.
- On a recent version, `docker compose stop` sends SIGTERM and the bot shuts
  down cleanly; `docker compose restart discord_bot` is safe.

**MongoDB is down.** Commands that read or write data fail with an error
message, and periodic tasks log errors and retry on their next run. The bot
itself keeps running. Once MongoDB is back, nothing needs restarting.

**The dashboard refuses a save (422).** The submitted configuration doesn't match
the module's schema; the error lists the fields. To check the whole file:
```bash
docker compose exec discord_bot python scripts/check_config.py
```
Unknown keys are removed from the dashboard with *Nettoyer la configuration*.

**Someone must lose dashboard access now.** Permissions are re-checked against
Discord every minute, so removing their role is enough. To log every session
out at once, empty `global.webui_sessions` in MongoDB and restart the bot.

## Rotating a secret

Secrets live in `config/config.json` and are masked in the dashboard. A rotated
secret is pasted into its field in the dashboard (global config), or edited in
the file, then the bot is restarted so every extension picks it up.

| Secret | Where to regenerate it | Config location |
|---|---|---|
| Discord bot token | Discord Developer Portal → Bot → Reset Token | `discord.botToken` |
| Dashboard OAuth secret | Developer Portal → OAuth2 → Reset Secret | `webui.clientSecret` |
| MongoDB credentials | Your MongoDB server | `mongodb.url` |
| Spotify, Twitch, YouTube, Notion | Each provider's developer console | `spotify`, `twitch`, `youtube`, `notion` sections |
| AI providers (OpenRouter, NanoGPT) | Provider dashboard | `OpenRouter.openrouterApiKey`, `NanoGPT.apiKey` |
| Shlink, Uptime Kuma, Random.org | Each service's settings | their sections |

After a suspected leak, rotate the Discord bot token first: it gives full control
of the bot. Then check the backups, which also contain the secrets (see
*Backups*), and rotate whatever they exposed.
