"""SSRF protection module enforcing strict egress constraints."""

import ipaddress
import os
import socket
from typing import Any, Iterable, Optional, Tuple
from urllib.parse import urlparse

import httpcore
import httpx


class SSRFError(Exception):
    """Raised when an outbound URL violates SSRF security boundaries."""


BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS metadata and link-local
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 Unique Local Address
    ipaddress.ip_network("fe80::/10"),  # IPv6 Link-Local
    ipaddress.ip_network("ff00::/8"),  # IPv6 Multicast
]


def is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address falls within any prohibited network range."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True

    for net in BLOCKED_NETWORKS:
        if ip in net:
            return True
    return False


class SSRFValidator:
    """Validates URLs and resolved IPs against SSRF policy."""

    def __init__(
        self,
        allow_insecure_local: Optional[bool] = None,
        max_response_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        if allow_insecure_local is None:
            self.allow_insecure_local = os.getenv("DEV_ALLOW_INSECURE_LOCAL", "false").lower() in (
                "true",
                "1",
                "yes",
            )
        else:
            self.allow_insecure_local = allow_insecure_local
        self.max_response_bytes = max_response_bytes

    def validate_url(self, url: str) -> Tuple[str, str, int]:
        """Validate scheme, parse host/port, enforce HTTPS, and verify resolved host IPs."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            raise SSRFError(f"Invalid URL structure: {url}")

        is_local_host = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if parsed.scheme.lower() != "https":
            if self.allow_insecure_local and is_local_host and parsed.scheme.lower() == "http":
                pass
            else:
                raise SSRFError(f"Insecure scheme '{parsed.scheme}'; only HTTPS URLs are allowed")

        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        self.resolve_and_validate_host(parsed.hostname, port)
        return parsed.hostname, parsed.scheme.lower(), port

    def resolve_and_validate_host(self, host: str, port: int) -> str:
        """Resolve host via getaddrinfo and verify that all resolved IPs are safe."""
        try:
            ip_obj = ipaddress.ip_address(host)
            if self.allow_insecure_local and ip_obj.is_loopback:
                return str(ip_obj)
            if is_ip_blocked(ip_obj):
                raise SSRFError(f"Blocked IP address: {host}")
            return str(ip_obj)
        except ValueError:
            pass

        if self.allow_insecure_local and host.lower() == "localhost":
            return "127.0.0.1"

        try:
            addr_info = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise SSRFError(f"Failed to resolve host '{host}': {e}") from e

        if not addr_info:
            raise SSRFError(f"No address records returned for host '{host}'")

        pinned_ip: Optional[str] = None
        for _family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError:
                raise SSRFError(f"Invalid resolved IP format: {ip_str}")

            if is_ip_blocked(ip_obj):
                raise SSRFError(f"Host '{host}' resolves to prohibited IP range: {ip_str}")

            if pinned_ip is None:
                pinned_ip = ip_str

        return pinned_ip or "127.0.0.1"

    def validate_peer_ip(self, peer_ip: str) -> None:
        """Connect-time verification of socket peer address."""
        try:
            ip_obj = ipaddress.ip_address(peer_ip)
        except ValueError:
            raise SSRFError(f"Invalid peer IP format: {peer_ip}")

        if self.allow_insecure_local and ip_obj.is_loopback:
            return

        if is_ip_blocked(ip_obj):
            raise SSRFError(f"Connected peer IP {peer_ip} is in prohibited network range")


class SSRFNetworkBackend(httpcore.AnyIOBackend):
    """Custom network backend performing DNS pinning and peer-IP connection validation."""

    def __init__(self, validator: SSRFValidator) -> None:
        super().__init__()
        self.validator = validator

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.AsyncNetworkStream:
        pinned_ip = self.validator.resolve_and_validate_host(host, port)
        stream = await super().connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        peer = stream.get_extra_info("peername")
        if peer and len(peer) > 0:
            self.validator.validate_peer_ip(peer[0])
        return stream


class SSRFTransport(httpx.AsyncHTTPTransport):
    """Secure transport pinning target IPs and validating peer connections."""

    def __init__(
        self,
        validator: Optional[SSRFValidator] = None,
        verify: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(verify=verify, **kwargs)
        self.validator = validator or SSRFValidator()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._pool._ssl_context,
            network_backend=SSRFNetworkBackend(self.validator),
        )


def create_safe_client(
    validator: Optional[SSRFValidator] = None,
    timeout: float = 30.0,
) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient pre-configured with SSRF safety constraints."""
    val = validator or SSRFValidator()
    transport = SSRFTransport(validator=val)
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=timeout,
    )
