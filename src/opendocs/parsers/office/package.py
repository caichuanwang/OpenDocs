from __future__ import annotations

import hashlib
import posixpath
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeVar
from zipfile import BadZipFile, ZipFile, ZipInfo

from opendocs._models import DocumentType
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.source import ParseWorkspace

MAX_ARCHIVE_MEMBERS = 2_048
MAX_TOTAL_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_XML_PART_BYTES = 4 * 1024 * 1024
MAX_MEDIA_PART_BYTES = 16 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 24 * 1024 * 1024
MAX_MEDIA_PARTS = 256
MAX_COMPRESSION_RATIO = 100

_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_RELATIONSHIP_TAG = f"{_REL_NS}Relationship"
_REQUIRED_PARTS = {
    DocumentType.DOCX: "word/document.xml",
    DocumentType.PPTX: "ppt/presentation.xml",
}
_MEDIA_SEGMENT = "media"
_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".emf",
    ".wmf",
    ".svg",
    ".bin",
    ".rels",
}
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class PackageMediaPart:
    member_name: str
    size: int


@dataclass(frozen=True, slots=True)
class OfficePackageLayout:
    document_type: DocumentType
    main_part_name: str
    media_parts: tuple[PackageMediaPart, ...]


@dataclass(frozen=True, slots=True)
class ExtractedMediaArtifact:
    member_name: str
    artifact_name: str
    path: Path
    content_sha256: str
    size: int


def _safe_name(name: str) -> str:
    if not name or "\\" in name or name.startswith("/"):
        raise CorruptDocumentError("Office package contains an unsafe member name")
    pure = PurePosixPath(name)
    if pure.is_absolute():
        raise CorruptDocumentError("Office package contains an unsafe member name")
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CorruptDocumentError("Office package contains an unsafe member name")
    if ":" in parts[0]:
        raise CorruptDocumentError("Office package contains an unsafe member name")
    return pure.as_posix()


def _is_xml_part(name: str) -> bool:
    return name.endswith(".xml") or name.endswith(".rels") or name == "[Content_Types].xml"


def _is_media_part(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if not parts:
        return False
    return _MEDIA_SEGMENT in parts and not _is_xml_part(name)


def _member_ratio(info: ZipInfo) -> float:
    if info.file_size <= 0:
        return 0.0
    return info.file_size / max(info.compress_size, 1)


def _rels_source_base(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    parent = PurePosixPath(name).parent
    if parent.name != "_rels":
        raise CorruptDocumentError("Office package relationships path is invalid")
    return parent.parent.as_posix()


def _normalize_target(base_dir: str, target: str) -> str:
    if not target or target.startswith(("/", "\\")) or "\\" in target:
        raise CorruptDocumentError("Office package relationship target is invalid")
    if ":" in PurePosixPath(target).parts[:1]:
        raise CorruptDocumentError("Office package relationship target is invalid")
    joined = (
        posixpath.normpath(posixpath.join(base_dir, target))
        if base_dir
        else posixpath.normpath(target)
    )
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        raise CorruptDocumentError("Office package relationship target is invalid")
    return joined


def _parse_relationship_targets(
    archive: ZipFile,
    infos_by_name: dict[str, ZipInfo],
    rels_name: str,
) -> None:
    try:
        root = ET.fromstring(archive.read(rels_name))
    except (KeyError, OSError, ET.ParseError) as error:
        raise CorruptDocumentError("Office package relationships are corrupt") from error
    base_dir = _rels_source_base(rels_name)
    for node in root.iter(_RELATIONSHIP_TAG):
        target = node.get("Target")
        if target is None:
            raise CorruptDocumentError("Office package relationship target is invalid")
        if node.get("TargetMode") == "External":
            continue
        normalized = _normalize_target(base_dir, target)
        if normalized not in infos_by_name:
            raise CorruptDocumentError("Office package relationship target is missing")


def _required_root_target(
    archive: ZipFile,
    infos_by_name: dict[str, ZipInfo],
    document_type: DocumentType,
) -> None:
    if "_rels/.rels" not in infos_by_name:
        raise CorruptDocumentError("Office package root relationships are missing")
    required = _REQUIRED_PARTS[document_type]
    try:
        root = ET.fromstring(archive.read("_rels/.rels"))
    except (OSError, ET.ParseError) as error:
        raise CorruptDocumentError("Office package relationships are corrupt") from error
    for node in root.iter(_RELATIONSHIP_TAG):
        target = node.get("Target")
        if target is None or node.get("TargetMode") == "External":
            continue
        if _normalize_target("", target) == required:
            return
    raise CorruptDocumentError("Office package required root relationship is missing")


def validate_office_package(path: Path, *, document_type: DocumentType) -> OfficePackageLayout:
    required_main_part = _REQUIRED_PARTS.get(document_type)
    if required_main_part is None:
        raise ValueError("document_type must be DOCX or PPTX")
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise LimitExceededError("Office package exceeds member count limit")

            infos_by_name: dict[str, ZipInfo] = {}
            media_parts: list[PackageMediaPart] = []
            total_bytes = 0
            total_media_bytes = 0
            media_count = 0
            for info in infos:
                safe_name = _safe_name(info.filename)
                if safe_name in infos_by_name:
                    raise CorruptDocumentError("Office package contains duplicate member names")
                if info.flag_bits & 0x1:
                    raise CorruptDocumentError("Office package contains encrypted members")
                total_bytes += info.file_size
                if total_bytes > MAX_TOTAL_ARCHIVE_BYTES:
                    raise LimitExceededError("Office package exceeds declared archive size limit")
                ratio = _member_ratio(info)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise LimitExceededError("Office package exceeds compression ratio limit")
                if _is_xml_part(safe_name) and info.file_size > MAX_XML_PART_BYTES:
                    raise LimitExceededError("Office package exceeds XML part size limit")
                if not info.is_dir() and _is_media_part(safe_name):
                    media_count += 1
                    if media_count > MAX_MEDIA_PARTS:
                        raise LimitExceededError("Office package exceeds media count limit")
                    if info.file_size > MAX_MEDIA_PART_BYTES:
                        raise LimitExceededError("Office package exceeds media part size limit")
                    total_media_bytes += info.file_size
                    if total_media_bytes > MAX_TOTAL_MEDIA_BYTES:
                        raise LimitExceededError("Office package exceeds aggregate media limit")
                    media_parts.append(PackageMediaPart(member_name=safe_name, size=info.file_size))
                infos_by_name[safe_name] = info

            if "[Content_Types].xml" not in infos_by_name:
                raise CorruptDocumentError("Office package content types part is missing")
            if required_main_part not in infos_by_name:
                raise CorruptDocumentError("Office package required main part is missing")
            _required_root_target(archive, infos_by_name, document_type)
            for rels_name in tuple(name for name in infos_by_name if name.endswith(".rels")):
                _parse_relationship_targets(archive, infos_by_name, rels_name)
    except BadZipFile as error:
        raise CorruptDocumentError("Office package is corrupt") from error
    except OSError as error:
        raise CorruptDocumentError("Office package could not be read") from error
    return OfficePackageLayout(
        document_type=document_type,
        main_part_name=required_main_part,
        media_parts=tuple(media_parts),
    )


def open_validated_office_document(
    path: Path,
    *,
    document_type: DocumentType,
    opener: Callable[[Path], _T],
) -> _T:
    validate_office_package(path, document_type=document_type)
    return opener(path)


def _artifact_name(index: int, member_name: str) -> str:
    suffix = Path(member_name).suffix.lower()
    if suffix not in _BINARY_SUFFIXES or not suffix:
        suffix = ".bin"
    return f"office-media-{index}{suffix}"


def extract_package_media(
    path: Path,
    *,
    document_type: DocumentType,
    workspace: ParseWorkspace,
) -> tuple[ExtractedMediaArtifact, ...]:
    layout = validate_office_package(path, document_type=document_type)
    workspace.path.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExtractedMediaArtifact] = []
    with ZipFile(path) as archive:
        for index, media in enumerate(layout.media_parts, start=1):
            data = archive.read(media.member_name)
            artifact_name = _artifact_name(index, media.member_name)
            artifact_path = workspace.output_path(artifact_name)
            artifact_path.write_bytes(data)
            artifacts.append(
                ExtractedMediaArtifact(
                    member_name=media.member_name,
                    artifact_name=artifact_name,
                    path=artifact_path,
                    content_sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                )
            )
    return tuple(artifacts)
