"""Cryptographic token generation, formatting, and hashing."""

import hashlib
import secrets
from typing import Tuple


def hash_sha256(val: str) -> str:
    """Compute SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def generate_invite_code() -> str:
    """Generate a high-entropy random invite code."""
    return f"inv_{secrets.token_urlsafe(16)}"


def generate_session_id() -> str:
    """Generate a random 32-byte session identifier."""
    return secrets.token_bytes(32).hex()


def generate_csrf_token() -> str:
    """Generate a random 32-byte CSRF double-submit token."""
    return secrets.token_hex(32)


def generate_api_key() -> Tuple[str, str, str, str]:
    """Generate a Free Point API key in format fp_<prefix>_<secret>.

    Returns:
        (full_key, key_id, prefix, key_hash)
        full_key is returned once and never stored.
        prefix and key_hash (SHA-256) are stored in the database.
    """
    prefix = secrets.token_hex(4)  # 8 hex chars
    secret = secrets.token_urlsafe(32)  # 32+ bytes entropy
    full_key = f"fp_{prefix}_{secret}"
    key_hash = hash_sha256(full_key)
    key_id = f"key-{secrets.token_hex(6)}"
    return full_key, key_id, prefix, key_hash
