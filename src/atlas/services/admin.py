"""Admin/roles (production step 15).

Ordinary accounts stay ``user``. An ``admin`` can list accounts, change
roles, and disable spenders. Threads stay owner-scoped even for admins —
this is not a "read everyone's chats" switch.

``ATLAS_ADMIN_USERNAMES`` only *promotes* existing usernames on startup.
It never demotes, so a later UI change is not undone by a restart.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.db.models import User
from atlas.repositories.user_repo import (
    ROLE_ADMIN,
    ROLE_USER,
    VALID_ROLES,
    UserRepository,
)
from atlas.schemas.auth import AdminUserOut, UserRole

logger = logging.getLogger("atlas.admin")


class AdminError(Exception):
    pass


class AdminNotFoundError(AdminError):
    pass


def parse_admin_usernames(settings: Settings) -> list[str]:
    return [
        piece.strip()
        for piece in settings.atlas_admin_usernames.split(",")
        if piece.strip()
    ]


def user_is_admin(user: User) -> bool:
    return user.role == ROLE_ADMIN


def user_is_disabled(user: User) -> bool:
    return user.disabled_at is not None


def admin_user_out(user: User) -> AdminUserOut:
    role: UserRole = "admin" if user.role == ROLE_ADMIN else "user"
    return AdminUserOut(
        username=user.username,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        role=role,
        disabled=user.disabled_at is not None,
        created_at=user.created_at,
    )


async def apply_admin_usernames(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> int:
    """Promote listed usernames to admin. Missing names are skipped."""
    promoted = 0
    for username in parse_admin_usernames(settings):
        async with session_factory() as session:
            repo = UserRepository(session)
            user = await repo.get_by_username(username)
            if user is None or user.role == ROLE_ADMIN:
                continue
            await repo.set_role(user.id, ROLE_ADMIN)
            promoted += 1
    if promoted:
        logger.info("promoted %s account(s) to admin from ATLAS_ADMIN_USERNAMES", promoted)
    return promoted


async def list_admin_users(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[AdminUserOut]:
    async with session_factory() as session:
        users = await UserRepository(session).list_users()
    return [admin_user_out(user) for user in users]


async def set_user_role(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    actor_username: str,
    username: str,
    role: str,
) -> AdminUserOut:
    if role not in VALID_ROLES:
        raise AdminError("Role must be user or admin.")
    target = username.strip()
    if not target:
        raise AdminNotFoundError("User not found.")
    if actor_username == target:
        raise AdminError("You cannot change your own role.")

    async with session_factory() as session:
        repo = UserRepository(session)
        user = await repo.get_by_username(target)
        if user is None:
            raise AdminNotFoundError("User not found.")
        if user.role == ROLE_ADMIN and role == ROLE_USER:
            if await repo.count_admins() <= 1:
                raise AdminError("Cannot demote the last admin.")
        updated = await repo.set_role(user.id, role)
    if updated is None:
        raise AdminNotFoundError("User not found.")
    logger.info("admin set role actor=%s target=%s role=%s", actor_username, target, role)
    return admin_user_out(updated)


async def set_user_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    actor_username: str,
    username: str,
    disabled: bool,
) -> AdminUserOut:
    target = username.strip()
    if not target:
        raise AdminNotFoundError("User not found.")
    if disabled and actor_username == target:
        raise AdminError("You cannot disable your own account.")

    async with session_factory() as session:
        repo = UserRepository(session)
        user = await repo.get_by_username(target)
        if user is None:
            raise AdminNotFoundError("User not found.")
        if disabled and user.role == ROLE_ADMIN and await repo.count_admins() <= 1:
            raise AdminError("Cannot disable the last admin.")
        stamp = datetime.now(UTC) if disabled else None
        updated = await repo.set_disabled_at(user.id, stamp)
    if updated is None:
        raise AdminNotFoundError("User not found.")
    logger.info(
        "admin set disabled actor=%s target=%s disabled=%s",
        actor_username,
        target,
        disabled,
    )
    return admin_user_out(updated)
