from __future__ import annotations

from enum import StrEnum


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
            "parse() active event loop use await aparse()",
            code=OpenDocsErrorCode.SYNC_IN_ASYNC_CONTEXT,
        )


class OpenDocsWarning(UserWarning):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
