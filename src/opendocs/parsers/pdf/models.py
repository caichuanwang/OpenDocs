from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias, cast

from opendocs._models import BBox


class PageRoute(StrEnum):
    NATIVE = "native"
    HYBRID = "hybrid"
    FULL_VISION = "full_vision"
    BLANK = "blank"


@dataclass(frozen=True, slots=True)
class TextQuality:
    non_whitespace_chars: int
    bad_char_ratio: float
    repeated_char_ratio: float


@dataclass(frozen=True, slots=True)
class PdfWord:
    text: str
    bbox: BBox
    source_index: int
    font_size: float | None = None
    font_name: str | None = None


@dataclass(frozen=True, slots=True)
class NativeTextCandidate:
    text: str
    bbox: BBox
    source_index: int


@dataclass(frozen=True, slots=True)
class NativeTableCandidate:
    bbox: BBox
    grid: tuple[tuple[str | None, ...], ...]
    header_rows: int
    source_index: int
    nonempty_ratio: float
    unknown_ratio: float
    text_capture_ratio: float

    @property
    def cell_count(self) -> int:
        return sum(len(row) for row in self.grid)


NativeCandidate: TypeAlias = NativeTextCandidate | NativeTableCandidate


@dataclass(frozen=True, slots=True)
class VisualRegion:
    bbox: BBox
    reasons: tuple[str, ...]
    source_index: int


@dataclass(frozen=True, slots=True)
class PageFacts:
    page_number: int
    media_box: BBox
    crop_box: BBox
    rotation: int
    display_width: float
    display_height: float
    words: tuple[PdfWord, ...]
    tables: tuple[NativeTableCandidate, ...]
    native_candidates: tuple[NativeCandidate, ...]
    visual_regions: tuple[VisualRegion, ...]
    quality: TextQuality
    image_area_ratio: float
    drawing_object_count: int
    native_extraction_failed: bool
    reading_order_ambiguous: bool
    native_text_reliable: bool
    image_bboxes: tuple[BBox, ...] = ()
    line_bboxes: tuple[BBox, ...] = ()
    rect_bboxes: tuple[BBox, ...] = ()
    curve_bboxes: tuple[BBox, ...] = ()


@dataclass(frozen=True, slots=True)
class PageRouteDecision:
    page_number: int
    route: PageRoute
    regions: tuple[VisualRegion, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PdfAnalysis:
    pages: tuple[PageFacts, ...]


def bbox_to_wire(bbox: BBox) -> tuple[float, float, float, float]:
    return float(bbox.left), float(bbox.top), float(bbox.right), float(bbox.bottom)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("PDF analysis number is invalid")
    return float(value)


def bbox_from_wire(value: object) -> BBox:
    if not isinstance(value, tuple) or len(value) != 4:
        raise ValueError("PDF analysis bbox is invalid")
    try:
        left, top, right, bottom = (_number(item) for item in value)
    except (TypeError, ValueError) as error:
        raise ValueError("PDF analysis bbox is invalid") from error
    return BBox(left, top, right, bottom)


def word_to_wire(word: PdfWord) -> dict[str, object]:
    return {
        "text": word.text,
        "bbox": bbox_to_wire(word.bbox),
        "source_index": word.source_index,
        "font_size": word.font_size,
        "font_name": word.font_name,
    }


def word_from_wire(value: object) -> PdfWord:
    if not isinstance(value, dict):
        raise ValueError("PDF analysis word is invalid")
    payload = cast(dict[str, Any], value)
    return PdfWord(
        text=str(payload["text"]),
        bbox=bbox_from_wire(payload["bbox"]),
        source_index=int(payload["source_index"]),
        font_size=float(payload["font_size"]) if payload.get("font_size") is not None else None,
        font_name=str(payload["font_name"]) if payload.get("font_name") is not None else None,
    )


def table_to_wire(table: NativeTableCandidate) -> dict[str, object]:
    return {
        "bbox": bbox_to_wire(table.bbox),
        "grid": table.grid,
        "header_rows": table.header_rows,
        "source_index": table.source_index,
        "nonempty_ratio": table.nonempty_ratio,
        "unknown_ratio": table.unknown_ratio,
        "text_capture_ratio": table.text_capture_ratio,
    }


def table_from_wire(value: object) -> NativeTableCandidate:
    if not isinstance(value, dict):
        raise ValueError("PDF analysis table is invalid")
    payload = cast(dict[str, Any], value)
    raw_grid = payload["grid"]
    if not isinstance(raw_grid, tuple):
        raise ValueError("PDF analysis table grid is invalid")
    grid = tuple(tuple(row) for row in raw_grid)
    return NativeTableCandidate(
        bbox=bbox_from_wire(payload["bbox"]),
        grid=grid,
        header_rows=int(payload["header_rows"]),
        source_index=int(payload["source_index"]),
        nonempty_ratio=float(payload["nonempty_ratio"]),
        unknown_ratio=float(payload["unknown_ratio"]),
        text_capture_ratio=float(payload["text_capture_ratio"]),
    )


def candidate_to_wire(candidate: NativeCandidate) -> dict[str, object]:
    if isinstance(candidate, NativeTableCandidate):
        return {"type": "table", "value": table_to_wire(candidate)}
    return {
        "type": "text",
        "value": {
            "text": candidate.text,
            "bbox": bbox_to_wire(candidate.bbox),
            "source_index": candidate.source_index,
        },
    }


def candidate_from_wire(value: object) -> NativeCandidate:
    if not isinstance(value, dict) or not isinstance(value.get("value"), dict):
        raise ValueError("PDF native candidate is invalid")
    value_payload = cast(dict[str, Any], value)
    payload = cast(dict[str, Any], value_payload["value"])
    if value.get("type") == "table":
        return table_from_wire(payload)
    if value.get("type") != "text":
        raise ValueError("PDF native candidate type is invalid")
    return NativeTextCandidate(
        text=str(payload["text"]),
        bbox=bbox_from_wire(payload["bbox"]),
        source_index=int(payload["source_index"]),
    )


def region_to_wire(region: VisualRegion) -> dict[str, object]:
    return {
        "bbox": bbox_to_wire(region.bbox),
        "reasons": region.reasons,
        "source_index": region.source_index,
    }


def region_from_wire(value: object) -> VisualRegion:
    if not isinstance(value, dict):
        raise ValueError("PDF visual region is invalid")
    payload = cast(dict[str, Any], value)
    reasons = payload["reasons"]
    if not isinstance(reasons, tuple):
        raise ValueError("PDF visual region reasons are invalid")
    return VisualRegion(
        bbox=bbox_from_wire(payload["bbox"]),
        reasons=tuple(str(reason) for reason in reasons),
        source_index=int(payload["source_index"]),
    )


def page_to_wire(page: PageFacts) -> dict[str, object]:
    return {
        "page_number": page.page_number,
        "media_box": bbox_to_wire(page.media_box),
        "crop_box": bbox_to_wire(page.crop_box),
        "rotation": page.rotation,
        "display_width": page.display_width,
        "display_height": page.display_height,
        "words": tuple(word_to_wire(word) for word in page.words),
        "tables": tuple(table_to_wire(table) for table in page.tables),
        "native_candidates": tuple(candidate_to_wire(item) for item in page.native_candidates),
        "visual_regions": tuple(region_to_wire(region) for region in page.visual_regions),
        "quality": {
            "non_whitespace_chars": page.quality.non_whitespace_chars,
            "bad_char_ratio": page.quality.bad_char_ratio,
            "repeated_char_ratio": page.quality.repeated_char_ratio,
        },
        "image_area_ratio": page.image_area_ratio,
        "drawing_object_count": page.drawing_object_count,
        "native_extraction_failed": page.native_extraction_failed,
        "reading_order_ambiguous": page.reading_order_ambiguous,
        "native_text_reliable": page.native_text_reliable,
        "image_bboxes": tuple(bbox_to_wire(bbox) for bbox in page.image_bboxes),
        "line_bboxes": tuple(bbox_to_wire(bbox) for bbox in page.line_bboxes),
        "rect_bboxes": tuple(bbox_to_wire(bbox) for bbox in page.rect_bboxes),
        "curve_bboxes": tuple(bbox_to_wire(bbox) for bbox in page.curve_bboxes),
    }


def page_from_wire(value: object) -> PageFacts:
    if not isinstance(value, dict) or not isinstance(value.get("quality"), dict):
        raise ValueError("PDF page analysis is invalid")
    payload = cast(dict[str, Any], value)
    quality = cast(dict[str, Any], payload["quality"])
    words = payload["words"]
    tables = payload["tables"]
    candidates = payload["native_candidates"]
    regions = payload["visual_regions"]
    image_bboxes = payload["image_bboxes"]
    line_bboxes = payload["line_bboxes"]
    rect_bboxes = payload["rect_bboxes"]
    curve_bboxes = payload["curve_bboxes"]
    collections = (
        words,
        tables,
        candidates,
        regions,
        image_bboxes,
        line_bboxes,
        rect_bboxes,
        curve_bboxes,
    )
    if not all(isinstance(items, tuple) for items in collections):
        raise ValueError("PDF page analysis collections are invalid")
    return PageFacts(
        page_number=int(payload["page_number"]),
        media_box=bbox_from_wire(payload["media_box"]),
        crop_box=bbox_from_wire(payload["crop_box"]),
        rotation=int(payload["rotation"]),
        display_width=float(payload["display_width"]),
        display_height=float(payload["display_height"]),
        words=tuple(word_from_wire(word) for word in words),
        tables=tuple(table_from_wire(table) for table in tables),
        native_candidates=tuple(candidate_from_wire(item) for item in candidates),
        visual_regions=tuple(region_from_wire(region) for region in regions),
        quality=TextQuality(
            int(quality["non_whitespace_chars"]),
            float(quality["bad_char_ratio"]),
            float(quality["repeated_char_ratio"]),
        ),
        image_area_ratio=float(payload["image_area_ratio"]),
        drawing_object_count=int(payload["drawing_object_count"]),
        native_extraction_failed=bool(payload["native_extraction_failed"]),
        reading_order_ambiguous=bool(payload["reading_order_ambiguous"]),
        native_text_reliable=bool(payload["native_text_reliable"]),
        image_bboxes=tuple(bbox_from_wire(bbox) for bbox in image_bboxes),
        line_bboxes=tuple(bbox_from_wire(bbox) for bbox in line_bboxes),
        rect_bboxes=tuple(bbox_from_wire(bbox) for bbox in rect_bboxes),
        curve_bboxes=tuple(bbox_from_wire(bbox) for bbox in curve_bboxes),
    )
