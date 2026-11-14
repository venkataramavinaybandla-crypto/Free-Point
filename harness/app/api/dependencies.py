"""API dependency hooks re-exported from auth dependencies."""

from ..auth.dependencies import (
    AuthenticatedKey,
    AuthenticatedUser,
    get_api_key,
    require_api_key,
    require_session,
)

__all__ = [
    "AuthenticatedKey",
    "AuthenticatedUser",
    "get_api_key",
    "require_api_key",
    "require_session",
]
