from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from atlas.api.deps import require_admin
from atlas.schemas.auth import AdminUserOut, AuthUser, SetRoleRequest
from atlas.services.admin import (
    AdminError,
    AdminNotFoundError,
    list_admin_users,
    set_user_disabled,
    set_user_role,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserOut])
async def get_users(
    request: Request,
    _: Annotated[AuthUser, Depends(require_admin)],
) -> list[AdminUserOut]:
    return await list_admin_users(request.app.state.session_factory)


@router.post("/users/{username}/role", response_model=AdminUserOut)
async def change_role(
    username: str,
    payload: SetRoleRequest,
    request: Request,
    actor: Annotated[AuthUser, Depends(require_admin)],
) -> AdminUserOut:
    try:
        return await set_user_role(
            request.app.state.session_factory,
            actor_username=actor.username,
            username=username,
            role=payload.role,
        )
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/users/{username}/disable", response_model=AdminUserOut)
async def disable_user(
    username: str,
    request: Request,
    actor: Annotated[AuthUser, Depends(require_admin)],
) -> AdminUserOut:
    try:
        return await set_user_disabled(
            request.app.state.session_factory,
            actor_username=actor.username,
            username=username,
            disabled=True,
        )
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/users/{username}/enable", response_model=AdminUserOut)
async def enable_user(
    username: str,
    request: Request,
    actor: Annotated[AuthUser, Depends(require_admin)],
) -> AdminUserOut:
    try:
        return await set_user_disabled(
            request.app.state.session_factory,
            actor_username=actor.username,
            username=username,
            disabled=False,
        )
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
