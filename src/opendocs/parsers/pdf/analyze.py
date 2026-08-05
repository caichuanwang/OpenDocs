from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfexceptions import PDFException
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

from opendocs._models import BBox, CoordinateTransform
from opendocs._runtime import ParserRuntime
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.parsers.pdf.extract import (
    bbox_contains_center,
    build_native_candidates,
    build_table_candidate,
    detect_heuristic_table,
    is_table_valid,
    is_text_reliable,
    measure_text_quality,
    select_canonical_tables,
    union_area,
)
from opendocs.parsers.pdf.models import (
    PageFacts,
    PdfAnalysis,
    PdfWord,
    VisualRegion,
    page_from_wire,
    page_to_wire,
)
from opendocs.parsers.pdf.routing import (
    MAX_VISUAL_CANDIDATES_PER_PAGE,
    VECTOR_OBJECT_COUNT_MIN,
    build_visual_regions,
    significant_image,
)

# This stays well below the native protocol's 8 MiB inline-value limit. The estimate is
# deliberately conservative because words are represented in multiple page collections.
_MAX_NATIVE_WIRE_ESTIMATE = 3 * 1024 * 1024
_MAX_NATIVE_WORDS = 10_000
_MAX_NATIVE_TEXT_CHARS = 1_000_000
_MAX_NATIVE_TABLES = 1_000
_MAX_NATIVE_TABLE_CELLS = 50_000
_MAX_READING_ORDER_COMPARISONS = 250_000
_MAX_PAGE_OBJECTS = 1_000
_WIRE_WORD_OVERHEAD = 1_600
_WIRE_TABLE_OVERHEAD = 2_048
_WIRE_CELL_OVERHEAD = 1_024
_WIRE_BBOX_OVERHEAD = 512


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("PDF number is invalid")
    return float(value)


def _absolute_box(value: object, fallback: tuple[float, float, float, float]) -> BBox:
    try:
        if not isinstance(value, Sequence):
            raise TypeError
        raw = tuple(_number(item) for item in value)
        if len(raw) != 4:
            raise ValueError
        return BBox(*raw)
    except (TypeError, ValueError):
        return BBox(*fallback)


def _normalized_box(raw: Sequence[float], transform: CoordinateTransform) -> BBox:
    if len(raw) != 4:
        raise ValueError("invalid PDF object bbox")
    try:
        absolute = BBox(*(float(value) for value in raw))
        return transform.points_to_page(absolute)
    except (TypeError, ValueError) as error:
        raise ValueError("PDF object bbox is outside the displayed page") from error


def _object_boxes(objects: object, transform: CoordinateTransform) -> list[BBox]:
    if not isinstance(objects, list):
        raise TypeError("PDF page objects must be a list")
    if len(objects) > _MAX_PAGE_OBJECTS:
        raise LimitExceededError("PDF page objects exceed the resource budget")
    boxes: list[BBox] = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        try:
            payload = cast(dict[str, Any], item)
            boxes.append(
                _normalized_box(
                    (
                        float(payload["x0"]),
                        float(payload["top"]),
                        float(payload["x1"]),
                        float(payload["bottom"]),
                    ),
                    transform,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return boxes


def _connected_dense_regions(boxes: list[BBox]) -> list[BBox]:
    remaining = list(boxes)
    components: list[list[BBox]] = []
    while remaining:
        component = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for box in list(remaining):
                if any(
                    not (
                        member.right + 0.02 < box.left
                        or box.right + 0.02 < member.left
                        or member.bottom + 0.02 < box.top
                        or box.bottom + 0.02 < member.top
                    )
                    for member in component
                ):
                    component.append(box)
                    remaining.remove(box)
                    changed = True
        components.append(component)
    return [
        BBox(
            min(box.left for box in component),
            min(box.top for box in component),
            max(box.right for box in component),
            max(box.bottom for box in component),
        )
        for component in components
        if len(component) >= VECTOR_OBJECT_COUNT_MIN
    ]


def _reading_order_ambiguity(words: Sequence[PdfWord]) -> tuple[bool, list[BBox]]:
    overlapping: list[BBox] = []
    comparisons = 0
    ordered = sorted(words, key=lambda word: (word.bbox.top, word.bbox.left, word.source_index))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.bbox.top >= left.bbox.bottom:
                break
            comparisons += 1
            if comparisons > _MAX_READING_ORDER_COMPARISONS:
                raise LimitExceededError("PDF reading-order analysis exceeds the resource budget")
            horizontal = max(
                0.0,
                min(left.bbox.right, right.bbox.right) - max(left.bbox.left, right.bbox.left),
            )
            vertical = max(
                0.0,
                min(left.bbox.bottom, right.bbox.bottom) - max(left.bbox.top, right.bbox.top),
            )
            if horizontal * vertical <= 0:
                continue
            left_area = (left.bbox.right - left.bbox.left) * (left.bbox.bottom - left.bbox.top)
            right_area = (right.bbox.right - right.bbox.left) * (right.bbox.bottom - right.bbox.top)
            if horizontal * vertical / min(left_area, right_area) >= 0.20:
                if len(overlapping) >= MAX_VISUAL_CANDIDATES_PER_PAGE:
                    raise LimitExceededError(
                        "PDF visual region candidates exceed the resource budget"
                    )
                overlapping.append(
                    BBox(
                        min(left.bbox.left, right.bbox.left),
                        min(left.bbox.top, right.bbox.top),
                        max(left.bbox.right, right.bbox.right),
                        max(left.bbox.bottom, right.bbox.bottom),
                    )
                )
    return bool(overlapping), overlapping


def _words(page: Any, transform: CoordinateTransform) -> tuple[list[PdfWord], bool]:
    try:
        raw_words = page.extract_words(
            keep_blank_chars=False,
            use_text_flow=False,
            extra_attrs=["size", "fontname"],
        )
    except (
        AttributeError,
        KeyError,
        OSError,
        PDFException,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return [], True
    if not isinstance(raw_words, list):
        return [], True
    if len(raw_words) > _MAX_NATIVE_WORDS:
        raise LimitExceededError("PDF native word count exceeds the resource budget")
    words: list[PdfWord] = []
    text_char_count = 0
    failed = False
    for source_index, value in enumerate(raw_words):
        if not isinstance(value, dict):
            failed = True
            continue
        try:
            payload = cast(dict[str, Any], value)
            text = str(payload.get("text", "")).strip()
            if not text:
                continue
            text_char_count += len(text)
            if text_char_count > _MAX_NATIVE_TEXT_CHARS:
                raise LimitExceededError("PDF native text exceeds the resource budget")
            font_size_raw = payload.get("size")
            font_size = float(font_size_raw) if font_size_raw is not None else None
            font_name_raw = payload.get("fontname")
            words.append(
                PdfWord(
                    text,
                    _normalized_box(
                        (
                            float(payload["x0"]),
                            float(payload["top"]),
                            float(payload["x1"]),
                            float(payload["bottom"]),
                        ),
                        transform,
                    ),
                    source_index,
                    font_size if font_size is not None and font_size > 0 else None,
                    str(font_name_raw).strip() if font_name_raw else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            failed = True
    return words, failed


def _table_cell_count(raw_grid: object) -> int:
    if not isinstance(raw_grid, list):
        return 0
    return sum(len(row) for row in raw_grid if isinstance(row, list))


def _tables(
    page: Any,
    words: Sequence[PdfWord],
    transform: CoordinateTransform,
) -> tuple[list[Any], bool]:
    try:
        raw_tables = page.find_tables()
    except (
        AttributeError,
        KeyError,
        OSError,
        PDFException,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return [], True
    if not isinstance(raw_tables, list):
        return [], True
    if len(raw_tables) > _MAX_NATIVE_TABLES:
        raise LimitExceededError("PDF native table count exceeds the resource budget")
    candidates = []
    cell_count = 0
    failed = False
    for source_index, table in enumerate(raw_tables):
        try:
            table_value = cast(Any, table)
            bbox = _normalized_box(table_value.bbox, transform)
            raw_grid = table_value.extract()
            cell_count += _table_cell_count(raw_grid)
            if cell_count > _MAX_NATIVE_TABLE_CELLS:
                raise LimitExceededError("PDF native table cells exceed the resource budget")
            candidates.append(
                build_table_candidate(
                    bbox=bbox,
                    raw_grid=raw_grid,
                    words=words,
                    source_index=source_index,
                )
            )
        except (
            AttributeError,
            KeyError,
            OSError,
            PDFException,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            failed = True
    return candidates, failed


def _wire_estimate(
    words: Sequence[PdfWord],
    tables: Sequence[Any],
    *,
    object_bbox_count: int = 0,
) -> int:
    estimate = sum(
        _WIRE_WORD_OVERHEAD + len(word.text) * 8 + len(word.font_name or "") * 8 for word in words
    )
    for table in tables:
        estimate += _WIRE_TABLE_OVERHEAD
        estimate += sum(
            _WIRE_CELL_OVERHEAD + (len(cell) * 8 if cell is not None else 0)
            for row in table.grid
            for cell in row
        )
    return estimate + object_bbox_count * _WIRE_BBOX_OVERHEAD


def _enforce_wire_budget(words: Sequence[PdfWord], tables: Sequence[Any]) -> None:
    if _wire_estimate(words, tables) > _MAX_NATIVE_WIRE_ESTIMATE:
        raise LimitExceededError("PDF native analysis exceeds the inline result budget")


def _analyze_page(page: Any, page_number: int) -> PageFacts:
    width = float(page.width)
    height = float(page.height)
    if width <= 0 or height <= 0:
        raise ValueError("PDF page dimensions are invalid")
    media_box = _absolute_box(getattr(page, "mediabox", None), (0.0, 0.0, width, height))
    crop_box = _absolute_box(getattr(page, "cropbox", None), (0.0, 0.0, width, height))
    rotation = int(getattr(page, "rotation", 0) or 0) % 360
    if rotation not in {0, 90, 180, 270}:
        rotation = 0
    geometry = CoordinateTransform(
        crop_box,
        1,
        1,
        media_box=media_box,
        rotation=rotation,
    )
    crop_width = crop_box.right - crop_box.left
    crop_height = crop_box.bottom - crop_box.top
    display_width, display_height = (
        (crop_height, crop_width) if rotation in {90, 270} else (crop_width, crop_height)
    )

    words, word_failed = _words(page, geometry)
    all_tables, table_failed = _tables(page, words, geometry)
    _enforce_wire_budget(words, all_tables)
    accepted_tables = select_canonical_tables(all_tables)
    if not accepted_tables:
        heuristic_table = detect_heuristic_table(words)
        if heuristic_table is not None:
            accepted_tables = (heuristic_table,)
    native_candidates = build_native_candidates(words, accepted_tables)
    all_text = " ".join(word.text for word in words)
    quality = measure_text_quality(all_text)

    probe_failed = False
    try:
        image_boxes = _object_boxes(page.images, geometry)
        line_boxes = _object_boxes(page.lines, geometry)
        rect_boxes = _object_boxes(page.rects, geometry)
        curve_boxes = _object_boxes(page.curves, geometry)
    except (
        AttributeError,
        OSError,
        PDFException,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        image_boxes, line_boxes, rect_boxes, curve_boxes = [], [], [], []
        probe_failed = True

    object_bbox_count = sum(
        len(boxes) for boxes in (image_boxes, line_boxes, rect_boxes, curve_boxes)
    )
    if object_bbox_count > _MAX_PAGE_OBJECTS:
        raise LimitExceededError("PDF page objects exceed the resource budget")
    if (
        _wire_estimate(words, all_tables, object_bbox_count=object_bbox_count)
        > _MAX_NATIVE_WIRE_ESTIMATE
    ):
        raise LimitExceededError("PDF native analysis exceeds the inline result budget")

    drawings = [*line_boxes, *rect_boxes, *curve_boxes]
    unexplained_drawings = [
        box
        for box in drawings
        if not any(bbox_contains_center(table.bbox, box) for table in accepted_tables)
    ]
    dense_drawings = _connected_dense_regions(unexplained_drawings)
    ambiguous, reading_regions = _reading_order_ambiguity(words)
    invalid_table_boxes = [table.bbox for table in all_tables if not is_table_valid(table)]
    image_regions = (
        image_boxes
        if not all_text.strip()
        else [box for box in image_boxes if significant_image(box)]
    )
    sparse_drawing_regions = unexplained_drawings if not all_text.strip() else []
    visual_candidates = [
        *((box, "image") for box in image_regions),
        *((box, "table_structure_uncertain") for box in invalid_table_boxes),
        *((box, "dense_drawing") for box in dense_drawings),
        *((box, "drawing") for box in sparse_drawing_regions),
        *((box, "reading_order_ambiguous") for box in reading_regions),
    ]
    regions = build_visual_regions(visual_candidates)
    extraction_failed = word_failed or table_failed or probe_failed
    return PageFacts(
        page_number=page_number,
        media_box=media_box,
        crop_box=crop_box,
        rotation=rotation,
        display_width=display_width,
        display_height=display_height,
        words=tuple(words),
        tables=accepted_tables,
        native_candidates=native_candidates,
        visual_regions=regions,
        quality=quality,
        image_area_ratio=union_area(image_boxes),
        drawing_object_count=len(unexplained_drawings),
        native_extraction_failed=extraction_failed,
        reading_order_ambiguous=ambiguous,
        native_text_reliable=is_text_reliable(all_text, words, extraction_failed),
        image_bboxes=tuple(image_boxes),
        line_bboxes=tuple(line_boxes),
        rect_bboxes=tuple(rect_boxes),
        curve_bboxes=tuple(curve_boxes),
    )


def _page_dimension(page: Any, name: str) -> float:
    try:
        return max(float(getattr(page, name, 0) or 0), 1.0)
    except (AttributeError, OSError, PDFException, RuntimeError, TypeError, ValueError):
        return 1.0


def _fallback_page(page: Any, page_number: int) -> PageFacts:
    width = _page_dimension(page, "width")
    height = _page_dimension(page, "height")
    box = BBox(0.0, 0.0, width, height)
    return PageFacts(
        page_number,
        box,
        box,
        0,
        width,
        height,
        (),
        (),
        (),
        (VisualRegion(BBox(0.0, 0.0, 1.0, 1.0), ("native_extraction_failed",), 0),),
        measure_text_quality(""),
        0.0,
        0,
        True,
        False,
        False,
    )


def analyze_pdf_native(path: Path, max_pages: int) -> tuple[dict[str, object], ...]:
    try:
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise CorruptDocumentError("document is not a valid PDF")
    except CorruptDocumentError:
        raise
    except OSError as error:
        raise CorruptDocumentError("PDF is not readable") from error
    try:
        with pdfplumber.open(path) as pdf:
            if not getattr(pdf.doc, "is_extractable", True):
                raise CorruptDocumentError("PDF is encrypted or not extractable")
            if len(pdf.pages) > max_pages:
                raise LimitExceededError(f"PDF exceeds the {max_pages}-page limit")
            pages = []
            word_count = 0
            table_count = 0
            table_cell_count = 0
            wire_estimate = 0
            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    try:
                        facts = _analyze_page(page, page_number)
                    except (AttributeError, KeyError, TypeError, ValueError):
                        facts = _fallback_page(page, page_number)
                    word_count += len(facts.words)
                    table_count += len(facts.tables)
                    table_cell_count += sum(table.cell_count for table in facts.tables)
                    wire_estimate += _wire_estimate(
                        facts.words,
                        facts.tables,
                        object_bbox_count=sum(
                            len(boxes)
                            for boxes in (
                                facts.image_bboxes,
                                facts.line_bboxes,
                                facts.rect_bboxes,
                                facts.curve_bboxes,
                            )
                        ),
                    )
                    if (
                        word_count > _MAX_NATIVE_WORDS
                        or table_count > _MAX_NATIVE_TABLES
                        or table_cell_count > _MAX_NATIVE_TABLE_CELLS
                        or wire_estimate > _MAX_NATIVE_WIRE_ESTIMATE
                    ):
                        raise LimitExceededError("PDF native analysis exceeds the resource budget")
                    pages.append(page_to_wire(facts))
                finally:
                    close = getattr(page, "close", None)
                    if callable(close):
                        close()
            return tuple(pages)
    except (CorruptDocumentError, LimitExceededError):
        raise
    except PDFPasswordIncorrect as error:
        raise CorruptDocumentError("PDF is encrypted or password protected") from error
    except (
        OSError,
        PDFException,
        PDFSyntaxError,
        MalformedPDFException,
        PdfminerException,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        raise CorruptDocumentError("PDF is corrupt or cannot be decoded") from error


async def analyze_pdf(runtime: ParserRuntime, path: Path, max_pages: int) -> PdfAnalysis:
    values = await runtime.run_native(analyze_pdf_native, path, max_pages)
    return PdfAnalysis(tuple(page_from_wire(value) for value in values))
