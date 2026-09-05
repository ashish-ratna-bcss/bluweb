import pytest

from app.services.crawling.failure_classification import (
    FailureCategory,
    classify_http_status,
    classify_transport_error,
    is_removal_eligible,
)


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, FailureCategory.HTTP_403),
        (404, FailureCategory.HTTP_404),
        (410, FailureCategory.HTTP_410),
        (429, FailureCategory.HTTP_429),
        (500, FailureCategory.HTTP_5XX),
        (503, FailureCategory.HTTP_5XX),
        (599, FailureCategory.HTTP_5XX),
        (451, FailureCategory.OTHER_HTTP_ERROR),
        (200, FailureCategory.NONE),
        (301, FailureCategory.NONE),
    ],
)
def test_classify_http_status(status: int, expected: FailureCategory):
    assert classify_http_status(status) == expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Name or service not known", FailureCategory.DNS_FAILURE),
        ("[Errno -2] Name does not resolve: dns lookup failed", FailureCategory.DNS_FAILURE),
        ("SSL: CERTIFICATE_VERIFY_FAILED", FailureCategory.TLS_FAILURE),
        ("ConnectTimeout: timed out", FailureCategory.TIMEOUT),
        ("ConnectError: Connection refused", FailureCategory.CONNECTION_FAILURE),
        ("Connection reset by peer", FailureCategory.CONNECTION_FAILURE),
    ],
)
def test_classify_transport_error(message: str, expected: FailureCategory):
    assert classify_transport_error(message) == expected


def test_blocked_and_throttled_are_never_removal_eligible():
    assert is_removal_eligible(FailureCategory.HTTP_403) is False
    assert is_removal_eligible(FailureCategory.HTTP_429) is False
    assert is_removal_eligible(FailureCategory.ROBOTS_BLOCKED) is False


def test_genuinely_gone_and_unreachable_are_removal_eligible():
    assert is_removal_eligible(FailureCategory.HTTP_404) is True
    assert is_removal_eligible(FailureCategory.HTTP_410) is True
    assert is_removal_eligible(FailureCategory.DNS_FAILURE) is True
    assert is_removal_eligible(FailureCategory.TIMEOUT) is True
    assert is_removal_eligible(FailureCategory.HTTP_5XX) is True


def test_success_is_not_removal_eligible():
    assert is_removal_eligible(FailureCategory.NONE) is False
