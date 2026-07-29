from __future__ import annotations

import re
from dataclasses import dataclass

from opendocs._models import (
    BBox,
    Block,
    PageBreakBlock,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.parsers.pdf.extract import intersection_area
from opendocs.parsers.pdf.models import (
    NativeCandidate,
    NativeTableCandidate,
    NativeTextCandidate,
    PageFacts,
    PageRoute,
)
from opendocs.vision.base import VisionElement, VisionTableElement, VisionTextElement

PAGE_NORMALIZED_V1 = "page-normalized-v1"
CROP_NORMALIZED_V1 = "crop-normalized-v1"


@dataclass(frozen=True, slots=True)
class PageVisionResult:
    page_number: int
    route: PageRoute
    elements: tuple[VisionElement, ...]
    coordinate_space: str | None
    warnings: tuple[WarningRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class PdfMergeResult:
    blocks: tuple[Block, ...]
    warnings: tuple[WarningRecord, ...]


def _visual_bbox(element: VisionElement, *, required: bool) -> BBox | None:
    bbox = element.bbox
    if bbox is None:
        if required:
            raise ValueError("hybrid visual elements require an explicit bbox")
        return None
    return bbox.require_normalized("visual bbox")


def _intersects(left: BBox, right: BBox) -> bool:
    return intersection_area(left, right) > 0


def _native_tables_own_text(candidates: tuple[NativeCandidate, ...]) -> tuple[NativeCandidate, ...]:
    table_boxes = tuple(
        candidate.bbox for candidate in candidates if isinstance(candidate, NativeTableCandidate)
    )
    return tuple(
        candidate
        for candidate in candidates
        if not (
            isinstance(candidate, NativeTextCandidate)
            and any(_intersects(candidate.bbox, table_bbox) for table_bbox in table_boxes)
        )
    )


def _visual_tables_own_text(elements: tuple[VisionElement, ...]) -> tuple[VisionElement, ...]:
    table_boxes = tuple(
        element.bbox
        for element in elements
        if isinstance(element, VisionTableElement) and element.bbox is not None
    )
    return tuple(
        element
        for element in elements
        if not (
            isinstance(element, VisionTextElement)
            and element.bbox is not None
            and any(_intersects(element.bbox, table_bbox) for table_bbox in table_boxes)
        )
    )


def _candidate_key(candidate: NativeCandidate) -> tuple[float, float, int, int]:
    return (
        candidate.bbox.top,
        candidate.bbox.left,
        candidate.source_index,
        0 if isinstance(candidate, NativeTableCandidate) else 1,
    )


def _element_key(element: VisionElement) -> tuple[float, float, int, int]:
    bbox = element.bbox
    return (
        bbox.top if bbox is not None else 0.0,
        bbox.left if bbox is not None else 0.0,
        element.source_index,
        0 if isinstance(element, VisionTableElement) else 1,
    )


def _native_block(candidate: NativeCandidate) -> Block:
    if isinstance(candidate, NativeTableCandidate):
        return TableBlock(candidate.grid, candidate.header_rows)
    return TextBlock(candidate.text)


def _visual_block(element: VisionElement) -> Block:
    if isinstance(element, VisionTableElement):
        return TableBlock(element.grid, element.header_rows)
    return TextBlock(element.text)


def _page_blocks(page: PageFacts, visual: PageVisionResult | None) -> tuple[Block, ...]:
    native_candidates = _native_tables_own_text(page.native_candidates)
    if visual is None:
        return tuple(
            _native_block(candidate) for candidate in sorted(native_candidates, key=_candidate_key)
        )

    elements = _visual_tables_own_text(visual.elements)
    if visual.route is PageRoute.FULL_VISION:
        return tuple(_visual_block(element) for element in sorted(elements, key=_element_key))
    if visual.route is not PageRoute.HYBRID:
        return tuple(
            _native_block(candidate) for candidate in sorted(native_candidates, key=_candidate_key)
        )
    if visual.coordinate_space != PAGE_NORMALIZED_V1:
        raise ValueError("hybrid merge requires page-normalized-v1 visual coordinates")

    visual_boxes = tuple(_visual_bbox(element, required=True) for element in elements)
    native = tuple(
        candidate
        for candidate in native_candidates
        if not any(
            visual_bbox is not None and _intersects(candidate.bbox, visual_bbox)
            for visual_bbox in visual_boxes
        )
    )
    candidates: list[tuple[float, float, int, int, Block]] = [
        (*_candidate_key(candidate), _native_block(candidate)) for candidate in native
    ]
    candidates.extend((*_element_key(element), _visual_block(element)) for element in elements)
    return tuple(item[-1] for item in sorted(candidates, key=lambda item: item[:-1]))


_PAGE_WARNING = re.compile(r"^PDF page (\d+):")


def _warning_key(warning: WarningRecord) -> tuple[str, int, str]:
    match = _PAGE_WARNING.match(warning.message)
    return warning.code, int(match.group(1)) if match else 0, warning.message


def merge_pdf_pages(
    pages: tuple[PageFacts, ...],
    visual_results: tuple[PageVisionResult, ...],
    document_warnings: tuple[WarningRecord, ...] = (),
) -> PdfMergeResult:
    page_slots = {page.page_number: page for page in pages}
    if len(page_slots) != len(pages) or any(number <= 0 for number in page_slots):
        raise ValueError("PDF pages must have unique positive page numbers")
    visual_slots = {result.page_number: result for result in visual_results}
    if len(visual_slots) != len(visual_results) or not set(visual_slots) <= set(page_slots):
        raise ValueError("PDF visual results must map to unique analyzed pages")

    blocks: list[Block] = []
    warnings = list(document_warnings)
    for page_number in sorted(page_slots):
        page = page_slots[page_number]
        visual = visual_slots.get(page_number)
        blocks.append(PageBreakBlock(page_number))
        blocks.extend(_page_blocks(page, visual))
        if visual is not None:
            warnings.extend(visual.warnings)
    warnings.sort(key=_warning_key)
    return PdfMergeResult(tuple(blocks), tuple(warnings))
