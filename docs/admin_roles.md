# Admin / roles (production step 15)

Every account has a **role**: `user` (default) or `admin`. Threads stay **owner-scoped** even for admins — this is not a way to read someone else's chats.

## What an admin can do

- **People:** list accounts, promote or demote, disable or enable.
- **Library:** add and remove documents. Regular users can still **read** the Library and Ask.
- **Disable:** a disabled account cannot sign in, and a still-valid session token is refused on the next request.

Signup always creates a `user`. Demo accounts stay `user` unless you list them in the bootstrap setting.

## Bootstrap the first admin

Set usernames in `ATLAS_ADMIN_USERNAMES` (comma-separated). On startup Atlas **promotes** those accounts if they already exist. It never demotes, so a later change in the People panel survives a restart.

```
ATLAS_ADMIN_USERNAMES=your-username
```

Then recreate the API so the process picks up the new value (`docker compose up -d --force-recreate api` on the server).

## API

| Method | Path | Who |
| :--- | :--- | :--- |
| `GET` | `/api/auth/me` | signed-in user — `{ username, role }` |
| `GET` | `/api/admin/users` | admin |
| `POST` | `/api/admin/users/{username}/role` | admin — `{ "role": "admin" \| "user" }` |
| `POST` | `/api/admin/users/{username}/disable` | admin |
| `POST` | `/api/admin/users/{username}/enable` | admin |
| `POST` | `/api/documents/upload` | admin |
| `DELETE` | `/api/documents/{id}` | admin |

Guards:

- You cannot change your own role or disable yourself.
- You cannot demote or disable the last admin.

Login and demo login return `role` on the token response. The JWT itself still only carries `sub`; authenticated routes load the user row so disable and role changes apply without waiting seven days.
