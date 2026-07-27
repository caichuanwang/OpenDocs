from __future__ import annotations

from enum import StrEnum


def _require_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _require_bool(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")
    return value


def _require_error_code(value: object) -> OpenDocsErrorCode:
    if not isinstance(value, OpenDocsErrorCode):
        raise TypeError("code must be an OpenDocsErrorCode")
    return value


class OpenDocsErrorCode(StrEnum):
    INVALID_SOURCE = "invalid_source"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    DOCUMENT_TYPE_MISMATCH = "document_type_mismatch"
    CORRUPT_DOCUMENT = "corrupt_document"
    LIMIT_EXCEEDED = "limit_exceeded"
    TIMEOUT = "timeout"
    VISION_REQUIRED = "vision_required"
    MODEL_AUTHENTICATION = "model_authentication"
    MODEL_PERMISSION = "model_permission"
    MODEL_INVALID_REQUEST = "model_invalid_request"
    MODEL_UNAVAILABLE = "model_unavailable"
    NO_USABLE_CONTENT = "no_usable_content"
    SYNC_IN_ASYNC_CONTEXT = "sync_in_async_context"


class OpenDocsError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: OpenDocsErrorCode,
        retryable: bool = False,
    ) -> None:
        _require_string("message", message)
        _require_error_code(code)
        _require_bool("retryable", retryable)
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def __str__(self) -> str:
        return str(self.args[0])

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code.value!r}, retryable={self.retryable!r}, message={self.args[0]!r})"
        )


class InvalidSourceError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.INVALID_SOURCE)


class UnsupportedDocumentError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.UNSUPPORTED_DOCUMENT)


class DocumentTypeMismatchError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.DOCUMENT_TYPE_MISMATCH)


class CorruptDocumentError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.CORRUPT_DOCUMENT)


class LimitExceededError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.LIMIT_EXCEEDED)


class DocumentTimeoutError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.TIMEOUT, retryable=True)


class VisionRequiredError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.VISION_REQUIRED)


class NoUsableContentError(OpenDocsError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=OpenDocsErrorCode.NO_USABLE_CONTENT)


class SyncInAsyncContextError(OpenDocsError):
    def __init__(self) -> None:
        super().__init__(
            "parse() cannot run inside an active event loop; use await aparse() instead",
            code=OpenDocsErrorCode.SYNC_IN_ASYNC_CONTEXT,
        )


class OpenDocsWarning(UserWarning):
    def __init__(self, message: str, *, code: str) -> None:
        _require_string("message", message)
        _require_string("code", code)
        super().__init__(message)
        self.code = code
