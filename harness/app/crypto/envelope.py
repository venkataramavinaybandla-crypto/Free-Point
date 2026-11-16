"""Envelope encryption using KMS or local AES-GCM provider."""

import base64
import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KeyProvider(ABC):
    """Abstract interface for KMS envelope key generation and decryption."""

    @abstractmethod
    def generate_data_key(self, encryption_context: Dict[str, str]) -> Tuple[bytes, bytes]:
        """Generate a plaintext data key and its encrypted ciphertext."""

    @abstractmethod
    def decrypt_data_key(self, encrypted_data_key: bytes, encryption_context: Dict[str, str]) -> bytes:
        """Decrypt the data key using the given encryption context."""


class LocalKeyProvider(KeyProvider):
    """Local software key provider for development, testing, and offline modes."""

    def __init__(self, master_key: Optional[bytes] = None) -> None:
        self.master_key = master_key or os.urandom(32)
        self._aesgcm = AESGCM(self.master_key)

    def generate_data_key(self, encryption_context: Dict[str, str]) -> Tuple[bytes, bytes]:
        plaintext_key = os.urandom(32)
        nonce = os.urandom(12)
        aad = json.dumps(encryption_context, sort_keys=True).encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, plaintext_key, aad)
        encrypted_data_key = nonce + ciphertext
        return plaintext_key, encrypted_data_key

    def decrypt_data_key(self, encrypted_data_key: bytes, encryption_context: Dict[str, str]) -> bytes:
        if len(encrypted_data_key) < 28:
            raise ValueError("Encrypted data key payload is too short")
        nonce = encrypted_data_key[:12]
        ciphertext = encrypted_data_key[12:]
        aad = json.dumps(encryption_context, sort_keys=True).encode("utf-8")
        try:
            return self._aesgcm.decrypt(nonce, ciphertext, aad)
        except InvalidTag as err:
            raise ValueError("Decryption failed: encryption context mismatch or corrupted ciphertext") from err


class KMSKeyProvider(KeyProvider):
    """AWS KMS key provider using boto3."""

    def __init__(self, key_id: str, kms_client: Optional[Any] = None, region_name: str = "ap-south-1") -> None:
        self.key_id = key_id
        if kms_client is not None:
            self.client = kms_client
        else:
            import boto3

            self.client = boto3.client("kms", region_name=region_name)

    def generate_data_key(self, encryption_context: Dict[str, str]) -> Tuple[bytes, bytes]:
        try:
            resp = self.client.generate_data_key(
                KeyId=self.key_id,
                KeySpec="AES_256",
                EncryptionContext=encryption_context,
            )
            return resp["Plaintext"], resp["CiphertextBlob"]
        except Exception as err:
            raise ValueError(f"KMS GenerateDataKey failed: {err}") from err

    def decrypt_data_key(self, encrypted_data_key: bytes, encryption_context: Dict[str, str]) -> bytes:
        try:
            resp = self.client.decrypt(
                CiphertextBlob=encrypted_data_key,
                KeyId=self.key_id,
                EncryptionContext=encryption_context,
            )
            return resp["Plaintext"]
        except Exception as err:
            raise ValueError(f"KMS Decrypt failed: {err}") from err


_default_provider: Optional[KeyProvider] = None


def default_key_provider() -> KeyProvider:
    """Process-wide provider so a stored secret can be decrypted later.

    Production uses KMS when ``KMS_KEY_ID`` is set; local runs use one shared
    key for the lifetime of the process.
    """
    global _default_provider
    if _default_provider is None:
        kms_key_id = os.environ.get("KMS_KEY_ID")
        _default_provider = KMSKeyProvider(kms_key_id) if kms_key_id else LocalKeyProvider()
    return _default_provider


class EnvelopeEncryptor:
    """Enforces KMS Envelope Encryption with AES-256-GCM."""

    def __init__(self, key_provider: Optional[KeyProvider] = None) -> None:
        self.provider = key_provider or default_key_provider()

    def encrypt(self, plaintext: str, user_id: str, tool: str) -> str:
        context = {"user_id": user_id, "tool": tool}
        plain_key, enc_key = self.provider.generate_data_key(context)
        aesgcm = AESGCM(plain_key)
        nonce = os.urandom(12)
        aad = json.dumps(context, sort_keys=True).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)

        payload = {
            "enc_key": base64.b64encode(enc_key).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        return json.dumps(payload)

    def decrypt(self, encrypted_payload: str, user_id: str, tool: str) -> str:
        context = {"user_id": user_id, "tool": tool}
        try:
            payload = json.loads(encrypted_payload)
            enc_key = base64.b64decode(payload["enc_key"])
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
        except Exception as err:
            raise ValueError(f"Invalid encrypted payload format: {err}") from err

        plain_key = self.provider.decrypt_data_key(enc_key, context)
        aesgcm = AESGCM(plain_key)
        aad = json.dumps(context, sort_keys=True).encode("utf-8")
        try:
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
            return plaintext_bytes.decode("utf-8")
        except InvalidTag as err:
            raise ValueError("Decryption failed: encryption context mismatch or corrupted ciphertext") from err


_SECRET_PATTERNS = [
    re.compile(r"fp_[A-Za-z0-9_-]{32,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{8,}"),
    re.compile(r'(?i)"?(?:password|token|secret|key)"?\s*[:=]\s*"?[^",\s}]+"?'),
]


def redact_secrets(text: str, custom_secrets: Optional[List[str]] = None) -> str:
    """Redact API keys, tokens, and custom secrets from logs or messages."""
    result = text
    if custom_secrets:
        for secret in custom_secrets:
            if secret and len(secret) >= 4:
                result = result.replace(secret, "[REDACTED]")

    result = re.sub(r"fp_[A-Za-z0-9_-]+", "[REDACTED_API_KEY]", result)
    result = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1[REDACTED_TOKEN]", result)
    return result
