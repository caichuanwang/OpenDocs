from __future__ import annotations

from typing import Any, cast

import pytest

from opendocs import LimitExceededError, NoUsableContentError
from opendocs._models import (
    DocumentType,
    MarkdownBlock,
    ParsedDocument,
    RenderResult,
    TextBlock,
    WarningRecord,
)
from opendocs.markdown import render_markdown


def test_render_markdown_preserves_markdown_blocks_and_escapes_text_blocks() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(
            MarkdownBlock("\n# Heading\n"),
            TextBlock("\\ ` * _ [ ] <>\n"),
        ),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert result == RenderResult(markdown="\n# Heading\n\n\\\\ \\` \\* \\_ \\[ \\] \\<\\>\n")


def test_render_markdown_escapes_block_markers_without_changing_canonical_joining() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(
            TextBlock("# heading\n"),
            TextBlock("   > quote\n"),
            TextBlock("- bullet\n"),
            TextBlock("2. ordered\n"),
        ),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert result.markdown == "\\# heading\n\n   \\> quote\n\n\\- bullet\n\n\\2. ordered\n"
    assert result.markdown.count("\\# heading") == 1


def test_render_markdown_escapes_hash_and_quote_markers_without_following_space() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock("#foo\n>foo\n"),),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert result.markdown == "\\#foo\n\\>foo\n"


def test_render_markdown_truncates_only_at_block_boundaries_and_retains_existing_warnings() -> None:
    warning = WarningRecord(code="upstream", message="kept")
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(
            TextBlock("alpha"),
            TextBlock("beta"),
            TextBlock("gamma"),
        ),
        warnings=(warning,),
    )

    result = render_markdown(document, max_output_chars=12)

    assert result.markdown == "alpha\n\nbeta\n"
    assert result.warnings == (
        warning,
        WarningRecord(
            code="output_truncated",
            message="output stopped before block 3",
        ),
    )


def test_render_markdown_raises_when_first_non_empty_block_does_not_fit() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock("alpha"), TextBlock("beta")),
    )

    with pytest.raises(
        LimitExceededError,
        match="no complete block fits within max_output_chars",
    ):
        render_markdown(document, max_output_chars=3)


def test_render_markdown_raises_when_all_blocks_are_empty() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock(""), MarkdownBlock("\n")),
    )

    with pytest.raises(
        NoUsableContentError,
        match="document produced no usable content",
    ):
        render_markdown(document, max_output_chars=400_000)


def test_render_markdown_requires_max_output_chars_keyword_argument() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock("alpha"),),
    )

    with pytest.raises(TypeError, match="max_output_chars"):
        cast(Any, render_markdown)(document)
