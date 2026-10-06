# Password reset

Atlas supports forgot-password for accounts that have an **email on file** (optional at signup).

**Production note:** Step 12 **code** is complete when SMTP is configured and links send. **Inbox delivery** for real users (verified domain or non-test `SMTP_FROM`) is **deferred ops** on the production checklist — not a blocker for steps 13–17. See § Inbox delivery below when you pick it up.

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

## Inbox delivery (required for real users)

**`onboarding@resend.dev` is not enough.** Resend may report *delivered* while Gmail and others never show the message. Users will not get reset links in their inbox until you send from an address they trust.

Pick **one** production path:

### A. Verified domain + Resend (recommended for a public portfolio)

1. Register a domain you control (e.g. Cloudflare, Porkbun).
2. [Resend → Domains](https://resend.com/domains) → **Add domain** → add the DNS records Resend shows (SPF, DKIM, etc.) until **Verified**.
3. On the server `/opt/atlas/.env`:

   ```env
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USERNAME=resend
   SMTP_PASSWORD=re_...your_sending_key...
   SMTP_FROM=Atlas <noreply@yourdomain.com>
   SMTP_USE_TLS=true
   APP_PUBLIC_URL=https://102-203-81-249.sslip.io
   ```

4. Reload the API container so it reads `.env`:

   ```bash
   cd /opt/atlas && docker compose up -d --force-recreate api
   ```

5. Forgot-password once → check **your** inbox (and Resend → Emails if debugging).

The `From` domain must **exactly** match the domain you verified in Resend.

### B. Gmail SMTP (small traffic / until you have a domain)

Uses your Google account as the mail pipe. Create a [Google App Password](https://myaccount.google.com/apppasswords) (2FA required). On the server:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your_16_char_app_password
SMTP_FROM=Atlas <you@gmail.com>
SMTP_USE_TLS=true
```

Then `docker compose up -d --force-recreate api`. Mail comes **from your Gmail address**; recipients usually see it in inbox/spam, not invisible like `resend.dev`.

### After any `.env` SMTP change

Always **`--force-recreate api`**. A plain `up -d` can leave the old container running without the new password.

### Local dev without SMTP

Set `PASSWORD_RESET_EXPOSE_TOKEN_IN_RESPONSE=true` in `.env` on your laptop only. Never on the public server.
