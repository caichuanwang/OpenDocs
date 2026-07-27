from __future__ import annotations

from opendocs import ParseOptions, UnsupportedDocumentError
from opendocs._models import DocumentType, ParsedDocument, TextBlock
from opendocs.parsers.base import DocumentParser
from opendocs.parsers.registry import ParserRegistry
from opendocs.source import ResolvedSource


class StubParser:
    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        return ParsedDocument(
            document_type=DocumentType.TEXT,
            blocks=(TextBlock(text=source.path.name),),
        )


def test_registry_returns_the_exact_registered_parser() -> None:
    parser = StubParser()
    registry = ParserRegistry()
    registry.register(DocumentType.TEXT, parser)

    assert registry.get(DocumentType.TEXT) is parser


def test_registry_rejects_duplicate_registration() -> None:
    registry = ParserRegistry()
    registry.register(DocumentType.TEXT, StubParser())

    try:
        registry.register(DocumentType.TEXT, StubParser())
    except ValueError as error:
        assert "text" in str(error)
    else:
        raise AssertionError("duplicate registration must fail")


def test_registry_raises_typed_error_for_unimplemented_format() -> None:
    registry = ParserRegistry()

    try:
        registry.get(DocumentType.PDF)
    except UnsupportedDocumentError as error:
        assert "pdf" in str(error)
    else:
        raise AssertionError("missing parser must fail")


def test_stub_satisfies_parser_protocol() -> None:
    parser: DocumentParser = StubParser()
    assert isinstance(parser, StubParser)
