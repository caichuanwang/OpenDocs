from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from opendocs._models import (
    BBox,
    DocumentType,
    HeadingBlock,
    InlineText,
    ListItemBlock,
    ListKind,
    MarkdownBlock,
    PageBreakBlock,
    ParagraphBlock,
    ParsedDocument,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.markdown import render_markdown
from opendocs.parsers.pdf.extract import measure_text_quality
from opendocs.parsers.pdf.merge import PAGE_NORMALIZED_V1, PageVisionResult, merge_pdf_pages
from opendocs.parsers.pdf.models import (
    NativeTableCandidate,
    NativeTextCandidate,
    PageFacts,
    PageRoute,
)
from opendocs.vision.base import VisionTableElement, VisionTextElement


def _page(page_number: int, candidates=()) -> PageFacts:
    box = BBox(0.0, 0.0, 100.0, 100.0)
    return PageFacts(
        page_number,
        box,
        box,
        0,
        100.0,
        100.0,
        (),
        (),
        tuple(candidates),
        (),
        measure_text_quality("native" if candidates else ""),
        0.0,
        0,
        False,
        False,
        True,
    )


def _table(bbox: BBox, index: int = 0) -> NativeTableCandidate:
    return NativeTableCandidate(
        bbox,
        (("A", "B"), ("1", "2")),
        1,
        index,
        1.0,
        0.0,
        1.0,
    )


def test_hybrid_visual_ownership_suppresses_native_and_table_owns_visual_text() -> None:
    native = (
        NativeTextCandidate("before", BBox(0.1, 0.1, 0.2, 0.2), 0),
        NativeTextCandidate("duplicate", BBox(0.3, 0.3, 0.4, 0.4), 1),
        NativeTextCandidate("after", BBox(0.8, 0.8, 0.9, 0.9), 2),
    )
    visual = PageVisionResult(
        1,
        PageRoute.HYBRID,
        (
            VisionTextElement("table duplicate", 1, BBox(0.31, 0.31, 0.39, 0.39)),
            VisionTableElement(
                (("A", "B"), ("3", "4")),
                1,
                0,
                BBox(0.25, 0.25, 0.5, 0.5),
            ),
        ),
        PAGE_NORMALIZED_V1,
    )

    merged = merge_pdf_pages((_page(1, native),), (visual,))

    assert merged.blocks == (
        PageBreakBlock(1),
        TextBlock("before"),
        TableBlock((("A", "B"), ("3", "4")), 1),
        TextBlock("after"),
    )


def test_full_vision_is_sole_owner_and_pages_are_source_ordered() -> None:
    page_one = _page(1, (NativeTextCandidate("native", BBox(0.1, 0.1, 0.2, 0.2), 0),))
    page_two = _page(2, (NativeTextCandidate("two", BBox(0.1, 0.1, 0.2, 0.2), 0),))
    visual = PageVisionResult(
        1,
        PageRoute.FULL_VISION,
        (VisionTextElement("vision", 0),),
        None,
    )

    merged = merge_pdf_pages((page_two, page_one), (visual,))

    assert merged.blocks == (
        PageBreakBlock(1),
        TextBlock("vision"),
        PageBreakBlock(2),
        TextBlock("two"),
    )


def test_native_table_is_rendered_once_and_owns_intersecting_text() -> None:
    table = _table(BBox(0.2, 0.2, 0.6, 0.6))
    duplicate = NativeTextCandidate("duplicate", BBox(0.3, 0.3, 0.4, 0.4), 1)
    merged = merge_pdf_pages(
        (_page(1, (table, duplicate)),),
        (),
        (
            WarningRecord("z_warning", "last"),
            WarningRecord("a_warning", "first"),
        ),
    )

    assert sum(isinstance(block, TableBlock) for block in merged.blocks) == 1
    assert TextBlock("duplicate") not in merged.blocks
    assert [warning.code for warning in merged.warnings] == ["a_warning", "z_warning"]


def test_native_table_only_owns_text_whose_center_is_inside() -> None:
    table = _table(BBox(0.2, 0.2, 0.6, 0.6))
    touching = NativeTextCandidate("touching", BBox(0.1, 0.1, 0.22, 0.18), 0)

    merged = merge_pdf_pages((_page(1, (touching, table)),), ())

    assert TextBlock("touching") in merged.blocks


def test_hybrid_region_only_owns_native_text_whose_center_is_inside() -> None:
    touching = NativeTextCandidate("touching", BBox(0.1, 0.1, 0.32, 0.18), 0)
    visual = PageVisionResult(
        1,
        PageRoute.HYBRID,
        (VisionTextElement("visual", 0, BBox(0.30, 0.15, 0.50, 0.30)),),
        PAGE_NORMALIZED_V1,
    )

    merged = merge_pdf_pages((_page(1, (touching,)),), (visual,))

    assert TextBlock("touching") in merged.blocks
    assert TextBlock("visual") in merged.blocks


def test_hybrid_region_replaces_only_owned_word_not_entire_line() -> None:
    page = _page(
        1,
        (
            NativeTextCandidate("native", BBox(0.1, 0.1, 0.25, 0.15), 0),
            NativeTextCandidate("figure", BBox(0.3, 0.1, 0.45, 0.15), 1),
        ),
    )
    visual = PageVisionResult(
        1,
        PageRoute.HYBRID,
        (VisionTextElement("diagram", 0, BBox(0.28, 0.08, 0.48, 0.20)),),
        PAGE_NORMALIZED_V1,
    )

    merged = merge_pdf_pages((page,), (visual,))

    assert TextBlock("native") in merged.blocks
    assert TextBlock("diagram") in merged.blocks
    assert TextBlock("native figure") not in merged.blocks


def test_hybrid_rejects_missing_or_wrong_coordinate_space() -> None:
    page = _page(1)
    missing = PageVisionResult(
        1,
        PageRoute.HYBRID,
        (VisionTextElement("bad", 0),),
        PAGE_NORMALIZED_V1,
    )
    wrong = PageVisionResult(
        1,
        PageRoute.HYBRID,
        (VisionTextElement("bad", 0, BBox(0.1, 0.1, 0.2, 0.2)),),
        "crop-normalized-v1",
    )

    with pytest.raises(ValueError, match="explicit bbox"):
        merge_pdf_pages((page,), (missing,))
    with pytest.raises(ValueError, match="page-normalized-v1"):
        merge_pdf_pages((page,), (wrong,))


def test_native_pages_filter_repeated_margins_and_structure_markdown() -> None:
    pages = (
        _page(
            1,
            (
                NativeTextCandidate("OpenDocs Manual", BBox(0.1, 0.01, 0.9, 0.04), 0, 9.0),
                NativeTextCandidate("PDF Pipeline", BBox(0.1, 0.1, 0.6, 0.16), 1, 20.0),
                NativeTextCandidate("• Fast native path", BBox(0.1, 0.22, 0.7, 0.27), 2, 11.0),
                NativeTextCandidate("hyphen-", BBox(0.1, 0.35, 0.3, 0.4), 3, 11.0),
                NativeTextCandidate("ation works.", BBox(0.1, 0.41, 0.5, 0.46), 4, 11.0),
                NativeTextCandidate("1", BBox(0.48, 0.96, 0.52, 0.99), 5, 9.0),
            ),
        ),
        _page(
            2,
            (
                NativeTextCandidate("OpenDocs Manual", BBox(0.1, 0.01, 0.9, 0.04), 0, 9.0),
                NativeTextCandidate("Second page", BBox(0.1, 0.2, 0.5, 0.25), 1, 11.0),
                NativeTextCandidate("2", BBox(0.48, 0.96, 0.52, 0.99), 2, 9.0),
            ),
        ),
        _page(
            3,
            (
                NativeTextCandidate("OpenDocs Manual", BBox(0.1, 0.01, 0.9, 0.04), 0, 9.0),
                NativeTextCandidate("Third page", BBox(0.1, 0.2, 0.5, 0.25), 1, 11.0),
                NativeTextCandidate("3", BBox(0.48, 0.96, 0.52, 0.99), 2, 9.0),
            ),
        ),
    )

    merged = merge_pdf_pages(pages, ())

    assert HeadingBlock(1, (InlineText("PDF Pipeline"),)) in merged.blocks
    assert (
        ListItemBlock(0, 0, ListKind.BULLET, 1, (InlineText("Fast native path"),)) in merged.blocks
    )
    assert ParagraphBlock((InlineText("hyphenation works."),)) in merged.blocks
    assert ParagraphBlock((InlineText("Second page"),)) in merged.blocks
    assert all(
        not (isinstance(block, TextBlock | ParagraphBlock) and "OpenDocs Manual" in str(block))
        for block in merged.blocks
    )
    assert all(
        not isinstance(block, ParagraphBlock)
        or block.inlines not in {(InlineText("1"),), (InlineText("2"),)}
        for block in merged.blocks
    )


def test_native_monospace_run_becomes_fenced_markdown() -> None:
    page = _page(
        1,
        (
            NativeTextCandidate(
                "def parse_pdf():",
                BBox(0.1, 0.2, 0.7, 0.25),
                0,
                10.0,
                "JetBrainsMono-Regular",
            ),
            NativeTextCandidate(
                "return markdown",
                BBox(0.12, 0.26, 0.7, 0.31),
                1,
                10.0,
                "JetBrainsMono-Regular",
            ),
        ),
    )

    merged = merge_pdf_pages((page,), ())

    assert MarkdownBlock("```\ndef parse_pdf():\nreturn markdown\n```") in merged.blocks


def test_native_list_run_shares_identity_and_monospace_label_stays_text() -> None:
    page = _page(
        1,
        (
            NativeTextCandidate("• first", BBox(0.1, 0.1, 0.5, 0.15), 0, 11.0),
            NativeTextCandidate("• second", BBox(0.1, 0.16, 0.5, 0.21), 1, 11.0),
            NativeTextCandidate(
                "MONOSPACE LABEL",
                BBox(0.1, 0.3, 0.5, 0.35),
                2,
                11.0,
                "Courier",
            ),
        ),
    )

    merged = merge_pdf_pages((page,), ())
    list_items = [block for block in merged.blocks if isinstance(block, ListItemBlock)]

    assert [block.list_id for block in list_items] == [0, 0]
    assert ParagraphBlock((InlineText("MONOSPACE LABEL"),)) in merged.blocks


def test_native_ordered_lists_preserve_ordinals_and_reset_identity() -> None:
    page = _page(
        1,
        (
            NativeTextCandidate("1. first", BBox(0.1, 0.1, 0.5, 0.15), 0, 11.0),
            NativeTextCandidate("2) second", BBox(0.1, 0.16, 0.5, 0.21), 1, 11.0),
            NativeTextCandidate("• bullet", BBox(0.1, 0.26, 0.5, 0.31), 2, 11.0),
        ),
    )

    merged = merge_pdf_pages((page,), ())
    items = [block for block in merged.blocks if isinstance(block, ListItemBlock)]

    assert items[0].kind is ListKind.ORDERED and items[0].ordinal == 1
    assert items[1].kind is ListKind.ORDERED and items[1].ordinal == 2
    assert items[0].list_id == items[1].list_id
    assert items[2].kind is ListKind.BULLET and items[2].list_id != items[0].list_id


def test_native_paragraph_joining_preserves_real_compound_words() -> None:
    page = _page(
        1,
        (
            NativeTextCandidate("well-", BBox(0.1, 0.1, 0.3, 0.15), 0, 11.0),
            NativeTextCandidate("known result.", BBox(0.1, 0.16, 0.5, 0.21), 1, 11.0),
            NativeTextCandidate("state-of-the-", BBox(0.1, 0.26, 0.5, 0.31), 2, 11.0),
            NativeTextCandidate("art", BBox(0.1, 0.32, 0.3, 0.37), 3, 11.0),
        ),
    )

    merged = merge_pdf_pages((page,), ())

    assert ParagraphBlock((InlineText("well-known result."),)) in merged.blocks
    assert ParagraphBlock((InlineText("state-of-the-art"),)) in merged.blocks


def test_short_document_bottom_page_numbers_are_filtered() -> None:
    pages = (
        _page(
            1,
            (
                NativeTextCandidate("Body one.", BBox(0.1, 0.3, 0.5, 0.35), 0, 11.0),
                NativeTextCandidate("1", BBox(0.48, 0.96, 0.52, 0.99), 1, 9.0),
            ),
        ),
        _page(
            2,
            (
                NativeTextCandidate("Body two.", BBox(0.1, 0.3, 0.5, 0.35), 0, 11.0),
                NativeTextCandidate("2", BBox(0.48, 0.96, 0.52, 0.99), 1, 9.0),
            ),
        ),
    )

    merged = merge_pdf_pages(pages, ())

    assert ParagraphBlock((InlineText("Body one."),)) in merged.blocks
    assert all(
        not (isinstance(block, TextBlock) and block.text.strip() in {"1", "2"})
        and not (
            isinstance(block, ParagraphBlock)
            and any(getattr(inline, "text", "").strip() in {"1", "2"} for inline in block.inlines)
        )
        for block in merged.blocks
    )


def test_hybrid_visual_element_in_right_column_keeps_column_order() -> None:
    native = (
        NativeTextCandidate("left one", BBox(0.05, 0.1, 0.35, 0.15), 0),
        NativeTextCandidate("left two", BBox(0.05, 0.3, 0.35, 0.35), 1),
        NativeTextCandidate("right one", BBox(0.65, 0.1, 0.95, 0.15), 2),
        NativeTextCandidate("right two", BBox(0.65, 0.3, 0.95, 0.35), 3),
    )
    visual = PageVisionResult(
        1,
        PageRoute.HYBRID,
        (VisionTextElement("vision", 0, BBox(0.62, 0.28, 0.98, 0.38)),),
        PAGE_NORMALIZED_V1,
    )

    merged = merge_pdf_pages((_page(1, native),), (visual,))

    assert merged.blocks == (
        PageBreakBlock(1),
        TextBlock("left one"),
        TextBlock("left two"),
        TextBlock("right one"),
        TextBlock("vision"),
    )


def test_native_semantics_render_as_clean_markdown() -> None:
    pages = (
        _page(
            1,
            (
                NativeTextCandidate("PDF Guide", BBox(0.1, 0.1, 0.6, 0.15), 0, 20.0),
                NativeTextCandidate("Body text.", BBox(0.1, 0.3, 0.7, 0.35), 1, 11.0),
            ),
        ),
        _page(
            2,
            (NativeTextCandidate("More body text.", BBox(0.1, 0.3, 0.7, 0.35), 0, 11.0),),
        ),
    )
    merged = merge_pdf_pages(pages, ())

    rendered = render_markdown(
        ParsedDocument(DocumentType.PDF, merged.blocks, merged.warnings),
        max_output_chars=10_000,
    )

    assert rendered.markdown == (
        "<!-- page: 1 -->\n\n# PDF Guide\n\nBody text.\n\n<!-- page: 2 -->\n\nMore body text.\n"
    )
