from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from opendocs._models import DocumentType
from opendocs.errors import UnsupportedDocumentError
from opendocs.parsers.base import DocumentParser

if TYPE_CHECKING:
    from opendocs._runtime import ParserRuntime
    from opendocs.options import VisionConfig
    from opendocs.vision.base import VisionClient


def _require_document_type(value: object) -> DocumentType:
    if not isinstance(value, DocumentType):
        raise TypeError("document_type must be a DocumentType")
    return value


def _require_document_parser(value: object) -> DocumentParser:
    if not isinstance(value, DocumentParser):
        raise TypeError("parser must conform to DocumentParser")
    parse = value.parse
    if not callable(parse) or not inspect.iscoroutinefunction(parse):
        raise TypeError("parser must provide an async parse method")
    return value


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[DocumentType, DocumentParser] = {}

    def register(self, document_type: DocumentType, parser: DocumentParser) -> None:
        document_type = _require_document_type(document_type)
        parser = _require_document_parser(parser)
        if document_type in self._parsers:
            raise ValueError(f"parser already registered for {document_type.value}")
        self._parsers[document_type] = parser

    def get(self, document_type: DocumentType) -> DocumentParser:
        document_type = _require_document_type(document_type)
        try:
            return self._parsers[document_type]
        except KeyError as error:
            raise UnsupportedDocumentError(
                f"support for {document_type.value} is not installed in this release"
            ) from error


def build_default_registry(
    runtime: ParserRuntime | None = None,
    vision: VisionClient | None = None,
    vision_config: VisionConfig | None = None,
    *,
    deadline: float | None = None,
) -> ParserRegistry:
    from opendocs.parsers.text import TextParser

    registry = ParserRegistry()
    registry.register(DocumentType.TEXT, TextParser(DocumentType.TEXT))
    registry.register(DocumentType.MARKDOWN, TextParser(DocumentType.MARKDOWN))
    if runtime is None:
        if vision is not None or vision_config is not None or deadline is not None:
            raise ValueError("parser dependencies require a parser runtime")
        return registry

    from opendocs.parsers.image import ImageParser
    from opendocs.parsers.pdf.parser import PDFParser

    registry.register(DocumentType.IMAGE, ImageParser(runtime, vision, vision_config))
    registry.register(
        DocumentType.PDF,
        PDFParser(runtime, vision, vision_config, deadline=deadline),
    )
    return registry
