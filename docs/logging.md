# Logging

Atlas logs through the `atlas.*` logger tree. Each line is either JSON (default) or plain text.

## Where logs go

| Sink | Purpose |
| :--- | :--- |
| **stdout** | `docker compose logs api` and local terminals |
| **`data/logs/atlas.jsonl`** | Durable file on the `atlas-data` volume (survives redeploys) |

Ask traces (`ask_retrieve`, `ask_tool`, `ask_complete`) are structured events on that same pipeline. They include IDs and counts, never passwords, tokens, or full questions/answers.

## Settings (environment)

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `LOG_LEVEL` | `INFO` | Python log level for `atlas.*` |
| `LOG_JSON` | `true` | JSON lines when true; human text when false |
| `LOG_FILE_ENABLED` | `true` | Write rotating file under `LOG_FILE_PATH` |
| `LOG_FILE_PATH` | `./data/logs/atlas.jsonl` | Primary durable log |
| `LOG_FILE_MAX_BYTES` | `10485760` | Rotate after 10 MB |
| `LOG_FILE_BACKUP_COUNT` | `5` | Keep five rotated files |

## On the server

```bash
# Follow live API logs
docker compose logs -f api

# Tail the durable file inside the api container
docker compose exec api tail -f /app/data/logs/atlas.jsonl
```

Nightly backup tar includes `data/logs/` because it lives on `atlas-data`.

## Local dev

Set `LOG_JSON=false` in `.env` for readable console output while keeping the same loggers.
