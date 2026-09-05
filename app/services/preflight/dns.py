from __future__ import annotations

import ipaddress
import time

import dns.asyncresolver
import dns.exception

from app.services.preflight.models import DNSCheckResult
from app.services.security.url_security import URLSecurityError, URLSecurityService


async def check_dns(hostname: str, security: URLSecurityService) -> DNSCheckResult:
    """Resolve A/AAAA records for `hostname` and validate every address
    against the SSRF policy. Any private/loopback/reserved address fails
    the check, even if other addresses for the same host are public.

    If `hostname` is itself a literal IP address (e.g. the user submitted
    "http://127.0.0.1/"), it is validated directly -- querying a DNS
    resolver with a literal IP would not exercise the policy at all.
    """
    try:
        literal_ip = str(ipaddress.ip_address(hostname))
        try:
            security.check_ip_allowed(literal_ip)
        except URLSecurityError as exc:
            return DNSCheckResult(resolves=True, resolution_time_ms=0.0, error=str(exc))
        is_v6 = ipaddress.ip_address(literal_ip).version == 6
        return DNSCheckResult(
            resolves=True,
            resolution_time_ms=0.0,
            ipv4_addresses=[] if is_v6 else [literal_ip],
            ipv6_addresses=[literal_ip] if is_v6 else [],
        )
    except ValueError:
        pass

    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 5
    resolver.lifetime = 5

    start = time.monotonic()
    ipv4: list[str] = []
    ipv6: list[str] = []
    error: str | None = None

    try:
        answer = await resolver.resolve(hostname, "A")
        ipv4 = [r.address for r in answer]
    except dns.exception.DNSException as exc:
        error = f"A record lookup failed: {exc}"

    try:
        answer6 = await resolver.resolve(hostname, "AAAA")
        ipv6 = [r.address for r in answer6]
    except dns.exception.DNSException:
        pass  # AAAA is optional; absence is not an error

    duration_ms = (time.monotonic() - start) * 1000

    if not ipv4 and not ipv6:
        return DNSCheckResult(
            resolves=False,
            resolution_time_ms=duration_ms,
            error=error or "no A or AAAA records found",
        )

    try:
        for ip in [*ipv4, *ipv6]:
            security.check_ip_allowed(ip)
    except URLSecurityError as exc:
        return DNSCheckResult(
            resolves=True,
            ipv4_addresses=ipv4,
            ipv6_addresses=ipv6,
            resolution_time_ms=duration_ms,
            error=str(exc),
        )

    return DNSCheckResult(
        resolves=True,
        ipv4_addresses=ipv4,
        ipv6_addresses=ipv6,
        resolution_time_ms=duration_ms,
    )
