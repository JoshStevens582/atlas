from fastapi import APIRouter, HTTPException, Request, status

from atlas.schemas.auth import LoginRequest, TokenOut
from atlas.services.auth import authenticate_user, issue_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginRequest, request: Request) -> TokenOut:
    settings = request.app.state.settings
    if not settings.atlas_auth_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ATLAS_AUTH_SECRET is not set.",
        )
    username = authenticate_user(settings, payload.username, payload.password)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    return TokenOut(
        access_token=issue_access_token(settings, username),
        username=username,
    )
