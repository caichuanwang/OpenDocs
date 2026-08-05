from __future__ import annotations

import re
from dataclasses import dataclass

from opendocs._models import BBox, Block, PageBreakBlock, TableBlock, TextBlock, WarningRecord
from opendocs.parsers.pdf.extract import bbox_contains_center, build_native_text_lines
from opendocs.parsers.pdf.models import (
    NativeCandidate,
    NativeTableCandidate,
    NativeTextCandidate,
    PageFacts,
    PageRoute,
)
from opendocs.parsers.pdf.semantics import body_font_size, edge_suppressions, structured_native_run
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


def _native_tables_own_text(candidates: tuple[NativeCandidate, ...]) -> tuple[NativeCandidate, ...]:
    table_boxes = tuple(
        candidate.bbox for candidate in candidates if isinstance(candidate, NativeTableCandidate)
    )
    return tuple(
        candidate
        for candidate in candidates
        if not (
            isinstance(candidate, NativeTextCandidate)
            and any(bbox_contains_center(table_bbox, candidate.bbox) for table_bbox in table_boxes)
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
            and any(bbox_contains_center(table_bbox, element.bbox) for table_bbox in table_boxes)
        )
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


def _item_bbox(item: NativeCandidate | VisionElement) -> BBox | None:
    return item.bbox


def _layout_columns(
    items: list[NativeCandidate | VisionElement],
) -> list[list[int]] | None:
    columns: list[tuple[float, float, list[int]]] = []
    for index, item in enumerate(items):
        bbox = _item_bbox(item)
        if bbox is None or bbox.right - bbox.left >= 0.70:
            continue
        for column_index, (left, right, indexes) in enumerate(columns):
            overlap = max(0.0, min(right, bbox.right) - max(left, bbox.left))
            narrower = min(right - left, bbox.right - bbox.left)
            if overlap / max(narrower, 0.001) >= 0.20:
                indexes.append(index)
                columns[column_index] = (min(left, bbox.left), max(right, bbox.right), indexes)
                break
        else:
            columns.append((bbox.left, bbox.right, [index]))
            if len(columns) > 2:
                return None
    columns.sort(key=lambda column: column[0])
    if (
        len(columns) != 2
        or sum(len(column[2]) for column in columns) < 3
        or columns[1][0] - columns[0][1] < 0.12
    ):
        return None
    return [column[2] for column in columns]


def _insert_visual_item(
    items: list[NativeCandidate | VisionElement],
    element: VisionElement,
    bbox: BBox,
) -> list[NativeCandidate | VisionElement]:
    if bbox.right - bbox.left >= 0.70:
        before = [
            item
            for item in items
            if (item_bbox := _item_bbox(item)) is not None and item_bbox.bottom <= bbox.top
        ]
        after = [item for item in items if item not in before]
        return [*before, element, *after]

    columns = _layout_columns(items)
    if columns is not None:
        overlaps = []
        for indexes in columns:
            column_boxes = [
                item_bbox
                for index in indexes
                if (item_bbox := _item_bbox(items[index])) is not None
            ]
            left = min(box.left for box in column_boxes)
            right = max(box.right for box in column_boxes)
            overlaps.append(max(0.0, min(right, bbox.right) - max(left, bbox.left)))
        target = max(range(len(columns)), key=overlaps.__getitem__)
        if overlaps[target] > 0:
            target_indexes = columns[target]
            insert_at = next(
                (
                    index
                    for index in target_indexes
                    if (item_bbox := _item_bbox(items[index])) is not None
                    and item_bbox.top >= bbox.top
                ),
                target_indexes[-1] + 1,
            )
            items.insert(insert_at, element)
            return items

    insert_at = next(
        (
            index
            for index, item in enumerate(items)
            if (item_bbox := _item_bbox(item)) is not None and item_bbox.top >= bbox.top
        ),
        len(items),
    )
    items.insert(insert_at, element)
    return items


def _page_blocks(
    page: PageFacts,
    visual: PageVisionResult | None,
    *,
    suppressed: set[tuple[int, int]],
    body_size: float | None,
) -> tuple[Block, ...]:
    native_candidates = tuple(
        candidate
        for candidate in _native_tables_own_text(page.native_candidates)
        if not (
            isinstance(candidate, NativeTextCandidate)
            and (page.page_number, candidate.source_index) in suppressed
        )
    )
    if visual is not None and visual.route is PageRoute.FULL_VISION:
        elements = _visual_tables_own_text(visual.elements)
        return tuple(_visual_block(element) for element in sorted(elements, key=_element_key))

    items: list[NativeCandidate | VisionElement] = list(native_candidates)
    if visual is not None and visual.route is PageRoute.HYBRID:
        if visual.coordinate_space != PAGE_NORMALIZED_V1:
            raise ValueError("hybrid merge requires page-normalized-v1 visual coordinates")
        elements = _visual_tables_own_text(visual.elements)
        visual_boxes = tuple(_visual_bbox(element, required=True) for element in elements)
        items = [
            candidate
            for candidate in items
            if not (
                isinstance(candidate, NativeTextCandidate)
                and any(
                    visual_bbox is not None and bbox_contains_center(visual_bbox, candidate.bbox)
                    for visual_bbox in visual_boxes
                )
            )
        ]
        for element in sorted(elements, key=_element_key):
            visual_bbox = _visual_bbox(element, required=True)
            if visual_bbox is None:
                raise AssertionError("required visual bbox disappeared")
            items = _insert_visual_item(items, element, visual_bbox)

    blocks: list[Block] = []
    native_run: list[NativeTextCandidate] = []

    def flush_native() -> None:
        if not native_run:
            return
        blocks.extend(
            structured_native_run(
                list(build_native_text_lines(native_run)),
                body_size=body_size,
            )
        )
        native_run.clear()

    for item in items:
        if isinstance(item, NativeTextCandidate):
            native_run.append(item)
            continue
        flush_native()
        blocks.append(
            _native_block(item) if isinstance(item, NativeTableCandidate) else _visual_block(item)
        )
    flush_native()
    return tuple(blocks)


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

    ordered_pages = tuple(page_slots[number] for number in sorted(page_slots))
    suppressed = edge_suppressions(ordered_pages)
    body_size = body_font_size(ordered_pages, suppressed)
    blocks: list[Block] = []
    warnings = list(document_warnings)
    for page in ordered_pages:
        visual = visual_slots.get(page.page_number)
        blocks.append(PageBreakBlock(page.page_number))
        blocks.extend(
            _page_blocks(
                page,
                visual,
                suppressed=suppressed,
                body_size=body_size,
            )
        )
        if visual is not None:
            warnings.extend(visual.warnings)
    warnings.sort(key=_warning_key)
    return PdfMergeResult(tuple(blocks), tuple(warnings))
