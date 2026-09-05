import logging

from app.core.logging import RequestIdFilter, request_id_var


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )


def test_filter_injects_dash_when_no_request_id_set():
    record = _make_record()
    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "-"


def test_filter_injects_current_request_id():
    token = request_id_var.set("abc-123")
    try:
        record = _make_record()
        RequestIdFilter().filter(record)
        assert record.request_id == "abc-123"
    finally:
        request_id_var.reset(token)
