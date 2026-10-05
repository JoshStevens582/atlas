# Password reset

Atlas supports forgot-password for accounts that have an **email on file** (optional at signup).

## Flow

1. **POST `/api/auth/forgot-password`** `{ "username": "..." }` — rate-limited by client IP. Always returns the same message (no username enumeration).
2. If the user exists and has an email, Atlas stores a **hashed** one-hour token and emails a link when SMTP is configured.
3. The link opens the app with `?reset_token=...` in the query string.
4. **POST `/api/auth/reset-password`** `{ "token": "...", "password": "..." }` — sets a new bcrypt hash and deletes the token.

Demo accounts (alice/bob) have no email and cannot use this flow.

## Settings

| Variable | Meaning |
| :--- | :--- |
| `APP_PUBLIC_URL` | Base URL for reset links (e.g. `https://102-203-81-249.sslip.io`) |
| `PASSWORD_RESET_TTL_SECONDS` | Token lifetime (default 3600) |
| `PASSWORD_RESET_EXPOSE_TOKEN_IN_RESPONSE` | **Local dev only.** When true and SMTP is not configured, `/forgot-password` returns `dev_reset_token` in JSON |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` | Optional outbound mail |

On the server, set `APP_PUBLIC_URL` to your public site and configure SMTP, or reset links will not reach users.
