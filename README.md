# Telegram Bot Tile Storage

Telegram bot that processes warehouse tile storage files and produces outputs based on templates.

## Requirements

- Docker (for containerized runs)
- A Telegram bot token in `.env` or as `BOT_TOKEN`

## Configuration

Required:
- `BOT_TOKEN`: Telegram bot token.

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
