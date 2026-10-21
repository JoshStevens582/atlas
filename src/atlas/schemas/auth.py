from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

UserRole = Literal["user", "admin"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str = Field(min_length=3, max_length=320)


class ForgotPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=8, max_length=128)


class MessageOut(BaseModel):
    message: str
    dev_reset_token: str | None = None
    dev_verify_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: UserRole = "user"


class MeOut(BaseModel):
    username: str
    role: UserRole


class AdminUserOut(BaseModel):
    username: str
    email: str | None
    email_verified: bool
    role: UserRole
    disabled: bool
    created_at: datetime | None = None


class SetRoleRequest(BaseModel):
    role: UserRole


class SignupOut(BaseModel):
    username: str
    message: str | None = None
    access_token: str | None = None
    token_type: str = "bearer"
    dev_verify_token: str | None = None


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class ResendVerificationRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class AuthUser(BaseModel):
    username: str
    role: UserRole = "user"
