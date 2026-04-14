"""Authentication routes: register, login, current user (Bearer JWT)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import get_current_user, get_db, get_login_auth_provider
from sentinel_prism.db.models import User
from sentinel_prism.services.auth import create_access_token, create_user
from sentinel_prism.services.auth.providers.protocol import AuthProvider

router = APIRouter(prefix="/auth", tags=["auth"])

_PASSWORD_MIN_LEN = 12


class RegisterRequest(BaseModel):
    """Register with email and password. Password rules: see README / OpenAPI."""

    email: EmailStr
    password: str = Field(
        ...,
        min_length=_PASSWORD_MIN_LEN,
        max_length=128,
        description=(
            f"Minimum {_PASSWORD_MIN_LEN} characters, maximum 128 characters; "
            "at least one lowercase letter, one uppercase letter, and one digit."
        ),
    )

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < _PASSWORD_MIN_LEN:
            raise ValueError(
                f"Password must be at least {_PASSWORD_MIN_LEN} characters"
            )
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v


class RegisterResponse(BaseModel):
    id: str
    email: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class MeResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a local user account",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    email = body.email.lower()
    try:
        user = await create_user(db, email, body.password)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        orig = getattr(exc, "orig", None)
        pgcode = getattr(orig, "pgcode", None)
        if pgcode == "23505":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            )
        raise
    return RegisterResponse(id=str(user.id), email=user.email)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Obtain a JWT for API calls",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
    auth_provider: AuthProvider = Depends(get_login_auth_provider),
) -> LoginResponse:
    user = await auth_provider.verify_email_password(
        db, body.email.lower(), body.password
    )
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.id)
    return LoginResponse(access_token=token, user_id=str(user.id))


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user (requires Bearer token)",
)
async def me(current: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        id=str(current.id),
        email=current.email,
        role=current.role.value,
        is_active=current.is_active,
    )
