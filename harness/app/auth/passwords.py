"""Password hashing with argon2id and constant-time verification."""

import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# argon2id hasher using argon2-cffi defaults
_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using argon2id."""
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against an argon2id hash in constant time."""
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        return False


def constant_time_compare(val_a: str, val_b: str) -> bool:
    """Compare two strings in constant time."""
    return hmac.compare_digest(val_a, val_b)
