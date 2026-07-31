from __future__ import annotations

import os
import zipfile
from collections.abc import Callable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from opendocs._models import DocumentType
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.parsers.office.package import (
    MAX_ARCHIVE_MEMBERS,
    MAX_MEDIA_PART_BYTES,
    MAX_MEDIA_PARTS,
    MAX_TOTAL_ARCHIVE_BYTES,
    MAX_TOTAL_MEDIA_BYTES,
    MAX_XML_PART_BYTES,
    extract_package_media,
    open_validated_office_document,
    validate_office_package,
)
from opendocs.source import ParseWorkspace


def _write_zip(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name_or_info, data in entries:
            archive.writestr(name_or_info, data)


def _minimal_docx(path: Path) -> None:
    _write_zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            (
                "_rels/.rels",
                b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
   Target="word/document.xml"/>
</Relationships>
""",
            ),
            ("word/document.xml", b"<w:document xmlns:w='urn:test'><w:body/></w:document>"),
        ],
    )


def _minimal_pptx(path: Path) -> None:
    _write_zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            (
                "_rels/.rels",
                b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
   Target="ppt/presentation.xml"/>
</Relationships>
""",
            ),
            (
                "ppt/presentation.xml",
                b"<p:presentation xmlns:p='urn:test'><p:sldIdLst/></p:presentation>",
            ),
        ],
    )


def test_validate_office_package_accepts_minimal_docx_and_pptx(tmp_path: Path) -> None:
    docx = tmp_path / "sample.docx"
    pptx = tmp_path / "sample.pptx"
    _minimal_docx(docx)
    _minimal_pptx(pptx)

    docx_layout = validate_office_package(docx, document_type=DocumentType.DOCX)
    pptx_layout = validate_office_package(pptx, document_type=DocumentType.PPTX)

    assert docx_layout.main_part_name == "word/document.xml"
    assert pptx_layout.main_part_name == "ppt/presentation.xml"


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    [
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    *[
                        (f"word/part-{index}.xml", b"<x/>")
                        for index in range(MAX_ARCHIVE_MEMBERS + 1)
                    ],
                ],
            ),
            LimitExceededError,
            "member count",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("word/document.xml", b"<w/>"),
                    ("word/large.xml", os.urandom(MAX_XML_PART_BYTES + 1)),
                ],
            ),
            LimitExceededError,
            "XML part",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("word/document.xml", b"<w/>"),
                    ("word/media/image1.png", os.urandom(MAX_MEDIA_PART_BYTES + 1)),
                ],
            ),
            LimitExceededError,
            "media part",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("word/document.xml", b"<w/>"),
                    *[
                        (
                            f"word/media/image{index}.png",
                            os.urandom((MAX_TOTAL_MEDIA_BYTES // 4) + 1),
                        )
                        for index in range(4)
                    ],
                ],
            ),
            LimitExceededError,
            "aggregate media",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("word/document.xml", b"<w/>"),
                    *[
                        (f"word/media/image{index}.png", b"x")
                        for index in range(MAX_MEDIA_PARTS + 1)
                    ],
                ],
            ),
            LimitExceededError,
            "media count",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("word/document.xml", b"x" * (MAX_TOTAL_ARCHIVE_BYTES + 1)),
                ],
            ),
            LimitExceededError,
            "archive size",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("word/document.xml", b"<w/>"),
                    *[
                        ("word/document.xml", b"<w/>"),
                    ],
                ],
            ),
            CorruptDocumentError,
            "duplicate",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("../word/document.xml", b"<w/>"),
                ],
            ),
            CorruptDocumentError,
            "unsafe member",
        ),
        (
            lambda path: _write_zip(
                path,
                [
                    ("[Content_Types].xml", b"<Types/>"),
                    ("_rels/.rels", _doc_root_rels("word/document.xml")),
                    ("/word/document.xml", b"<w/>"),
                ],
            ),
            CorruptDocumentError,
            "unsafe member",
        ),
    ],
)
def test_validate_office_package_rejects_size_and_name_failures(
    tmp_path: Path,
    factory: Callable[[Path], None],
    error_type: type[Exception],
    message: str,
) -> None:
    path = tmp_path / "sample.docx"
    factory(path)

    with pytest.raises(error_type, match=message):
        validate_office_package(path, document_type=DocumentType.DOCX)


def test_validate_office_package_rejects_broken_relationships(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    _write_zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            ("_rels/.rels", _doc_root_rels("word/document.xml")),
            ("word/document.xml", b"<w/>"),
            (
                "word/_rels/document.xml.rels",
                b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="urn:test" Target="media/missing.png"/>
</Relationships>
""",
            ),
        ],
    )

    with pytest.raises(CorruptDocumentError, match="relationship target"):
        validate_office_package(path, document_type=DocumentType.DOCX)


def test_validate_office_package_rejects_encrypted_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sample.docx"
    _minimal_docx(path)

    original_infolist = ZipFile.infolist

    def flagged_infolist(self: ZipFile) -> list[zipfile.ZipInfo]:
        infos = original_infolist(self)
        for info in infos:
            if info.filename == "word/document.xml":
                info.flag_bits |= 0x1
        return infos

    monkeypatch.setattr("opendocs.parsers.office.package.ZipFile.infolist", flagged_infolist)

    with pytest.raises(CorruptDocumentError, match="encrypted"):
        validate_office_package(path, document_type=DocumentType.DOCX)


def test_validate_office_package_rejects_suspicious_compression_ratio(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    payload = (b"a" * (2 * 1024 * 1024)) + (b"b" * (2 * 1024 * 1024))
    _write_zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            ("_rels/.rels", _doc_root_rels("word/document.xml")),
            ("word/document.xml", payload),
        ],
    )

    with pytest.raises(LimitExceededError, match="compression ratio"):
        validate_office_package(path, document_type=DocumentType.DOCX)


def test_failed_preflight_happens_before_office_opener(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    path.write_bytes(b"not-a-zip")
    called = False

    def opener(_path: Path) -> object:
        nonlocal called
        called = True
        raise AssertionError("opener should not run")

    with pytest.raises(CorruptDocumentError):
        open_validated_office_document(path, document_type=DocumentType.DOCX, opener=opener)

    assert called is False


def test_extract_package_media_uses_workspace_generated_basenames(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    _write_zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            ("_rels/.rels", _doc_root_rels("word/document.xml")),
            ("word/document.xml", b"<w/>"),
            ("word/media/very/private/folder/logo.png", b"png"),
            ("word/media/diagram 2.jpeg", b"jpg"),
        ],
    )

    artifacts = extract_package_media(
        path,
        document_type=DocumentType.DOCX,
        workspace=ParseWorkspace(tmp_path / "workspace"),
    )

    assert len(artifacts) == 2
    for artifact in artifacts:
        assert Path(artifact.artifact_name).name == artifact.artifact_name
        assert artifact.path.parent == tmp_path / "workspace"
        assert artifact.path.exists()
        assert "private" not in artifact.artifact_name


def test_extract_package_media_ignores_explicit_media_directories(tmp_path: Path) -> None:
    path = tmp_path / "sample.pptx"
    _write_zip(
        path,
        [
            ("[Content_Types].xml", b"<Types/>"),
            ("_rels/.rels", _doc_root_rels("ppt/presentation.xml")),
            ("ppt/presentation.xml", b"<p:presentation xmlns:p='urn:test'/>"),
            ("ppt/media/", b""),
            ("ppt/media/image1.png", b"png"),
        ],
    )

    artifacts = extract_package_media(
        path,
        document_type=DocumentType.PPTX,
        workspace=ParseWorkspace(tmp_path / "workspace"),
    )

    assert len(artifacts) == 1
    assert artifacts[0].member_name == "ppt/media/image1.png"


def _doc_root_rels(target: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
   Target="{target}"/>
</Relationships>
""".encode()
