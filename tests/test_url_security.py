from __future__ import annotations

import pytest

from app.services.security.url_security import URLSecurityError, URLSecurityService


class _FakeAnswer:
    def __init__(self, address: str):
        self.address = address


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint (link-local)
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://0.0.0.0/",
    ],
)
async def test_literal_private_ips_rejected(security: URLSecurityService, url: str):
    hostname = security.validate_scheme(url)
    with pytest.raises(URLSecurityError):
        await security.resolve_and_validate(hostname)


async def test_localhost_hostname_resolving_to_loopback_is_blocked(
    security: URLSecurityService, monkeypatch: pytest.MonkeyPatch
):
    import dns.asyncresolver

    async def fake_resolve(self, hostname, rdtype):
        if rdtype == "A":
            return [_FakeAnswer("127.0.0.1")]
        import dns.exception

        raise dns.exception.DNSException("no AAAA")

    monkeypatch.setattr(dns.asyncresolver.Resolver, "resolve", fake_resolve)

    with pytest.raises(URLSecurityError):
        await security.resolve_and_validate("localhost")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "javascript:alert(1)",
        "",
        "not-a-url",
    ],
)
def test_unsupported_schemes_rejected(security: URLSecurityService, url: str):
    with pytest.raises(URLSecurityError):
        security.validate_scheme(url)


def test_allowed_schemes_pass(security: URLSecurityService):
    assert security.validate_scheme("http://example.com/") == "example.com"
    assert security.validate_scheme("https://example.com/path") == "example.com"


@pytest.mark.parametrize(
    "url",
    [
        "https://https://example.com/",
        "http://https://example.com/path",
        "https://http://example.com/",
    ],
)
def test_nested_schemes_rejected_as_invalid(security: URLSecurityService, url: str):
    with pytest.raises(URLSecurityError) as excinfo:
        security.validate_scheme(url)
    assert excinfo.value.code == "URL_INVALID"


async def test_dns_rebinding_style_resolution_to_private_ip_is_blocked(
    security: URLSecurityService, monkeypatch: pytest.MonkeyPatch
):
    """A hostname that resolves to a private IP must be blocked regardless
    of how innocent it looks -- this is the core DNS-rebinding defense."""
    import dns.asyncresolver

    async def fake_resolve(self, hostname, rdtype):
        if rdtype == "A":
            return [_FakeAnswer("10.1.2.3")]
        import dns.exception

        raise dns.exception.DNSException("no AAAA")

    monkeypatch.setattr(dns.asyncresolver.Resolver, "resolve", fake_resolve)

    with pytest.raises(URLSecurityError):
        await security.resolve_and_validate("attacker-controlled.example.com")


async def test_public_ip_resolution_is_allowed(
    security: URLSecurityService, monkeypatch: pytest.MonkeyPatch
):
    import dns.asyncresolver

    async def fake_resolve(self, hostname, rdtype):
        if rdtype == "A":
            return [_FakeAnswer("93.184.216.34")]  # example.com's public IP
        import dns.exception

        raise dns.exception.DNSException("no AAAA")

    monkeypatch.setattr(dns.asyncresolver.Resolver, "resolve", fake_resolve)

    ip = await security.resolve_and_validate("example.com")
    assert ip == "93.184.216.34"


def test_check_ip_allowed_blocks_reserved_and_multicast(security: URLSecurityService):
    with pytest.raises(URLSecurityError):
        security.check_ip_allowed("224.0.0.1")  # multicast
    with pytest.raises(URLSecurityError):
        security.check_ip_allowed("240.0.0.1")  # reserved
    security.check_ip_allowed("8.8.8.8")  # public, must not raise
