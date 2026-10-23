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
    access_token: str | None = None
    token_type: str = "bearer"
    username: str
    role: UserRole = "user"
    mfa_required: bool = False
    mfa_token: str | None = None


class MeOut(BaseModel):
    username: str
    role: UserRole
    mfa_enabled: bool = False


class AdminUserOut(BaseModel):
    username: str
    email: str | None
    email_verified: bool
    role: UserRole
    disabled: bool
    mfa_enabled: bool = False
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


class MfaSetupRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=32)


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=4000)
    code: str = Field(min_length=6, max_length=32)


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_data_uri: str


class MfaConfirmOut(BaseModel):
    recovery_codes: list[str]


class AuthUser(BaseModel):
    username: str
    role: UserRole = "user"
    mfa_enabled: bool = False
