from opendocs.api import aparse, parse
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
from opendocs.options import ParseOptions, VisionConfig

__version__ = "0.1.0"

__all__ = [
    "CorruptDocumentError",
    "DocumentTimeoutError",
    "DocumentTypeMismatchError",
    "InvalidSourceError",
    "LimitExceededError",
    "NoUsableContentError",
    "OpenDocsError",
    "OpenDocsErrorCode",
    "OpenDocsWarning",
    "ParseOptions",
    "SyncInAsyncContextError",
    "UnsupportedDocumentError",
    "VisionConfig",
    "VisionRequiredError",
    "__version__",
    "aparse",
    "parse",
]
