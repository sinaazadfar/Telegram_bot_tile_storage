# Telegram Bot Tile Storage

## Admin backups on germany-01

- `/export_backup`: privately send a full unencrypted `.tar.gz` to the requesting admin.
- `/import_backup`: upload a backup, review its contents, select data-only (default) or full configuration restore, then explicitly confirm. `/cancel` cancels. Requests expire after 15 minutes.
- Every day at **03:30 Asia/Tehran**, send a backup to every current administrator. A missed run is caught up on startup after 03:30. Failed recipients retry independently after 5 minutes, increasing to hourly; unfinished archives expire at the next daily cycle.
- Administrators must start the bot before it can deliver private messages. Archives contain the bot token: treat the downloaded files as credentials.
- Backups contain `data/`, `template/`, `.env`, and a versioned checksum manifest. Code, dependencies, logs, runtime state, and previous archives are excluded. Both warehouses and stored admins are restored together. Full restore replaces configuration and can switch the active bot identity.
- Limits: 19 MiB compressed, 200 MiB extracted (including workbook expansion checks), and 10,000 archive files. Oversized backups fail visibly; they are not truncated.
- Delivered archives are removed locally. An archive awaiting failed deliveries remains temporarily in `/var/lib/deldel-tile-backup`. The latest successful restore's pre-change safety archive remains root-only at `/var/lib/deldel-tile-backup-root/safety-backup.tar.gz`.
- Restore validates before changing data, takes a safety backup, restarts the bot, and rolls back if startup does not pass its health check. Result notification retries after startup. Bot conversation state resets on restart.

Deployment: install `requirements.txt`, then run `sh deploy/install_backups.sh` as root and restart `deldel-tile-bot`. This installs a root-owned fixed-path helper and systemd path/service units; no sudo permission is granted to the bot. The environment file becomes a compatibility symlink to `/etc/deldel-tile-bot/config.env` (root-owned, group-readable). Application code and the helper must remain root-owned. Runtime delivery records survive restarts and are excluded from exported backups.

Checks: run `python -m unittest discover -s tests -v`. Inspect `systemctl status deldel-tile-bot deldel-tile-restore.path`, the bot journal, and `/var/lib/deldel-tile-backup/startup-ready.json` for the actual next scheduled backup.

Telegram does not offer idempotent document sends: a machine crash after Telegram accepts an upload but before its delivery record is saved can cause that recipient to receive a duplicate on retry.

Telegram bot that processes warehouse tile storage files and produces outputs based on templates.

## Requirements

- Docker (for containerized runs)
- A Telegram bot token in `.env` or as `BOT_TOKEN`

## Configuration

Required:
- `BOT_TOKEN`: Telegram bot token.
- `BOT_ADMIN_IDS`: comma-separated Telegram user IDs for the initial bot administrators.

Administrators can add another administrator by replying to that user's message with
`/addadmin`, or with `/addadmin USER_ID`. Added administrators are stored persistently
in `data/admins.json`. Other users have read-only access.

Any user can run `/userid` to see their Telegram user ID. When the command is sent as
a reply, it shows the replied-to user's ID.

Optional:
- `BOT_DEFAULT_METRIC`: `physical`, `sellable`, or `reserved` (default: `physical`).
- `BOT_CONNECT_TIMEOUT`: HTTP connect timeout seconds (default: `30`).
- `BOT_READ_TIMEOUT`: HTTP read timeout seconds (default: `60`).
- `BOT_WRITE_TIMEOUT`: HTTP write timeout seconds (default: `60`).
- `BOT_POOL_TIMEOUT`: HTTP pool timeout seconds (default: `30`).
- `BOT_PROCESS_TIMEOUT`: processing timeout seconds (unset by default).
- `BOT_PROXY`: proxy URL (optional).
- `BOT_POOL_SIZE`: request pool size (default: `8`).
- `BOT_UPDATES_POOL_SIZE`: updates pool size (default: `1`).

## Run with persistent data

Use a named volume so `/app/data` survives container rebuilds and restarts.

```sh
docker run -d \
  --name telegram-bot-tile-storage \
  --restart unless-stopped \
  --env-file .env \
  -e BOT_TOKEN="$BOT_TOKEN" \
  -v telegram-bot-tile-storage-data:/app/data \
  telegram-bot-tile-storage:latest
```

## Self-hosted workflow

The GitHub Actions workflow builds the image and runs the container on a self-hosted runner, creating `.env` from repository secrets. Ensure `BOT_TOKEN` is set in repository secrets.
