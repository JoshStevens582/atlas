# Refresh tokens (production step 17)

Sign-in returns two credentials:

| Credential | Lifetime | Stored | Revocable |
| :--- | :--- | :--- | :--- |
| **Access JWT** | Short (default 15 minutes) | Browser `sessionStorage` | Expires on its own |
| **Refresh token** | Long (default 30 days) | Browser `sessionStorage` + hash in SQLite | Yes — logout or admin disable |

## Flow

1. `POST /api/auth/login` (or demo / MFA complete) → `access_token` + `refresh_token`.
2. API calls send `Authorization: Bearer <access_token>`.
3. When access expires, `POST /api/auth/refresh` with `{ "refresh_token" }` → new pair (rotation).
4. `POST /api/auth/logout` with `{ "refresh_token" }` → row revoked in the database.

Reusing an old refresh token after rotation revokes the whole **family** (stolen-token detection).

Disabling an account revokes all refresh rows for that user.

## Settings

- `ACCESS_TOKEN_TTL_SECONDS` (default `900`)
- `REFRESH_TOKEN_TTL_SECONDS` (default `2592000`)

## API

| Method | Path |
| :--- | :--- |
| `POST` | `/api/auth/refresh` |
| `POST` | `/api/auth/logout` |

Login responses include `refresh_token` when a full session is issued (not during MFA challenge).

## Frontend

The SPA refreshes the access JWT in the background about two minutes before it expires, and retries once on `401` when a refresh credential is still stored. Failed refresh clears the session and returns to the login screen.
