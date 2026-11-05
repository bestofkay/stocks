"""Log redaction filter tests."""

from __future__ import annotations

import logging

from app.core.logging import RedactingFilter


def test_redacting_filter_masks_password():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='login password=supersecret token=abc123',
        args=(),
        exc_info=None,
    )
    filt = RedactingFilter()
    assert filt.filter(record) is True
    assert "supersecret" not in record.msg
    assert "[REDACTED]" in record.msg
