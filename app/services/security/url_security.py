"""SSRF protection: URL scheme/host validation and DNS-pinned HTTP transport.

Every outbound fetch the crawler makes (preflight or crawl) must go through
this module. It rejects disallowed schemes, blocks requests to private /
loopback / link-local / reserved / multicast IP ranges, and pins each HTTP
connection to the specific IP address it validated -- closing the classic
DNS-rebinding gap where a hostname resolves to a safe IP at validation time
and a private IP at connect time.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import httpx

from app.core.config import Settings


class URLSecurityError(Exception):
    """Raised when a URL fails SSRF/security validation."""


@dataclass(frozen=True)
class ValidatedHost:
    hostname: str
    ip: str


class URLSecurityService:
    """Validates URLs and resolved IPs against the collection safety policy.

    ponytail: DNS resolution here is a single A/AAAA lookup per request with
    no caching -- fine at preflight/crawl volumes. Add a short-TTL resolver
    cache if this becomes a bottleneck under high concurrency.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.timeout = 5
        self._resolver.lifetime = 5

    def validate_scheme(self, url: str) -> str:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        if scheme not in self._settings.allowed_schemes_set:
            raise URLSecurityError(f"scheme '{scheme or '(none)'}' is not allowed")
        if not parts.hostname:
            raise URLSecurityError("URL has no hostname")
        return parts.hostname

    def check_ip_allowed(self, ip_str: str) -> None:
        if not self._settings.block_private_networks:
            return
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise URLSecurityError(f"resolved IP {ip_str} is in a blocked range")

    async def resolve_and_validate(self, hostname: str) -> str:
        """Resolve hostname to an IP, validate it, and return the IP string.

        If `hostname` is already a literal IP address, it is validated
        directly without a DNS lookup.
        """
        try:
            literal = ipaddress.ip_address(hostname)
            self.check_ip_allowed(str(literal))
            return str(literal)
        except ValueError:
            pass

        try:
            answer = await self._resolver.resolve(hostname, "A")
        except dns.exception.DNSException:
            try:
                answer = await self._resolver.resolve(hostname, "AAAA")
            except dns.exception.DNSException as exc:
                raise URLSecurityError(f"DNS resolution failed for {hostname}: {exc}") from exc

        resolved_ips = [rdata.address for rdata in answer]
        if not resolved_ips:
            raise URLSecurityError(f"DNS resolution for {hostname} returned no addresses")

        for ip_str in resolved_ips:
            self.check_ip_allowed(ip_str)

        return resolved_ips[0]

    async def validate_url(self, url: str) -> ValidatedHost:
        hostname = self.validate_scheme(url)
        ip = await self.resolve_and_validate(hostname)
        return ValidatedHost(hostname=hostname, ip=ip)

    def build_client(self, **client_kwargs) -> httpx.AsyncClient:
        """Build an httpx.AsyncClient whose transport re-validates and pins
        the IP for every request AND every redirect hop."""
        transport = _SSRFSafeTransport(self, verify=True)
        return httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,  # caller must walk redirects explicitly and re-validate each hop
            headers={"User-Agent": self._settings.crawler_user_agent},
            **client_kwargs,
        )


class _SSRFSafeTransport(httpx.AsyncHTTPTransport):
    def __init__(self, security: URLSecurityService, **kwargs):
        super().__init__(**kwargs)
        self._security = security

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        hostname = self._security.validate_scheme(str(request.url))
        ip = await self._security.resolve_and_validate(hostname)

        original_host = request.url.host
        pinned_url = request.url.copy_with(host=ip)
        request.url = pinned_url
        request.headers["Host"] = original_host
        request.extensions["sni_hostname"] = original_host

        response = await super().handle_async_request(request)

        # Restore the original hostname on the request so callers that read
        # response.url / response.request.url see the real host, not the
        # pinned IP we substituted purely for the connection itself.
        request.url = request.url.copy_with(host=original_host)
        return response


def is_safe_ip_socket_family(hostname: str) -> int:  # pragma: no cover - trivial helper
    """Best-effort address family hint for callers that need socket.AF_*."""
    try:
        ipaddress.IPv6Address(hostname)
        return socket.AF_INET6
    except ValueError:
        return socket.AF_INET
