from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


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


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    markdown: str


Block: TypeAlias = TextBlock | MarkdownBlock


@dataclass(frozen=True, slots=True)
class WarningRecord:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    document_type: DocumentType
    blocks: tuple[Block, ...]
    warnings: tuple[WarningRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderResult:
    markdown: str
    warnings: tuple[WarningRecord, ...] = ()
