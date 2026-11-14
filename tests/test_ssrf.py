"""Tests for SSRF validation, address blocking, and connection pinning."""

from unittest.mock import patch

import pytest

from harness.app.security.ssrf import (
    SSRFError,
    SSRFValidator,
    create_safe_client,
)


def test_ssrf_blocks_private_ipv4_10_x():
    validator = SSRFValidator(allow_insecure_local=False)
    with pytest.raises(SSRFError) as exc:
        validator.validate_url("https://10.0.0.1/v1/chat")
    assert "Blocked IP address" in str(exc.value) or "prohibited IP range" in str(exc.value)


def test_ssrf_blocks_loopback_127_0_0_1():
    validator = SSRFValidator(allow_insecure_local=False)
    with pytest.raises(SSRFError) as exc:
        validator.validate_url("https://127.0.0.1:8000/v1")
    assert "Blocked IP address" in str(exc.value) or "prohibited IP range" in str(exc.value)


def test_ssrf_blocks_aws_metadata_169_254_169_254():
    validator = SSRFValidator(allow_insecure_local=False)
    with pytest.raises(SSRFError) as exc:
        validator.validate_url("https://169.254.169.254/latest/meta-data/")
    assert "Blocked IP address" in str(exc.value) or "prohibited IP range" in str(exc.value)


def test_ssrf_blocks_ipv6_loopback():
    validator = SSRFValidator(allow_insecure_local=False)
    with pytest.raises(SSRFError) as exc:
        validator.validate_url("https://[::1]:8080/v1")
    assert "Blocked IP address" in str(exc.value) or "prohibited IP range" in str(exc.value)


def test_ssrf_enforces_https_only():
    validator = SSRFValidator(allow_insecure_local=False)
    with pytest.raises(SSRFError) as exc:
        validator.validate_url("http://example.com/api")
    assert "only HTTPS URLs are allowed" in str(exc.value)


@pytest.mark.asyncio
async def test_ssrf_redirects_off():
    client = create_safe_client()
    assert client.follow_redirects is False
    await client.aclose()


def test_ssrf_dns_rebinding_defense():
    validator = SSRFValidator(allow_insecure_local=False)

    with patch("socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        pinned_ip = validator.resolve_and_validate_host("safe.example.com", 443)
        assert pinned_ip == "93.184.216.34"

    with pytest.raises(SSRFError) as exc1:
        validator.validate_peer_ip("127.0.0.1")
    assert "prohibited network range" in str(exc1.value)

    with pytest.raises(SSRFError) as exc2:
        validator.validate_peer_ip("169.254.169.254")
    assert "prohibited network range" in str(exc2.value)
