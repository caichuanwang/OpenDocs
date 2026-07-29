from __future__ import annotations

from typing import Any, cast

import pytest

from opendocs import LimitExceededError, NoUsableContentError
from opendocs._models import (
    DocumentType,
    MarkdownBlock,
    PageBreakBlock,
    ParsedDocument,
    RenderResult,
    TableBlock,
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
            TextBlock("10) next\n"),
        ),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert (
        result.markdown
        == "\\# heading\n\n   \\> quote\n\n\\- bullet\n\n2\\. ordered\n\n10\\) next\n"
    )
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


def test_render_markdown_renders_page_break_and_one_header_table() -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(
            PageBreakBlock(1),
            TableBlock(
                grid=(("A|B", "line 1\nline 2"), ("x\\y", None)),
                header_rows=1,
            ),
        ),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert result.markdown == (
        "<!-- page: 1 -->\n\n| A\\|B | line 1<br>line 2 |\n| --- | --- |\n| x\\\\y |  |\n"
    )


def test_render_markdown_renders_zero_header_table_as_html_without_thead() -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(TableBlock((("<x>", '"quoted" & value'),), header_rows=0),),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert result.markdown == (
        "<table>\n<tbody>\n<tr><td>&lt;x&gt;</td>"
        "<td>&quot;quoted&quot; &amp; value</td></tr>\n</tbody>\n</table>\n"
    )


def test_render_markdown_renders_multiple_header_rows_as_html() -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(
            TableBlock(
                (("A", "B"), ("C", "D"), ("1", "2")),
                header_rows=2,
            ),
        ),
    )

    result = render_markdown(document, max_output_chars=400_000)

    assert result.markdown == (
        "<table>\n<thead>\n<tr><th>A</th><th>B</th></tr>\n"
        "<tr><th>C</th><th>D</th></tr>\n</thead>\n<tbody>\n"
        "<tr><td>1</td><td>2</td></tr>\n</tbody>\n</table>\n"
    )


@pytest.mark.parametrize("max_output_chars", [1, 5, 400_000])
def test_structural_page_blocks_do_not_make_document_semantically_usable(
    max_output_chars: int,
) -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(PageBreakBlock(1), PageBreakBlock(2)),
    )

    with pytest.raises(NoUsableContentError, match="no usable content"):
        render_markdown(document, max_output_chars=max_output_chars)


def test_empty_table_and_page_are_never_reported_as_output_limit() -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(PageBreakBlock(1), TableBlock((("", None),), header_rows=0)),
    )

    with pytest.raises(NoUsableContentError, match="no usable content"):
        render_markdown(document, max_output_chars=1)


def test_structural_page_block_is_retained_when_semantic_content_exists() -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(PageBreakBlock(1), TextBlock("hello")),
    )

    assert render_markdown(document, max_output_chars=400_000).markdown == (
        "<!-- page: 1 -->\n\nhello\n"
    )


def test_render_markdown_requires_max_output_chars_keyword_argument() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock("alpha"),),
    )

    with pytest.raises(TypeError, match="max_output_chars"):
        cast(Any, render_markdown)(document)
