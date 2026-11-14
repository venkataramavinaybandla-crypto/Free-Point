"""API dependency hooks."""

from typing import Optional

from fastapi import Header


async def get_api_key(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[str]:
    """Dependency hook for API key authentication (P3 integration point).

    In P3, this dependency will extract the key prefix, hash the token with SHA-256,
    and validate it against the DynamoDB api_keys table with rate limit tracking.
    For P1 local development, it returns the provided key without blocking.
    """
    key = x_api_key
    if not key and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            key = parts[1]
        else:
            key = authorization
    return key
