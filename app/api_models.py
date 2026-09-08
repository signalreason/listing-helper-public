"""Account API contracts."""

from pydantic import BaseModel, EmailStr, Field


class SetupRequest(BaseModel):
    email: EmailStr
    setup_token: str = Field(min_length=1, max_length=500)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=500)


class CreateUserRequest(BaseModel):
    email: EmailStr


class UserUpdateRequest(BaseModel):
    enabled: bool
