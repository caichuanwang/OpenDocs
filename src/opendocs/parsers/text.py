from __future__ import annotations

import asyncio
import re

from opendocs._models import DocumentType, MarkdownBlock, ParsedDocument, TextBlock
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.options import ParseOptions
from opendocs.source import ResolvedSource

_MAX_TEXT_BYTES = 20_000_000
_PARAGRAPH_BREAK = re.compile(r"\n(?:[ \t]*\n)+")


def _read_utf8(source: ResolvedSource) -> str:
    with source.path.open("rb") as handle:
        data = handle.read(_MAX_TEXT_BYTES + 1)

    if len(data) > _MAX_TEXT_BYTES:
        raise LimitExceededError(f"text source exceeds {_MAX_TEXT_BYTES} bytes")

    try:
        value = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorruptDocumentError("text source is not valid UTF-8") from error

    return value.replace("\r\n", "\n").replace("\r", "\n")


def _require_supported_document_type(value: object) -> DocumentType:
    if not isinstance(value, DocumentType):
        raise TypeError("document_type must be a DocumentType")
    if value not in {DocumentType.TEXT, DocumentType.MARKDOWN}:
        raise ValueError("TextParser supports only text and markdown")
    return value


class TextParser:
    def __init__(self, document_type: DocumentType) -> None:
        self._document_type = _require_supported_document_type(document_type)

    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        del options
        value = await asyncio.to_thread(_read_utf8, source)

        if self._document_type is DocumentType.MARKDOWN:
            blocks = (MarkdownBlock(markdown=value),)
        else:
            blocks = tuple(
                TextBlock(text=paragraph)
                for paragraph in _PARAGRAPH_BREAK.split(value)
                if paragraph.strip()
            )

        return ParsedDocument(document_type=self._document_type, blocks=blocks)
