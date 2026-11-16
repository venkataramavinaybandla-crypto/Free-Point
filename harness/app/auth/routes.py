"""Web session routes for signup, login, logout, keys, and user secrets."""

import re
import secrets
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..crypto.envelope import EnvelopeEncryptor
from ..mcp_client.github_config import (
    GitHubConfigError,
    load_github_credentials,
    store_github_config,
)
from ..security.ssrf import SSRFError, SSRFValidator
from ..store.factory import (
    get_api_key_store,
    get_secret_store,
    get_session_store,
    get_user_store,
)
from .dependencies import AuthenticatedUser, check_ip_rate_limit, require_session
from .passwords import hash_password, verify_password
from .tokens import (
    generate_api_key,
    generate_csrf_token,
    generate_session_id,
    hash_sha256,
)

auth_router = APIRouter(tags=["Auth & Web Session"])

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="User password")
    invite: Optional[str] = Field(None, description="Optional invite code (ignored)")


class LoginRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class AuthResponse(BaseModel):
    status: str
    user_id: str
    email: str
    csrf_token: str


class KeyCreateResponse(BaseModel):
    id: str
    key: str
    prefix: str
    created_at: int


class KeySummary(BaseModel):
    id: str
    prefix: str
    created_at: int
    last_used: Optional[int] = None


class SecretPutRequest(BaseModel):
    value: str = Field(..., description="Secret plaintext value")


class SecretSummary(BaseModel):
    name: str
    updated_at: int


class GitHubConfigRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096)
    allowlist: List[str] = Field(..., min_length=1, max_length=20)
    base_branch: str = Field("main", min_length=1, max_length=120)


class GitHubConfigResponse(BaseModel):
    configured: bool
    allowlist: List[str] = Field(default_factory=list)
    base_branch: str = "main"


@auth_router.post("/auth/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest, request: Request, response: Response) -> AuthResponse:
    """Register a new user account with email and password."""
    check_ip_rate_limit(request, "signup")

    normalized_email = req.email.strip().lower()
    if not EMAIL_REGEX.match(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not create account.",
        )

    user_store = get_user_store()
    pwd_hash = hash_password(req.password)
    user_id = f"usr-{secrets.token_hex(6)}"
    now_ts = int(time.time())

    try:
        user = await user_store.create_user(
            user_id=user_id,
            email=normalized_email,
            password_hash=pwd_hash,
            created_at=now_ts,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not create account.",
        )

    # Issue session
    raw_session_id = generate_session_id()
    csrf_token = generate_csrf_token()
    session_hash = hash_sha256(raw_session_id)
    expires_at = now_ts + 86400

    session_store = get_session_store()
    await session_store.create_session(
        session_hash=session_hash,
        user_id=user["id"],
        csrf_token=csrf_token,
        expires_at=expires_at,
    )

    response.set_cookie(
        key="session_id",
        value=raw_session_id,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=86400,
    )
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="strict",
        max_age=86400,
    )

    return AuthResponse(
        status="ok",
        user_id=user["id"],
        email=user["email"],
        csrf_token=csrf_token,
    )


@auth_router.post("/auth/login", response_model=AuthResponse)
async def login(req: LoginRequest, request: Request, response: Response) -> AuthResponse:
    """Log in with email and password to receive a session cookie."""
    check_ip_rate_limit(request, "login")

    user_store = get_user_store()
    user = await user_store.get_user_by_email(req.email)
    if not user:
        # Uniform timing resistance
        verify_password("$argon2id$v=19$m=65536,t=3,p=4$dummyhash$dummyhash", req.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    if not verify_password(user["password_hash"], req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    now_ts = int(time.time())
    raw_session_id = generate_session_id()
    csrf_token = generate_csrf_token()
    session_hash = hash_sha256(raw_session_id)
    expires_at = now_ts + 86400

    session_store = get_session_store()
    await session_store.create_session(
        session_hash=session_hash,
        user_id=user["id"],
        csrf_token=csrf_token,
        expires_at=expires_at,
    )

    response.set_cookie(
        key="session_id",
        value=raw_session_id,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=86400,
    )
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="strict",
        max_age=86400,
    )

    return AuthResponse(
        status="ok",
        user_id=user["id"],
        email=user["email"],
        csrf_token=csrf_token,
    )


@auth_router.post("/auth/logout")
async def logout(
    response: Response,
    current_user: AuthenticatedUser = Depends(require_session),
) -> Dict[str, str]:
    """Log out of current session and invalidate cookies."""
    session_store = get_session_store()
    await session_store.delete_session(current_user.session_hash)

    response.delete_cookie(key="session_id", httponly=True, secure=True, samesite="strict")
    response.delete_cookie(key="csrf_token", httponly=False, secure=True, samesite="strict")
    return {"status": "logged_out"}


@auth_router.post("/keys", response_model=KeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    current_user: AuthenticatedUser = Depends(require_session),
) -> KeyCreateResponse:
    """Create a new API key (max 5 active per user). Returns key once."""
    api_key_store = get_api_key_store()
    active_count = await api_key_store.count_active_keys(current_user.user_id)
    if active_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum active API keys limit reached (5). Revoke an existing key first.",
        )

    full_key, key_id, prefix, key_hash = generate_api_key()
    now_ts = int(time.time())
    await api_key_store.create_key(
        key_id=key_id,
        user_id=current_user.user_id,
        prefix=prefix,
        key_hash=key_hash,
        created_at=now_ts,
    )

    return KeyCreateResponse(
        id=key_id,
        key=full_key,
        prefix=prefix,
        created_at=now_ts,
    )


@auth_router.post("/keys/{key_id}/rotate", response_model=KeyCreateResponse)
async def rotate_api_key(
    key_id: str,
    current_user: AuthenticatedUser = Depends(require_session),
) -> KeyCreateResponse:
    """Revoke existing key and issue a new one under the same account."""
    api_key_store = get_api_key_store()
    new_full_key, new_key_id, new_prefix, new_key_hash = generate_api_key()
    now_ts = int(time.time())

    rotated = await api_key_store.rotate_key(
        old_key_id=key_id,
        user_id=current_user.user_id,
        new_key_id=new_key_id,
        new_prefix=new_prefix,
        new_hash=new_key_hash,
        created_at=now_ts,
    )
    if not rotated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key_id}' not found.",
        )

    return KeyCreateResponse(
        id=new_key_id,
        key=new_full_key,
        prefix=new_prefix,
        created_at=now_ts,
    )


@auth_router.post("/keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    current_user: AuthenticatedUser = Depends(require_session),
) -> Dict[str, str]:
    """Revoke an active API key."""
    api_key_store = get_api_key_store()
    revoked = await api_key_store.revoke_key(key_id, current_user.user_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key_id}' not found.",
        )
    return {"id": key_id, "status": "revoked"}


@auth_router.get("/keys", response_model=List[KeySummary])
async def list_api_keys(
    current_user: AuthenticatedUser = Depends(require_session),
) -> List[KeySummary]:
    """List API keys metadata for the caller (never reveals secret or hash)."""
    api_key_store = get_api_key_store()
    keys = await api_key_store.list_keys_for_user(current_user.user_id)
    return [
        KeySummary(
            id=k["key_id"],
            prefix=k["prefix"],
            created_at=k["created_at"],
            last_used=k.get("last_used"),
        )
        for k in keys
    ]


@auth_router.put("/secrets/{name}")
async def put_secret(
    name: str,
    req: SecretPutRequest,
    current_user: AuthenticatedUser = Depends(require_session),
) -> Dict[str, str]:
    """Store user secret encrypted via KMS envelope encryption."""
    if name == "model_url":
        validator = SSRFValidator()
        try:
            validator.validate_url(req.value)
        except SSRFError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSRF validation rejected model URL: {e}",
            )

    encryptor = EnvelopeEncryptor()
    encrypted_payload = encryptor.encrypt(
        plaintext=req.value,
        user_id=current_user.user_id,
        tool=name,
    )

    secret_store = get_secret_store()
    await secret_store.set_secret(
        user_id=current_user.user_id,
        name=name,
        encrypted_value=encrypted_payload,
        updated_at=int(time.time()),
    )
    return {"name": name, "status": "stored"}


@auth_router.put("/github/config", response_model=GitHubConfigResponse)
async def put_github_config(
    req: GitHubConfigRequest,
    current_user: AuthenticatedUser = Depends(require_session),
) -> GitHubConfigResponse:
    """Store the encrypted GitHub token and per-user repository allowlist."""
    try:
        credentials = await store_github_config(
            current_user.user_id,
            req.token,
            req.allowlist,
            req.base_branch,
        )
    except GitHubConfigError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    return GitHubConfigResponse(
        configured=True,
        allowlist=list(credentials.allowlist),
        base_branch=credentials.base_branch,
    )


@auth_router.get("/github/config", response_model=GitHubConfigResponse)
async def get_github_config(
    current_user: AuthenticatedUser = Depends(require_session),
) -> GitHubConfigResponse:
    """Return allowlist metadata only; the token is never returned."""
    try:
        credentials = await load_github_credentials(current_user.user_id)
    except GitHubConfigError:
        return GitHubConfigResponse(configured=False)
    return GitHubConfigResponse(
        configured=True,
        allowlist=list(credentials.allowlist),
        base_branch=credentials.base_branch,
    )


@auth_router.get("/secrets", response_model=List[SecretSummary])
async def list_secrets(
    current_user: AuthenticatedUser = Depends(require_session),
) -> List[SecretSummary]:
    """List user secrets metadata (names and timestamps only, never values)."""
    secret_store = get_secret_store()
    secrets_list = await secret_store.list_secrets(current_user.user_id)
    return [SecretSummary(**s) for s in secrets_list]


@auth_router.delete("/secrets/{name}")
async def delete_secret(
    name: str,
    current_user: AuthenticatedUser = Depends(require_session),
) -> Dict[str, str]:
    """Delete a stored user secret."""
    secret_store = get_secret_store()
    await secret_store.delete_secret(current_user.user_id, name)
    return {"name": name, "status": "deleted"}
