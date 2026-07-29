from __future__ import annotations

from typing import Any, cast

from opendocs import ParseOptions, UnsupportedDocumentError
from opendocs._models import DocumentType, ParsedDocument, TextBlock
from opendocs._runtime import ParserRuntime
from opendocs.parsers.base import DocumentParser
from opendocs.parsers.registry import ParserRegistry, build_default_registry
from opendocs.source import ParseWorkspace, ResolvedSource


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


class MissingParseParser:
    pass


class NonCallableParseParser:
    parse = 123


class SyncParseParser:
    def parse(
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


def test_registry_rejects_non_document_type_on_register() -> None:
    registry = ParserRegistry()

    try:
        registry.register(cast(Any, "text"), StubParser())
    except TypeError as error:
        assert "DocumentType" in str(error)
    else:
        raise AssertionError("non-DocumentType registration must fail")


def test_registry_rejects_non_parser_registration() -> None:
    registry = ParserRegistry()

    try:
        registry.register(DocumentType.TEXT, cast(Any, MissingParseParser()))
    except TypeError as error:
        assert "DocumentParser" in str(error)
    else:
        raise AssertionError("non-parser registration must fail")


def test_registry_rejects_non_callable_parse_registration() -> None:
    registry = ParserRegistry()

    try:
        registry.register(DocumentType.TEXT, cast(Any, NonCallableParseParser()))
    except TypeError as error:
        assert "async" in str(error)
    else:
        raise AssertionError("non-callable parse registration must fail")


def test_registry_rejects_sync_parse_registration() -> None:
    registry = ParserRegistry()

    try:
        registry.register(DocumentType.TEXT, cast(Any, SyncParseParser()))
    except TypeError as error:
        assert "async" in str(error)
    else:
        raise AssertionError("sync parse registration must fail")


def test_registry_raises_typed_error_for_unimplemented_format() -> None:
    registry = ParserRegistry()

    try:
        registry.get(DocumentType.PDF)
    except UnsupportedDocumentError as error:
        assert "pdf" in str(error)
    else:
        raise AssertionError("missing parser must fail")


def test_registry_rejects_non_document_type_on_get() -> None:
    registry = ParserRegistry()

    try:
        registry.get(cast(Any, "text"))
    except TypeError as error:
        assert "DocumentType" in str(error)
    else:
        raise AssertionError("non-DocumentType lookup must fail")


def test_stub_satisfies_parser_protocol() -> None:
    parser: DocumentParser = StubParser()
    assert isinstance(parser, DocumentParser)


def test_injected_default_registry_registers_all_m1_types(tmp_path) -> None:
    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    try:
        registry = build_default_registry(runtime)
        assert registry.get(DocumentType.TEXT)
        assert registry.get(DocumentType.MARKDOWN)
        assert registry.get(DocumentType.IMAGE)
        assert registry.get(DocumentType.PDF)
    finally:
        import asyncio

        asyncio.run(runtime.aclose())
