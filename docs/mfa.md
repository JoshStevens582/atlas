# Authenticator MFA (production step 16)

Password stays the first check. An authenticator app (Google Authenticator, 1Password, Authy, or similar) is the second. **Off until that person turns it on.** Existing accounts, including the live admin, keep signing in with a password until they enroll. **Continue as demo user** stays one click unless someone turns MFA on for that demo account.

## Flow

1. Sign in, open **Security**, enter the password, and choose **Set up authenticator**.
2. Scan the QR code or type the secret into the app.
3. Enter the 6-digit code. Atlas turns MFA on and shows **8 recovery codes once**. **Copy or download** them before continuing; Atlas does not store the plain codes for you. Each code works a single time.
4. The next sign-in: correct password returns no session. The screen asks for a code (or a recovery code). Only then is the 7-day token issued.
5. **Turn off** from Security with the password plus a current code or an unused recovery code.
6. **Generate new recovery codes** from Security with the password plus a **6-digit app code** (not a recovery code). Old unused backup codes stop working.
7. An admin can **Clear authenticator** on someone else's row in People (lost phone and lost codes). An admin cannot clear their own from People; they use Security or a server break-glass.

A setup that was started but not confirmed does **not** block sign-in.

The short sign-in step expires after 5 minutes (`MFA_CHALLENGE_TTL_SECONDS`). It is not a session: it cannot call Ask, the Library, or People. Failed codes count toward the same username lockout as a wrong password. The same 6-digit code cannot be used twice.

## What is stored

| Stored | How |
| :--- | :--- |
| Authenticator secret | Encrypted with the Atlas API key (`ATLAS_AUTH_SECRET`) before it goes into SQLite |
| Recovery codes | SHA-256 hash only. The plain codes are shown once |
| Last accepted code step | So a code from the current 30-second window cannot be replayed |

Rotating `ATLAS_AUTH_SECRET` makes stored authenticator secrets unreadable, the same way it invalidates sessions. Turn MFA off before rotating that key. If you already rotated it, an admin who can still sign in can clear MFA for other people. Clearing your own after a rotation needs the database: set `mfa_secret`, `mfa_enabled_at`, and `mfa_last_step` to null on your user row and delete that user's rows in `mfa_recovery_codes`.

## API

| Method | Path | Who |
| :--- | :--- | :--- |
| `POST` | `/api/auth/mfa/setup` | signed in — `{ "password": "..." }` → secret, otpauth URI, QR data URI |
| `POST` | `/api/auth/mfa/confirm` | signed in — `{ "code": "123456" }` → `{ "recovery_codes": [...] }` |
| `POST` | `/api/auth/mfa/disable` | signed in — password + code |
| `POST` | `/api/auth/mfa/recovery-codes/regenerate` | signed in — password + **app code only** → new recovery codes |
| `POST` | `/api/auth/login` | password only. When MFA is on, `mfa_required` is true and `access_token` is null |
| `POST` | `/api/auth/login/mfa` | `{ "mfa_token", "code" }` → session |
| `POST` | `/api/admin/users/{username}/mfa/reset` | admin, other people only |

`GET /api/auth/me` includes `mfa_enabled`.
