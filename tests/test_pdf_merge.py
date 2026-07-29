from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox, PageBreakBlock, TableBlock, TextBlock, WarningRecord
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
