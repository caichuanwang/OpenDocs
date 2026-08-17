from __future__ import annotations

from opendocs._models import (
    HeadingBlock,
    InlineText,
    MarkdownBlock,
    ParagraphBlock,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.parsers.xlsx.merge import XlsxVisualOutcome, merge_xlsx_document
from opendocs.parsers.xlsx.models import (
    XlsxChartSlot,
    XlsxDocument,
    XlsxImageSlot,
    XlsxNativeSlot,
    XlsxSheet,
    XlsxSheetKind,
    XlsxSheetState,
)
from opendocs.vision.base import VisionResult, VisionTextElement


def _prelude(sheet_index: int, name: str) -> XlsxNativeSlot:
    return XlsxNativeSlot(
        0,
        "A1",
        (
            MarkdownBlock(f"<!-- xlsx-sheet: {sheet_index} -->"),
            HeadingBlock(1, (InlineText(name),)),
            ParagraphBlock((InlineText("legacy state"),)),
        ),
    )


def test_merge_orders_by_anchor_and_keeps_all_native_facts_before_same_anchor_vision() -> None:
    chart_digest = "a" * 64
    image_digest = "b" * 64
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "Data",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.HIDDEN,
                (
                    _prelude(1, "Data"),
                    XlsxNativeSlot(1, "A10", (TextBlock("late"),)),
                    XlsxChartSlot(
                        3,
                        "B2",
                        "chart.png",
                        chart_digest,
                        (TextBlock("native chart facts"),),
                    ),
                    XlsxImageSlot(
                        4,
                        "B2",
                        "image.png",
                        image_digest,
                        alt_text="Quarterly dashboard",
                        object_name="Picture 1",
                    ),
                    XlsxNativeSlot(2, "A2", (TextBlock("early"),)),
                ),
            ),
            XlsxSheet(
                2,
                "Empty",
                XlsxSheetKind.CHARTSHEET,
                XlsxSheetState.VERY_HIDDEN,
                (_prelude(2, "Empty"),),
            ),
        ),
        (WarningRecord("xlsx_unsupported_object", "Data!C4: unsupported control"),),
    )
    outcomes = {
        chart_digest: XlsxVisualOutcome(
            VisionResult((VisionTextElement("chart interpretation", 0),))
        ),
        image_digest: XlsxVisualOutcome(
            VisionResult((VisionTextElement("image interpretation", 0),))
        ),
    }

    merged = merge_xlsx_document(document, outcomes)

    assert merged.blocks[0:2] == (
        MarkdownBlock("<!-- xlsx-sheet: 1 -->"),
        HeadingBlock(1, (InlineText("Data (Hidden)"),)),
    )
    assert merged.blocks[-2:] == (
        MarkdownBlock("<!-- xlsx-sheet: 2 -->"),
        HeadingBlock(1, (InlineText("Empty (Very Hidden)"),)),
    )
    rendered_text = [block.text for block in merged.blocks if isinstance(block, TextBlock)]
    assert rendered_text == (
        [
            "early",
            "native chart facts",
            "chart interpretation",
            "image interpretation",
            "late",
        ]
    )
    chart_native = merged.blocks.index(TextBlock("native chart facts"))
    image_metadata = merged.blocks.index(
        ParagraphBlock((InlineText("Image description: Quarterly dashboard"),))
    )
    chart_visual = merged.blocks.index(TextBlock("chart interpretation"))
    image_visual = merged.blocks.index(TextBlock("image interpretation"))
    assert chart_native < chart_visual
    assert image_metadata < chart_visual
    assert chart_visual < image_visual
    assert merged.warnings == document.warnings


def test_merge_replays_digest_failure_for_every_occurrence_without_losing_metadata() -> None:
    digest = "c" * 64
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "One",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    _prelude(1, "One"),
                    XlsxImageSlot(1, "C3", "same.png", digest, title="Logo"),
                ),
            ),
            XlsxSheet(
                2,
                "Two",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    _prelude(2, "Two"),
                    XlsxImageSlot(1, "D4", "same.png", digest, alt_text="Brand"),
                ),
            ),
        )
    )

    merged = merge_xlsx_document(document, {digest: XlsxVisualOutcome(None, "xlsx_vision_failed")})

    assert ParagraphBlock((InlineText("Image title: Logo"),)) in merged.blocks
    assert ParagraphBlock((InlineText("Image description: Brand"),)) in merged.blocks
    assert [warning.code for warning in merged.warnings] == [
        "xlsx_vision_failed",
        "xlsx_vision_failed",
    ]
    assert "One!C3" in merged.warnings[0].message
    assert "Two!D4" in merged.warnings[1].message


def test_merge_keeps_valid_vision_tables_after_native_chart_data() -> None:
    digest = "d" * 64
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "Chart",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    _prelude(1, "Chart"),
                    XlsxChartSlot(
                        1,
                        "A1",
                        "chart.png",
                        digest,
                        (TableBlock((("native", "1"),), 0),),
                    ),
                ),
            ),
        )
    )
    outcome = XlsxVisualOutcome(
        VisionResult((VisionTextElement("visual trend", 0),)),
    )

    merged = merge_xlsx_document(document, {digest: outcome})

    assert merged.blocks.index(TableBlock((("native", "1"),), 0)) < merged.blocks.index(
        TextBlock("visual trend")
    )


def test_merge_returns_headings_and_warnings_for_unsupported_only_workbook() -> None:
    warning = WarningRecord("xlsx_unsupported_object", "Only!A1: unsupported object")
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "Only",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (_prelude(1, "Only"),),
            ),
        ),
        (warning,),
    )

    merged = merge_xlsx_document(document, {})

    assert merged.blocks == (
        MarkdownBlock("<!-- xlsx-sheet: 1 -->"),
        HeadingBlock(1, (InlineText("Only (Visible)"),)),
    )
    assert merged.warnings == (warning,)
