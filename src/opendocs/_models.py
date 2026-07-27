from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


def _require_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _require_document_type(value: object) -> DocumentType:
    if not isinstance(value, DocumentType):
        raise TypeError("document_type must be a DocumentType")
    return value


def _require_tuple(name: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    return value


def _require_block(name: str, index: int, value: object) -> Block:
    if not isinstance(value, TextBlock | MarkdownBlock):
        raise TypeError(f"{name}[{index}] must be a TextBlock or MarkdownBlock")
    return value


def _require_warning_record(name: str, index: int, value: object) -> WarningRecord:
    if not isinstance(value, WarningRecord):
        raise TypeError(f"{name}[{index}] must be a WarningRecord")
    return value


class DocumentType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    PPTX = "pptx"


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str

    def __post_init__(self) -> None:
        _require_string("text", self.text)


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    markdown: str

    def __post_init__(self) -> None:
        _require_string("markdown", self.markdown)


Block: TypeAlias = TextBlock | MarkdownBlock


@dataclass(frozen=True, slots=True)
class WarningRecord:
    code: str
    message: str

    def __post_init__(self) -> None:
        _require_string("code", self.code)
        _require_string("message", self.message)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    document_type: DocumentType
    blocks: tuple[Block, ...]
    warnings: tuple[WarningRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_document_type(self.document_type)

        blocks = _require_tuple("blocks", self.blocks)
        for index, block in enumerate(blocks):
            _require_block("blocks", index, block)

        warnings = _require_tuple("warnings", self.warnings)
        for index, warning in enumerate(warnings):
            _require_warning_record("warnings", index, warning)


@dataclass(frozen=True, slots=True)
class RenderResult:
    markdown: str
    warnings: tuple[WarningRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_string("markdown", self.markdown)

        warnings = _require_tuple("warnings", self.warnings)
        for index, warning in enumerate(warnings):
            _require_warning_record("warnings", index, warning)
