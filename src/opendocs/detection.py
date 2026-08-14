from __future__ import annotations

import codecs
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from opendocs._models import DocumentType
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTypeMismatchError,
    UnsupportedDocumentError,
)
from opendocs.parsers.office.package import validate_office_package
from opendocs.source import ResolvedSource

_SUFFIX_TYPES = {
    ".txt": DocumentType.TEXT,
    ".md": DocumentType.MARKDOWN,
    ".markdown": DocumentType.MARKDOWN,
    ".pdf": DocumentType.PDF,
    ".png": DocumentType.IMAGE,
    ".jpg": DocumentType.IMAGE,
    ".jpeg": DocumentType.IMAGE,
    ".webp": DocumentType.IMAGE,
    ".docx": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
    ".xlsx": DocumentType.XLSX,
}
_ZIP_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_UTF8_CHUNK_SIZE = 4096


def _signature_type(signature: bytes) -> DocumentType | None:
    if signature.startswith(b"%PDF-"):
        return DocumentType.PDF
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return DocumentType.IMAGE
    if signature.startswith(b"\xff\xd8\xff"):
        return DocumentType.IMAGE
    if signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
        return DocumentType.IMAGE
    return None


def _container_type(path: Path) -> DocumentType:
    try:
        with ZipFile(path) as archive:
            found_docx = False
            found_pptx = False
            found_xlsx = False
            for info in archive.infolist():
                if info.filename == "word/document.xml":
                    found_docx = True
                elif info.filename == "ppt/presentation.xml":
                    found_pptx = True
                elif info.filename == "xl/workbook.xml":
                    found_xlsx = True
    except BadZipFile as error:
        raise CorruptDocumentError("ZIP-based document is corrupt") from error

    if found_docx:
        return DocumentType.DOCX
    if found_pptx:
        return DocumentType.PPTX
    if found_xlsx:
        validate_office_package(path, document_type=DocumentType.XLSX)
        return DocumentType.XLSX
    raise UnsupportedDocumentError("ZIP container is neither DOCX nor PPTX")


def _require_utf8(path: Path) -> None:
    try:
        decoder = codecs.getincrementaldecoder("utf-8")()
        with path.open("rb") as handle:
            while chunk := handle.read(_UTF8_CHUNK_SIZE):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise CorruptDocumentError("text document is not valid UTF-8") from error


def _mismatch(suffix: str, detected: DocumentType | None) -> DocumentTypeMismatchError:
    actual = detected.value if detected is not None else "unknown content"
    return DocumentTypeMismatchError(f"declared extension {suffix} is incompatible with {actual}")


def detect_document_type(source: ResolvedSource) -> DocumentType:
    with source.path.open("rb") as handle:
        signature = handle.read(16)

    suffix = Path(source.original_name).suffix.lower() if source.original_name else ""
    if suffix and suffix not in _SUFFIX_TYPES:
        raise UnsupportedDocumentError(f"unsupported document extension: {suffix}")

    declared = _SUFFIX_TYPES.get(suffix)
    detected = _signature_type(signature)
    if detected is None and signature.startswith(_ZIP_PREFIXES):
        try:
            detected = _container_type(source.path)
        except UnsupportedDocumentError:
            if declared is not DocumentType.XLSX:
                raise
            validate_office_package(source.path, document_type=DocumentType.XLSX)
            detected = DocumentType.XLSX

    if declared is DocumentType.TEXT or declared is DocumentType.MARKDOWN:
        if detected is not None:
            raise _mismatch(suffix, detected)
        _require_utf8(source.path)
        return declared

    if declared is not None:
        if detected is not declared:
            raise _mismatch(suffix, detected)
        return declared

    if detected is not None:
        return detected

    try:
        _require_utf8(source.path)
    except CorruptDocumentError as error:
        raise UnsupportedDocumentError("unnamed bytes have no supported signature") from error
    return DocumentType.TEXT
