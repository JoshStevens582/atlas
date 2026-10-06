# Login lockout (production step 13)

Atlas tracks **failed password attempts per username** in Redis. After too many failures inside a window, that username is **temporarily locked** even if the attacker rotates IP addresses (IP rate limits on `/api/auth/login` still apply separately).

## Behaviour

1. **POST `/api/auth/login`** — before checking the password, Atlas refuses locked usernames with **429** and a `Retry-After` header (seconds).
2. Each wrong password increments a counter keyed by username (stripped). Successful login clears the counter and any lock.
3. When failures reach the threshold, Atlas sets a lock key with a TTL. The attempt that crosses the threshold also returns **429**.
4. **POST `/api/auth/demo`** is unchanged (no password guessing on a named account).
5. After a successful **POST `/api/auth/reset-password`**, lock state for that user is cleared so they can sign in with the new password.

## Defaults

| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `LOGIN_LOCKOUT_ENABLED` | `true` | Turn lockout off on a laptop without Redis. |
| `LOGIN_LOCKOUT_FAIL_CLOSED` | `true` | No Redis while lockout is on → **503** on login (same idea as rate limits). |
| `LOGIN_LOCKOUT_MAX_FAILURES` | `5` | Failed attempts before lock. |
| `LOGIN_LOCKOUT_FAILURE_WINDOW_SECONDS` | `900` | Counter TTL (15 minutes). |
| `LOGIN_LOCKOUT_DURATION_SECONDS` | `900` | How long the lock lasts (15 minutes). |

## Redis keys

- `atlas:login:failures:{username}` — failure counter (expires with the window).
- `atlas:login:locked:{username}` — present while locked.

## 429 vs 401 vs 503

- **401** — wrong username or password (not locked yet).
- **429** — username locked, or IP hit login rate limits (different message).
- **503** — Redis required for lockout (or rate limits) and not available.
