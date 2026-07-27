from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import opendocs.detection as detection_module
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
        ("notes.md", b"# hello", DocumentType.MARKDOWN),
        ("NOTES.MARKDOWN", b"# hello", DocumentType.MARKDOWN),
        ("paper.pdf", b"%PDF-1.7\n", DocumentType.PDF),
        ("image.png", b"\x89PNG\r\n\x1a\n", DocumentType.IMAGE),
        ("image.jpg", b"\xff\xd8\xff\xe0", DocumentType.IMAGE),
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


@pytest.mark.parametrize(
    ("name", "member", "expected"),
    [
        ("file.docx", "word/document.xml", DocumentType.DOCX),
        ("file.pptx", "ppt/presentation.xml", DocumentType.PPTX),
    ],
)
def test_named_office_suffixes_match_container_type(
    tmp_path: Path,
    name: str,
    member: str,
    expected: DocumentType,
) -> None:
    path = tmp_path / "office"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")

    assert detect_document_type(_resolved(path, name)) is expected


def test_container_detection_prefers_docx_when_both_markers_exist(tmp_path: Path) -> None:
    path = tmp_path / "office"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<ppt/>")
        archive.writestr("word/document.xml", "<docx/>")

    assert detect_document_type(_resolved(path)) is DocumentType.DOCX


def test_unnamed_utf8_bytes_default_to_text(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes("中文内容".encode())

    assert detect_document_type(_resolved(path)) is DocumentType.TEXT


def test_utf8_validation_succeeds_when_multibyte_character_spans_chunk_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"a" + "中".encode())
    monkeypatch.setattr(detection_module, "_UTF8_CHUNK_SIZE", 2)

    assert detect_document_type(_resolved(path, "notes.txt")) is DocumentType.TEXT


@pytest.mark.parametrize("name", ["notes.txt", "notes.md", "notes.markdown"])
def test_named_text_formats_require_utf8(tmp_path: Path, name: str) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"\xff")

    with pytest.raises(CorruptDocumentError, match="UTF-8"):
        detect_document_type(_resolved(path, name))


def test_named_text_formats_reject_truncated_utf8_sequences(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"\xe4\xb8")

    with pytest.raises(CorruptDocumentError, match="UTF-8"):
        detect_document_type(_resolved(path, "notes.txt"))


def test_unnamed_non_utf8_bytes_without_signature_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"\xff")

    with pytest.raises(UnsupportedDocumentError, match="supported signature"):
        detect_document_type(_resolved(path))


def test_unnamed_truncated_utf8_without_signature_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"\xe4\xb8")

    with pytest.raises(UnsupportedDocumentError, match="supported signature"):
        detect_document_type(_resolved(path))


def test_text_validation_does_not_use_path_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source"
    path.write_text("hello", encoding="utf-8")

    def _fail_read_bytes(self: Path) -> bytes:
        raise AssertionError("read_bytes should not be used for text validation")

    monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes)

    assert detect_document_type(_resolved(path, "notes.txt")) is DocumentType.TEXT


def test_extension_cannot_override_signature(tmp_path: Path) -> None:
    path = tmp_path / "source"
    path.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(DocumentTypeMismatchError, match="txt"):
        detect_document_type(_resolved(path, "wrong.txt"))


@pytest.mark.parametrize(
    ("name", "member"),
    [
        ("file.docx", "ppt/presentation.xml"),
        ("file.pptx", "word/document.xml"),
    ],
)
def test_office_suffix_must_match_detected_container_type(
    tmp_path: Path,
    name: str,
    member: str,
) -> None:
    path = tmp_path / "office"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")

    with pytest.raises(DocumentTypeMismatchError):
        detect_document_type(_resolved(path, name))


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
