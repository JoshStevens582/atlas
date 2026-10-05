# Redis

Redis is a separate program. Atlas uses it for three jobs: the upload queue, rate-limit counters, and the Ask answer cache. Chat history stays in SQLite. Search stays in Chroma.

## Required on the server, optional on a laptop

| Where | `REDIS_REQUIRED` | If Redis does not answer |
| :--- | :--- | :--- |
| Server (`docker-compose.yml`) | `true` | The API **refuses to start** |
| Laptop (default) | `false` | The API **starts**. Uploads run in the request. The answer cache is off. Rate-limited routes return **503** |

Compose also waits until Redis is healthy before it starts the API. That is the first gate. `REDIS_REQUIRED` is the gate inside the process, so a bad `REDIS_URL` still stops startup.

## Monitored after startup

`/api/ready` pings Redis and the database. **200** only if both answer. **503** and the body says which one is down. Docker's health check uses `/api/ready`.

`/api/live` does not touch Redis. It only says the process is running.

If Redis dies after a successful start:

| Job | What happens |
| :--- | :--- |
| Rate limits | **503** (fail closed — Atlas will not run uncapped) |
| Upload queue | **503** (the file is not indexed in the request) |
| Answer cache | That Ask is a cache miss and is generated as usual |
| Readiness | `/api/ready` returns **503** |

The worker retries its queue reads instead of exiting.

## Settings

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Where Redis is. Compose points this at the `redis` service. |
| `REDIS_REQUIRED` | `false` | `true` on the server: no startup without a successful ping. |
