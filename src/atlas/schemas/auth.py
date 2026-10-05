from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=320)


class ForgotPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=8, max_length=128)


class MessageOut(BaseModel):
    message: str
    dev_reset_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class AuthUser(BaseModel):
    username: str
