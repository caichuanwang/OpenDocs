from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from opendocs import (
    CorruptDocumentError,
    DocumentTypeMismatchError,
    UnsupportedDocumentError,
)
from opendocs._models import DocumentType
from opendocs.detection import detect_document_type
from opendocs.source import ResolvedSource


def _resolved(path: Path, name: str | None = None) -> ResolvedSource:
    return ResolvedSource(path=path, original_name=name, owned=False)


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("notes.txt", b"hello", DocumentType.TEXT),
        ("NOTES.MARKDOWN", b"# hello", DocumentType.MARKDOWN),
        ("paper.pdf", b"%PDF-1.7\n", DocumentType.PDF),
        ("image.png", b"\x89PNG\r\n\x1a\n", DocumentType.IMAGE),
        ("IMAGE.JPEG", b"\xff\xd8\xff\xe0", DocumentType.IMAGE),
        ("image.webp", b"RIFF\x00\x00\x00\x00WEBP", DocumentType.IMAGE),
    ],
)
def test_detects_simple_formats(
    tmp_path: Path,
    name: str,
    content: bytes,
    expected: DocumentType,
) -> None:
    path = tmp_path / "source"
    path.write_bytes(content)

    assert detect_document_type(_resolved(path, name)) is expected


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("word/document.xml", DocumentType.DOCX),
        ("ppt/presentation.xml", DocumentType.PPTX),
    ],
)
def test_detects_office_containers(
    tmp_path: Path,
    member: str,
    expected: DocumentType,
) -> None:
    path = tmp_path / "office"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")

    assert detect_document_type(_resolved(path)) is expected


def test_unnamed_utf8_bytes_default_to_text(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes("中文内容".encode())

    assert detect_document_type(_resolved(path)) is DocumentType.TEXT


@pytest.mark.parametrize("name", ["notes.txt", "notes.md", "notes.markdown"])
def test_named_text_formats_require_utf8(tmp_path: Path, name: str) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"\xff")

    with pytest.raises(CorruptDocumentError, match="UTF-8"):
        detect_document_type(_resolved(path, name))


def test_unnamed_non_utf8_bytes_without_signature_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"\xff")

    with pytest.raises(UnsupportedDocumentError, match="supported signature"):
        detect_document_type(_resolved(path))


def test_extension_cannot_override_signature(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(DocumentTypeMismatchError, match="txt"):
        detect_document_type(_resolved(path, "wrong.txt"))


def test_named_unknown_extension_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_text("hello", encoding="utf-8")

    with pytest.raises(UnsupportedDocumentError, match=r"\.unknown"):
        detect_document_type(_resolved(path, "file.unknown"))


def test_corrupt_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"PK\x03\x04not-a-zip")

    with pytest.raises(CorruptDocumentError, match="corrupt"):
        detect_document_type(_resolved(path))


def test_pdf_extension_requires_pdf_signature(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_text("not a PDF", encoding="utf-8")

    with pytest.raises(DocumentTypeMismatchError, match=r"\.pdf"):
        detect_document_type(_resolved(path, "false.pdf"))


def test_unrelated_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("data.json", "{}")

    with pytest.raises(UnsupportedDocumentError, match="neither DOCX nor PPTX"):
        detect_document_type(_resolved(path))
