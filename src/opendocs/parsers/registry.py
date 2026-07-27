from __future__ import annotations

from opendocs._models import DocumentType
from opendocs.errors import UnsupportedDocumentError
from opendocs.parsers.base import DocumentParser


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[DocumentType, DocumentParser] = {}

    def register(self, document_type: DocumentType, parser: DocumentParser) -> None:
        if document_type in self._parsers:
            raise ValueError(f"parser already registered for {document_type.value}")
        self._parsers[document_type] = parser

    def get(self, document_type: DocumentType) -> DocumentParser:
        try:
            return self._parsers[document_type]
        except KeyError as error:
            raise UnsupportedDocumentError(
                f"support for {document_type.value} is not installed in this release"
            ) from error
