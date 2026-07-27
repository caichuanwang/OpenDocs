from __future__ import annotations

from collections.abc import Callable

import pytest

from opendocs.errors import (
    CorruptDocumentError,
    DocumentTimeoutError,
    DocumentTypeMismatchError,
    InvalidSourceError,
    LimitExceededError,
    NoUsableContentError,
    OpenDocsError,
    OpenDocsErrorCode,
    OpenDocsWarning,
    SyncInAsyncContextError,
    UnsupportedDocumentError,
    VisionRequiredError,
)


def test_invalid_source_error_exposes_expected_base_fields() -> None:
    error = InvalidSourceError("bad source")

    assert isinstance(error, OpenDocsError)
    assert error.code is OpenDocsErrorCode.INVALID_SOURCE
    assert error.retryable is False
    assert str(error) == "bad source"
    assert "invalid_source" in repr(error)


@pytest.mark.parametrize(
    ("error_cls", "expected_code", "message", "retryable"),
    [
        (InvalidSourceError, OpenDocsErrorCode.INVALID_SOURCE, "invalid", False),
        (
            UnsupportedDocumentError,
            OpenDocsErrorCode.UNSUPPORTED_DOCUMENT,
            "unsupported",
            False,
        ),
        (
            DocumentTypeMismatchError,
            OpenDocsErrorCode.DOCUMENT_TYPE_MISMATCH,
            "mismatch",
            False,
        ),
        (CorruptDocumentError, OpenDocsErrorCode.CORRUPT_DOCUMENT, "corrupt", False),
        (LimitExceededError, OpenDocsErrorCode.LIMIT_EXCEEDED, "limit", False),
        (DocumentTimeoutError, OpenDocsErrorCode.TIMEOUT, "timeout", True),
        (VisionRequiredError, OpenDocsErrorCode.VISION_REQUIRED, "vision", False),
        (NoUsableContentError, OpenDocsErrorCode.NO_USABLE_CONTENT, "empty", False),
    ],
)
def test_concrete_errors_map_to_stable_codes(
    error_cls: Callable[[str], OpenDocsError],
    expected_code: OpenDocsErrorCode,
    message: str,
    retryable: bool,
) -> None:
    error = error_cls(message)

    assert error.code is expected_code
    assert error.retryable is retryable
    assert str(error) == message


def test_sync_in_async_context_error_uses_stable_default_message() -> None:
    error = SyncInAsyncContextError()

    assert error.code is OpenDocsErrorCode.SYNC_IN_ASYNC_CONTEXT
    assert str(error) == "parse() active event loop use await aparse()"
    assert error.retryable is False


def test_error_code_contains_public_model_error_codes() -> None:
    assert OpenDocsErrorCode.MODEL_AUTHENTICATION == "model_authentication"
    assert OpenDocsErrorCode.MODEL_PERMISSION == "model_permission"
    assert OpenDocsErrorCode.MODEL_INVALID_REQUEST == "model_invalid_request"
    assert OpenDocsErrorCode.MODEL_UNAVAILABLE == "model_unavailable"


def test_warning_keeps_string_code() -> None:
    warning = OpenDocsWarning("limit_exceeded", "warning message")

    assert warning.code == "limit_exceeded"
    assert str(warning) == "warning message"
