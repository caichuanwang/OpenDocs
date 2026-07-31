from __future__ import annotations

from opendocs._models import (
    BBox,
    DocumentType,
    HardPageBreakBlock,
    InlineText,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.parsers.office.merge import (
    OfficeVisualOutcome,
    has_semantic_office_content,
    merge_office_document,
)
from opendocs.parsers.office.models import (
    BreakSlot,
    ImageSlot,
    NativeSlot,
    OfficeDocument,
    OfficePage,
)
from opendocs.vision.base import VisionResult, VisionTableElement, VisionTextElement


def _image(source_index: int, digest: str = "a" * 64) -> ImageSlot:
    return ImageSlot(source_index, f"media-{source_index}.png", digest, BBox(0, 0, 1, 1))


def test_merge_office_document_replays_visual_results_at_every_source_slot() -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (
            OfficePage(
                1,
                (
                    NativeSlot(0, (TextBlock("before"),)),
                    _image(1),
                    NativeSlot(2, (TextBlock("middle"),)),
                    _image(3),
                    BreakSlot(4),
                    NativeSlot(5, (TextBlock("after"),)),
                ),
            ),
        ),
    )
    outcome = OfficeVisualOutcome(
        VisionResult(
            (
                VisionTextElement("second", 2),
                VisionTableElement((("A", "B"), ("1", "2")), 1, 1),
            )
        )
    )

    merged = merge_office_document(document, {"a" * 64: outcome})

    assert merged.blocks == (
        TextBlock("before"),
        TableBlock((("A", "B"), ("1", "2")), 1),
        TextBlock("second"),
        TextBlock("middle"),
        TableBlock((("A", "B"), ("1", "2")), 1),
        TextBlock("second"),
        HardPageBreakBlock(),
        TextBlock("after"),
    )


def test_merge_pptx_keeps_every_slide_boundary_and_source_order() -> None:
    document = OfficeDocument(
        DocumentType.PPTX,
        (
            OfficePage(1, (NativeSlot(8, (TextBlock("later index"),)),)),
            OfficePage(2, ()),
            OfficePage(
                3,
                (
                    NativeSlot(2, (TextBlock("second"),)),
                    NativeSlot(1, (TextBlock("first"),)),
                ),
            ),
        ),
    )

    merged = merge_office_document(document, {})

    assert merged.blocks == (
        PageBreakBlock(1),
        TextBlock("later index"),
        PageBreakBlock(2),
        PageBreakBlock(3),
        TextBlock("first"),
        TextBlock("second"),
    )


def test_merge_replays_location_stable_warning_for_duplicate_images() -> None:
    document = OfficeDocument(
        DocumentType.PPTX,
        (OfficePage(1, (_image(0), _image(4))),),
    )

    merged = merge_office_document(
        document,
        {"a" * 64: OfficeVisualOutcome(None, "vision_unavailable_native_only")},
    )

    assert [warning.code for warning in merged.warnings] == [
        "vision_unavailable_native_only",
        "vision_unavailable_native_only",
    ]
    assert [warning.message for warning in merged.warnings] == [
        "PPTX page 1 source 0: vision unavailable native only",
        "PPTX page 1 source 4: vision unavailable native only",
    ]


def test_office_semantic_content_ignores_only_structural_breaks() -> None:
    structural = merge_office_document(
        OfficeDocument(DocumentType.PPTX, (OfficePage(1, ()),)),
        {},
    )
    text = merge_office_document(
        OfficeDocument(
            DocumentType.DOCX,
            (OfficePage(1, (NativeSlot(0, (TextBlock("content"),)),)),),
        ),
        {},
    )

    assert has_semantic_office_content(structural) is False
    assert has_semantic_office_content(text) is True


def test_office_semantic_content_accepts_structured_blocks() -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (
            OfficePage(
                1,
                (
                    NativeSlot(
                        0,
                        (ParagraphBlock((InlineText("content"),)),),
                    ),
                ),
            ),
        ),
    )

    # Model validation is tested elsewhere; this assertion documents that any structured
    # semantic block counts without requiring the merge layer to inspect its inline shape.
    assert has_semantic_office_content(merge_office_document(document, {})) is True


def test_merge_bounds_repeated_warning_codes_deterministically() -> None:
    warnings = tuple(
        WarningRecord(
            code="pptx_unsupported_shape",
            message=f"PPTX page 1 source {source_index}: unsupported shape",
        )
        for source_index in range(50)
    )
    document = OfficeDocument(
        DocumentType.PPTX,
        (OfficePage(1, (NativeSlot(0, (TextBlock("content"),)),)),),
        warnings,
    )

    first = merge_office_document(document, {})
    second = merge_office_document(document, {})

    assert first.warnings == second.warnings
    assert [warning.code for warning in first.warnings] == [
        *(["pptx_unsupported_shape"] * 20),
        "office_warnings_truncated",
    ]
    assert first.warnings[-1].message == (
        "suppressed 30 additional pptx_unsupported_shape warnings"
    )
