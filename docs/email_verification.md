# Email verification (production step 14)

Every **new account** must provide an email at signup and click a verification link before they can sign in. **Continue as demo user** is unchanged (seeded demo accounts have no email).

## Flow

1. **POST `/api/auth/signup`** with `username`, `password`, and **`email`** (required).
2. Atlas stores the user with `email_verified_at` unset, emails a link (when SMTP is configured), and returns a message — **no access token** until verification.
3. The link opens the app with `?verify_token=...` in the query string (auto-verify in the UI).
4. **POST `/api/auth/verify-email`** `{ "token": "..." }` — sets `email_verified_at` and deletes the token.
5. **POST `/api/auth/resend-verification`** `{ "username": "..." }` — rate-limited; same generic message whether or not the account needs verification.

**POST `/api/auth/login`** returns **403** with a clear message if the password is correct but the email is not verified yet.

Existing production users who already had an email on file are **grandfathered** as verified **once**, when the `email_verified_at` column is first added (SQLite migration in `init_database`). New signups after that stay unverified until they click the link.

**Deliverability:** Gmail and most major providers work when you send from a verified domain (see below). Some providers (e.g. Yahoo) may delay or filter harder — check spam and the [Resend](https://resend.com/emails) dashboard (`delivered` vs `bounced`).

## Settings

| Variable | Meaning |
| :--- | :--- |
| `APP_PUBLIC_URL` | Base URL for verification links (same as password reset) |
| `EMAIL_VERIFICATION_TTL_SECONDS` | Token lifetime (default 86400) |
| `EMAIL_VERIFICATION_EXPOSE_TOKEN_IN_RESPONSE` | **Local dev only.** When true and SMTP is not configured, signup/resend may return `dev_verify_token` in JSON |
| `SMTP_*` | Same outbound mail as password reset — see `docs/password_reset.md` |

Verification mail will not reach real inboxes until you send from a **verified domain** (`SMTP_FROM`), not `onboarding@resend.dev`.

## Domain + mail (ops)

1. Register a domain and point an **A record** to your server (Rackzar: `102.203.81.249`).
2. Set `SITE_ADDRESS=your.domain.com` and rebuild **web** (Caddy + Let's Encrypt).
3. Set `APP_PUBLIC_URL=https://your.domain.com` and verify the domain in [Resend](https://resend.com/domains).
4. Set `SMTP_FROM=Atlas <noreply@yourdomain.com>` and **`docker compose up -d --force-recreate api`**.

Then test signup → inbox → verify link → login, and forgot-password on the same domain.
