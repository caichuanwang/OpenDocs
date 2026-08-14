from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from opendocs import CorruptDocumentError, ParseOptions, UnsupportedDocumentError
from opendocs._models import DocumentType, ParsedDocument, TextBlock
from opendocs._runtime import ParserRuntime
from opendocs.parsers.base import DocumentParser
from opendocs.parsers.office.parser import OfficeParser
from opendocs.parsers.registry import ParserRegistry, build_default_registry
from opendocs.parsers.xlsx import XlsxParser
from opendocs.source import ParseWorkspace, ResolvedSource
from tests.xlsx_fixtures import write_xlsx


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


def test_injected_default_registry_registers_all_core_types(tmp_path) -> None:
    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    try:
        registry = build_default_registry(runtime)
        assert registry.get(DocumentType.TEXT)
        assert registry.get(DocumentType.MARKDOWN)
        assert registry.get(DocumentType.IMAGE)
        assert registry.get(DocumentType.PDF)
        assert isinstance(registry.get(DocumentType.DOCX), OfficeParser)
        assert isinstance(registry.get(DocumentType.PPTX), OfficeParser)
        assert isinstance(registry.get(DocumentType.XLSX), XlsxParser)
    finally:
        import asyncio

        asyncio.run(runtime.aclose())


def test_default_registry_without_runtime_keeps_binary_formats_unavailable() -> None:
    registry = build_default_registry()

    for document_type in (
        DocumentType.IMAGE,
        DocumentType.PDF,
        DocumentType.DOCX,
        DocumentType.PPTX,
        DocumentType.XLSX,
    ):
        with pytest.raises(UnsupportedDocumentError, match=document_type.value):
            registry.get(document_type)


@pytest.mark.asyncio
async def test_xlsx_parser_seam_is_callable_and_prevalidates_the_package(tmp_path) -> None:
    parser = XlsxParser()
    valid = tmp_path / "valid.xlsx"
    write_xlsx(valid)

    with pytest.raises(UnsupportedDocumentError, match="XLSX content parsing"):
        await parser.parse(_resolved(valid), options=ParseOptions())

    corrupt = tmp_path / "corrupt.xlsx"
    corrupt.write_bytes(b"not-a-zip")
    with pytest.raises(CorruptDocumentError):
        await parser.parse(_resolved(corrupt), options=ParseOptions())


def _resolved(path: Path) -> ResolvedSource:
    return ResolvedSource(path=path, original_name="workbook.xlsx", owned=False)
