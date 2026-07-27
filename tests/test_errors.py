from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

import opendocs


def test_invalid_source_error_exposes_expected_base_fields() -> None:
    error = opendocs.InvalidSourceError("bad source")

    assert isinstance(error, opendocs.OpenDocsError)
    assert error.code is opendocs.OpenDocsErrorCode.INVALID_SOURCE
    assert error.retryable is False
    assert str(error) == "bad source"
    assert "invalid_source" in repr(error)


@pytest.mark.parametrize(
    ("error_cls", "expected_code", "message", "retryable"),
    [
        (opendocs.InvalidSourceError, opendocs.OpenDocsErrorCode.INVALID_SOURCE, "invalid", False),
        (
            opendocs.UnsupportedDocumentError,
            opendocs.OpenDocsErrorCode.UNSUPPORTED_DOCUMENT,
            "unsupported",
            False,
        ),
        (
            opendocs.DocumentTypeMismatchError,
            opendocs.OpenDocsErrorCode.DOCUMENT_TYPE_MISMATCH,
            "mismatch",
            False,
        ),
        (
            opendocs.CorruptDocumentError,
            opendocs.OpenDocsErrorCode.CORRUPT_DOCUMENT,
            "corrupt",
            False,
        ),
        (
            opendocs.LimitExceededError,
            opendocs.OpenDocsErrorCode.LIMIT_EXCEEDED,
            "limit",
            False,
        ),
        (
            opendocs.DocumentTimeoutError,
            opendocs.OpenDocsErrorCode.TIMEOUT,
            "timeout",
            True,
        ),
        (
            opendocs.VisionRequiredError,
            opendocs.OpenDocsErrorCode.VISION_REQUIRED,
            "vision",
            False,
        ),
        (
            opendocs.NoUsableContentError,
            opendocs.OpenDocsErrorCode.NO_USABLE_CONTENT,
            "empty",
            False,
        ),
    ],
)
def test_concrete_errors_map_to_stable_codes(
    error_cls: Callable[[str], opendocs.OpenDocsError],
    expected_code: opendocs.OpenDocsErrorCode,
    message: str,
    retryable: bool,
) -> None:
    error = error_cls(message)

    assert error.code is expected_code
    assert error.retryable is retryable
    assert str(error) == message


def test_sync_in_async_context_error_uses_stable_default_message() -> None:
    error = opendocs.SyncInAsyncContextError()

    assert error.code is opendocs.OpenDocsErrorCode.SYNC_IN_ASYNC_CONTEXT
    assert (
        str(error) == "parse() cannot run inside an active event loop; use await aparse() instead"
    )
    assert error.retryable is False


def test_error_code_contains_public_model_error_codes() -> None:
    assert opendocs.OpenDocsErrorCode.MODEL_AUTHENTICATION == "model_authentication"
    assert opendocs.OpenDocsErrorCode.MODEL_PERMISSION == "model_permission"
    assert opendocs.OpenDocsErrorCode.MODEL_INVALID_REQUEST == "model_invalid_request"
    assert opendocs.OpenDocsErrorCode.MODEL_UNAVAILABLE == "model_unavailable"


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("message", {"message": cast(Any, 123), "code": "invalid_source"}),
    ],
)
def test_open_docs_error_rejects_invalid_base_argument_types(
    field_name: str,
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(TypeError, match=field_name):
        opendocs.OpenDocsError(**kwargs)


def test_open_docs_error_rejects_invalid_code_type() -> None:
    with pytest.raises(TypeError, match="code"):
        opendocs.OpenDocsError("bad source", code=cast(Any, "invalid_source"))


def test_open_docs_error_rejects_invalid_retryable_type() -> None:
    with pytest.raises(TypeError, match="retryable"):
        opendocs.OpenDocsError(
            "bad source",
            code=opendocs.OpenDocsErrorCode.INVALID_SOURCE,
            retryable=cast(Any, 1),
        )


def test_warning_uses_keyword_only_code_parameter() -> None:
    warning = opendocs.OpenDocsWarning("warning message", code="limit_exceeded")

    assert warning.code == "limit_exceeded"
    assert str(warning) == "warning message"


def test_warning_rejects_legacy_positional_signature() -> None:
    with pytest.raises(TypeError):
        cast(Any, opendocs.OpenDocsWarning)("limit_exceeded", "warning message")
