from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from opendocs import (
    CorruptDocumentError,
    LimitExceededError,
    NoUsableContentError,
    ParseOptions,
    UnsupportedDocumentError,
)
from opendocs._models import DocumentType, MarkdownBlock, TextBlock
from opendocs.markdown import render_markdown
from opendocs.parsers.registry import build_default_registry
from opendocs.source import ResolvedSource


@pytest.mark.asyncio
async def test_markdown_parser_preserves_source_markdown(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_bytes(b"# Heading\r\n\r\n- one\rline\n")
    parser = build_default_registry().get(DocumentType.MARKDOWN)

    document = await parser.parse(
        ResolvedSource(path=path, original_name=path.name, owned=False),
        options=ParseOptions(),
    )

    assert document.blocks == (MarkdownBlock(markdown="# Heading\n\n- one\nline\n"),)


@pytest.mark.asyncio
async def test_text_parser_splits_paragraphs_without_markdown_interpretation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"first\r\n\r\n*literal*\r\n")
    parser = build_default_registry().get(DocumentType.TEXT)

    document = await parser.parse(
        ResolvedSource(path=path, original_name=path.name, owned=False),
        options=ParseOptions(),
    )

    assert document.blocks == (TextBlock(text="first"), TextBlock(text="*literal*\n"))


@pytest.mark.asyncio
async def test_text_parser_consumes_consecutive_blank_separator_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "paragraphs.txt"
    path.write_bytes(b"alpha\n\n\nbeta")
    parser = build_default_registry().get(DocumentType.TEXT)

    document = await parser.parse(
        ResolvedSource(path=path, original_name=path.name, owned=False),
        options=ParseOptions(),
    )

    assert document.blocks == (TextBlock(text="alpha"), TextBlock(text="beta"))
    assert render_markdown(document, max_output_chars=400_000).markdown == "alpha\n\nbeta\n"


@pytest.mark.asyncio
async def test_text_parser_consumes_whitespace_only_separator_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "whitespace.txt"
    path.write_bytes(b"alpha\n \t\n\t \n beta\n")
    parser = build_default_registry().get(DocumentType.TEXT)

    document = await parser.parse(
        ResolvedSource(path=path, original_name=path.name, owned=False),
        options=ParseOptions(),
    )

    assert document.blocks == (TextBlock(text="alpha"), TextBlock(text=" beta\n"))
    assert render_markdown(document, max_output_chars=400_000).markdown == "alpha\n\n beta\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_type", "name", "message"),
    [
        (DocumentType.TEXT, "empty.txt", "text document is empty"),
        (DocumentType.MARKDOWN, "empty.md", "markdown document is empty"),
    ],
)
async def test_text_parser_reports_empty_documents_explicitly(
    tmp_path: Path,
    document_type: DocumentType,
    name: str,
    message: str,
) -> None:
    path = tmp_path / name
    path.write_bytes(b" \n\t")
    parser = build_default_registry().get(document_type)

    with pytest.raises(NoUsableContentError, match=message):
        await parser.parse(
            ResolvedSource(path=path, original_name=path.name, owned=False),
            options=ParseOptions(),
        )


@pytest.mark.asyncio
async def test_text_parser_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe")
    parser = build_default_registry().get(DocumentType.TEXT)

    with pytest.raises(CorruptDocumentError, match="UTF-8"):
        await parser.parse(
            ResolvedSource(path=path, original_name=path.name, owned=False),
            options=ParseOptions(),
        )


@pytest.mark.asyncio
async def test_text_parser_rejects_oversized_input_after_reading_at_most_limit_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opendocs.parsers import text as text_module

    parser = build_default_registry().get(DocumentType.TEXT)
    path = tmp_path / "huge.txt"
    path.write_bytes(b"placeholder")
    source = ResolvedSource(path=path, original_name=path.name, owned=False)
    monkeypatch.setattr(text_module, "_MAX_TEXT_BYTES", 5)

    read_sizes: list[int] = []
    real_open = Path.open

    class _FakeHandle:
        def __enter__(self) -> _FakeHandle:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return b"abcdef"

    def fake_open(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if self == path and mode == "rb":
            return _FakeHandle()
        return real_open(
            self,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "open", fake_open)

    with pytest.raises(LimitExceededError, match="5 bytes"):
        await parser.parse(source, options=ParseOptions())

    assert read_sizes == [6]


def test_text_parser_rejects_unsupported_document_type() -> None:
    from opendocs.parsers.text import TextParser

    with pytest.raises(ValueError, match="text and markdown"):
        TextParser(DocumentType.PDF)


def test_text_parser_rejects_non_document_type() -> None:
    from opendocs.parsers.text import TextParser

    with pytest.raises(TypeError, match="DocumentType"):
        TextParser(cast(Any, "text"))


def test_default_registry_registers_exactly_text_and_markdown() -> None:
    registry = build_default_registry()

    assert registry.get(DocumentType.TEXT)
    assert registry.get(DocumentType.MARKDOWN)

    for unsupported_type in (
        DocumentType.PDF,
        DocumentType.IMAGE,
        DocumentType.DOCX,
        DocumentType.PPTX,
    ):
        with pytest.raises(UnsupportedDocumentError, match=unsupported_type.value):
            registry.get(unsupported_type)
