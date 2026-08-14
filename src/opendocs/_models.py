from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias
from urllib.parse import urlsplit


def _require_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _require_document_type(value: object) -> DocumentType:
    if not isinstance(value, DocumentType):
        raise TypeError("document_type must be a DocumentType")
    return value


def _require_tuple(name: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    return value


def _require_block(name: str, index: int, value: object) -> Block:
    if not isinstance(
        value,
        TextBlock
        | MarkdownBlock
        | PageBreakBlock
        | TableBlock
        | ParagraphBlock
        | HeadingBlock
        | ListItemBlock
        | HardPageBreakBlock
        | SpannedTableBlock,
    ):
        raise TypeError(
            f"{name}[{index}] must be a TextBlock, MarkdownBlock, PageBreakBlock, TableBlock, "
            "ParagraphBlock, HeadingBlock, ListItemBlock, HardPageBreakBlock, or "
            "SpannedTableBlock"
        )
    return value


def _require_warning_record(name: str, index: int, value: object) -> WarningRecord:
    if not isinstance(value, WarningRecord):
        raise TypeError(f"{name}[{index}] must be a WarningRecord")
    return value


class DocumentType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"


class ListKind(StrEnum):
    BULLET = "bullet"
    ORDERED = "ordered"


def _require_inline(name: str, index: int, value: object) -> Inline:
    if not isinstance(value, InlineText | InlineLink):
        raise TypeError(f"{name}[{index}] must be an InlineText or InlineLink")
    return value


def _require_inline_tuple(name: str, value: object) -> tuple[Inline, ...]:
    items = _require_tuple(name, value)
    if not items:
        raise ValueError(f"{name} must contain at least one inline")
    normalized: list[Inline] = []
    for index, item in enumerate(items):
        normalized.append(_require_inline(name, index, item))
    return tuple(normalized)


def _require_list_kind(value: object) -> ListKind:
    if not isinstance(value, ListKind):
        raise TypeError("kind must be a ListKind")
    return value


def _normalize_link_target(target: object) -> str:
    normalized = _require_string("target", target)
    if not normalized:
        raise ValueError("target must not be empty")
    if normalized != normalized.strip():
        raise ValueError("target must not have surrounding whitespace")
    if any(character.isspace() for character in normalized):
        raise ValueError("target must not contain whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("target must not contain control characters")
    urlsplit(normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str

    def __post_init__(self) -> None:
        _require_string("text", self.text)


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    markdown: str

    def __post_init__(self) -> None:
        _require_string("markdown", self.markdown)


@dataclass(frozen=True, slots=True)
class InlineText:
    text: str

    def __post_init__(self) -> None:
        _require_string("text", self.text)


@dataclass(frozen=True, slots=True, init=False)
class InlineLink:
    label: str
    target: str

    def __init__(self, label: str, target: str) -> None:
        normalized_label = _require_string("label", label)
        if not normalized_label:
            raise ValueError("label must not be empty")
        object.__setattr__(self, "label", normalized_label)
        object.__setattr__(self, "target", _normalize_link_target(target))


Inline: TypeAlias = InlineText | InlineLink


@dataclass(frozen=True, slots=True)
class PageBreakBlock:
    page_number: int

    def __post_init__(self) -> None:
        page_number = _require_int("page_number", self.page_number)
        if page_number <= 0:
            raise ValueError("page_number must be greater than zero")


@dataclass(frozen=True, slots=True, init=False)
class ParagraphBlock:
    inlines: tuple[Inline, ...]

    def __init__(self, inlines: tuple[Inline, ...]) -> None:
        object.__setattr__(self, "inlines", _require_inline_tuple("inlines", inlines))


@dataclass(frozen=True, slots=True, init=False)
class HeadingBlock:
    level: int
    inlines: tuple[Inline, ...]

    def __init__(self, level: int, inlines: tuple[Inline, ...]) -> None:
        normalized_level = _require_int("level", level)
        object.__setattr__(self, "level", min(max(normalized_level, 1), 6))
        object.__setattr__(self, "inlines", _require_inline_tuple("inlines", inlines))


@dataclass(frozen=True, slots=True, init=False)
class ListItemBlock:
    list_id: int
    level: int
    kind: ListKind
    ordinal: int
    inlines: tuple[Inline, ...]

    def __init__(
        self,
        list_id: int,
        level: int,
        kind: ListKind,
        ordinal: int,
        inlines: tuple[Inline, ...],
    ) -> None:
        normalized_list_id = _require_int("list_id", list_id)
        if normalized_list_id < 0:
            raise ValueError("list_id must be greater than or equal to zero")
        normalized_level = _require_int("level", level)
        if normalized_level < 0:
            raise ValueError("level must be greater than or equal to zero")
        normalized_ordinal = _require_int("ordinal", ordinal)
        if normalized_ordinal <= 0:
            raise ValueError("ordinal must be greater than zero")
        object.__setattr__(self, "list_id", normalized_list_id)
        object.__setattr__(self, "level", normalized_level)
        object.__setattr__(self, "kind", _require_list_kind(kind))
        object.__setattr__(self, "ordinal", normalized_ordinal)
        object.__setattr__(self, "inlines", _require_inline_tuple("inlines", inlines))


@dataclass(frozen=True, slots=True)
class HardPageBreakBlock:
    pass


@dataclass(frozen=True, slots=True, init=False)
class TableBlock:
    grid: tuple[tuple[str, ...], ...]
    header_rows: int

    def __init__(
        self,
        grid: tuple[tuple[str | None, ...], ...],
        header_rows: int,
    ) -> None:
        rows = _require_tuple("grid", grid)
        if not rows:
            raise ValueError("grid must contain at least one row")

        normalized: list[tuple[str, ...]] = []
        width: int | None = None
        for row_index, row_value in enumerate(rows):
            row = _require_tuple(f"grid[{row_index}]", row_value)
            if width is None:
                width = len(row)
                if width == 0:
                    raise ValueError("grid must contain at least one column")
            elif len(row) != width:
                raise ValueError("grid must be rectangular")

            cells: list[str] = []
            for column_index, cell in enumerate(row):
                if cell is None:
                    cells.append("")
                elif isinstance(cell, str):
                    cells.append(cell)
                else:
                    raise TypeError(f"grid[{row_index}][{column_index}] must be a str or None")
            normalized.append(tuple(cells))

        normalized_header_rows = _require_int("header_rows", header_rows)
        if not 0 <= normalized_header_rows <= len(normalized):
            raise ValueError("header_rows must be between zero and the number of rows")

        object.__setattr__(self, "grid", tuple(normalized))
        object.__setattr__(self, "header_rows", normalized_header_rows)


@dataclass(frozen=True, slots=True, init=False)
class SpannedTableCell:
    row: int
    column: int
    row_span: int
    column_span: int
    text: str

    def __init__(
        self,
        row: int,
        column: int,
        row_span: int,
        column_span: int,
        text: str,
    ) -> None:
        normalized_row = _require_int("row", row)
        normalized_column = _require_int("column", column)
        normalized_row_span = _require_int("row_span", row_span)
        normalized_column_span = _require_int("column_span", column_span)
        if normalized_row < 0:
            raise ValueError("row must be greater than or equal to zero")
        if normalized_column < 0:
            raise ValueError("column must be greater than or equal to zero")
        if normalized_row_span <= 0:
            raise ValueError("row_span must be greater than zero")
        if normalized_column_span <= 0:
            raise ValueError("column_span must be greater than zero")
        object.__setattr__(self, "row", normalized_row)
        object.__setattr__(self, "column", normalized_column)
        object.__setattr__(self, "row_span", normalized_row_span)
        object.__setattr__(self, "column_span", normalized_column_span)
        object.__setattr__(self, "text", _require_string("text", text))


@dataclass(frozen=True, slots=True, init=False)
class SpannedTableBlock:
    row_count: int
    column_count: int
    cells: tuple[SpannedTableCell, ...]
    header_rows: int

    def __init__(
        self,
        row_count: int,
        column_count: int,
        cells: tuple[SpannedTableCell, ...],
        header_rows: int,
    ) -> None:
        normalized_row_count = _require_int("row_count", row_count)
        normalized_column_count = _require_int("column_count", column_count)
        if normalized_row_count <= 0:
            raise ValueError("row_count must be greater than zero")
        if normalized_column_count <= 0:
            raise ValueError("column_count must be greater than zero")

        normalized_cells_raw = _require_tuple("cells", cells)
        if not normalized_cells_raw:
            raise ValueError("cells must contain at least one cell")

        normalized_cells: list[SpannedTableCell] = []
        occupied = [
            [False for _ in range(normalized_column_count)] for _ in range(normalized_row_count)
        ]
        for index, cell_value in enumerate(normalized_cells_raw):
            if not isinstance(cell_value, SpannedTableCell):
                raise TypeError(f"cells[{index}] must be a SpannedTableCell")
            row_limit = cell_value.row + cell_value.row_span
            column_limit = cell_value.column + cell_value.column_span
            if row_limit > normalized_row_count or column_limit > normalized_column_count:
                raise ValueError("cells must remain within table bounds")
            for row_index in range(cell_value.row, row_limit):
                for column_index in range(cell_value.column, column_limit):
                    if occupied[row_index][column_index]:
                        raise ValueError("cells must not overlap")
                    occupied[row_index][column_index] = True
            normalized_cells.append(cell_value)

        if not all(all(row) for row in occupied):
            raise ValueError("cells must cover every coordinate in the table")

        normalized_header_rows = _require_int("header_rows", header_rows)
        if not 0 <= normalized_header_rows <= normalized_row_count:
            raise ValueError("header_rows must be between zero and the number of rows")

        object.__setattr__(self, "row_count", normalized_row_count)
        object.__setattr__(self, "column_count", normalized_column_count)
        object.__setattr__(
            self,
            "cells",
            tuple(sorted(normalized_cells, key=lambda cell: (cell.row, cell.column))),
        )
        object.__setattr__(self, "header_rows", normalized_header_rows)


Block: TypeAlias = (
    TextBlock
    | MarkdownBlock
    | PageBreakBlock
    | TableBlock
    | ParagraphBlock
    | HeadingBlock
    | ListItemBlock
    | HardPageBreakBlock
    | SpannedTableBlock
)


@dataclass(frozen=True, slots=True)
class WarningRecord:
    code: str
    message: str

    def __post_init__(self) -> None:
        _require_string("code", self.code)
        _require_string("message", self.message)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    document_type: DocumentType
    blocks: tuple[Block, ...]
    warnings: tuple[WarningRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_document_type(self.document_type)

        blocks = _require_tuple("blocks", self.blocks)
        for index, block in enumerate(blocks):
            _require_block("blocks", index, block)

        warnings = _require_tuple("warnings", self.warnings)
        for index, warning in enumerate(warnings):
            _require_warning_record("warnings", index, warning)


@dataclass(frozen=True, slots=True)
class RenderResult:
    markdown: str
    warnings: tuple[WarningRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_string("markdown", self.markdown)

        warnings = _require_tuple("warnings", self.warnings)
        for index, warning in enumerate(warnings):
            _require_warning_record("warnings", index, warning)


@dataclass(frozen=True, slots=True)
class BBox:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if any(isinstance(value, bool) or not isinstance(value, int | float) for value in values):
            raise TypeError("bbox coordinates must be real numbers")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("bbox coordinates must be finite")
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("bbox must have positive width and height")

    def require_normalized(self, name: str = "bbox") -> BBox:
        if self.left < 0 or self.top < 0 or self.right > 1 or self.bottom > 1:
            raise ValueError(f"{name} coordinates must be within [0, 1]")
        return self


@dataclass(frozen=True, slots=True, init=False)
class CoordinateTransform:
    media_box: BBox
    crop_box: BBox
    rotation: int
    raster_width: int
    raster_height: int
    crop_pixel_box: tuple[int, int, int, int]

    def __init__(
        self,
        crop_box: BBox,
        raster_width: int,
        raster_height: int,
        *,
        media_box: BBox | None = None,
        rotation: int = 0,
        crop_pixel_box: tuple[int, int, int, int] | None = None,
    ) -> None:
        if not isinstance(crop_box, BBox):
            raise TypeError("crop_box must be a BBox")
        resolved_media = media_box if media_box is not None else crop_box
        if not isinstance(resolved_media, BBox):
            raise TypeError("media_box must be a BBox")
        if (
            crop_box.left < resolved_media.left
            or crop_box.top < resolved_media.top
            or crop_box.right > resolved_media.right
            or crop_box.bottom > resolved_media.bottom
        ):
            raise ValueError("crop_box must be within media_box")
        resolved_rotation = _require_int("rotation", rotation)
        if resolved_rotation not in {0, 90, 180, 270}:
            raise ValueError("rotation must be 0, 90, 180, or 270")
        resolved_width = _require_int("raster_width", raster_width)
        resolved_height = _require_int("raster_height", raster_height)
        if resolved_width <= 0 or resolved_height <= 0:
            raise ValueError("raster dimensions must be greater than zero")
        resolved_crop = (
            (0, 0, resolved_width, resolved_height) if crop_pixel_box is None else crop_pixel_box
        )
        if not isinstance(resolved_crop, tuple) or len(resolved_crop) != 4:
            raise TypeError("crop_pixel_box must be a four-item tuple")
        left, top, right, bottom = resolved_crop
        for value in resolved_crop:
            _require_int("crop pixel coordinate", value)
        if (
            left < 0
            or top < 0
            or right > resolved_width
            or bottom > resolved_height
            or left >= right
            or top >= bottom
        ):
            raise ValueError("crop_pixel_box must be a positive rectangle within the raster")
        object.__setattr__(self, "media_box", resolved_media)
        object.__setattr__(self, "crop_box", crop_box)
        object.__setattr__(self, "rotation", resolved_rotation)
        object.__setattr__(self, "raster_width", resolved_width)
        object.__setattr__(self, "raster_height", resolved_height)
        object.__setattr__(self, "crop_pixel_box", resolved_crop)

    def _rotate_point(self, x: float, y: float) -> tuple[float, float]:
        if self.rotation == 0:
            return x, y
        if self.rotation == 90:
            return 1 - y, x
        if self.rotation == 180:
            return 1 - x, 1 - y
        return y, 1 - x

    def _unrotate_point(self, x: float, y: float) -> tuple[float, float]:
        if self.rotation == 0:
            return x, y
        if self.rotation == 90:
            return y, 1 - x
        if self.rotation == 180:
            return 1 - x, 1 - y
        return 1 - y, x

    @staticmethod
    def _bounds(points: tuple[tuple[float, float], ...]) -> BBox:
        xs = tuple(point[0] for point in points)
        ys = tuple(point[1] for point in points)
        return BBox(min(xs), min(ys), max(xs), max(ys))

    def points_to_page(self, bbox: BBox) -> BBox:
        if (
            bbox.left < self.crop_box.left
            or bbox.top < self.crop_box.top
            or bbox.right > self.crop_box.right
            or bbox.bottom > self.crop_box.bottom
        ):
            raise ValueError("point bbox must be within crop_box")
        width = self.crop_box.right - self.crop_box.left
        height = self.crop_box.bottom - self.crop_box.top
        corners = (
            ((bbox.left - self.crop_box.left) / width, (bbox.top - self.crop_box.top) / height),
            ((bbox.right - self.crop_box.left) / width, (bbox.top - self.crop_box.top) / height),
            ((bbox.left - self.crop_box.left) / width, (bbox.bottom - self.crop_box.top) / height),
            ((bbox.right - self.crop_box.left) / width, (bbox.bottom - self.crop_box.top) / height),
        )
        return self._bounds(
            tuple(self._rotate_point(*point) for point in corners)
        ).require_normalized("page bbox")

    def page_to_points(self, bbox: BBox) -> BBox:
        bbox.require_normalized("page bbox")
        width = self.crop_box.right - self.crop_box.left
        height = self.crop_box.bottom - self.crop_box.top
        corners = (
            self._unrotate_point(bbox.left, bbox.top),
            self._unrotate_point(bbox.right, bbox.top),
            self._unrotate_point(bbox.left, bbox.bottom),
            self._unrotate_point(bbox.right, bbox.bottom),
        )
        normalized = self._bounds(corners)
        return BBox(
            self.crop_box.left + normalized.left * width,
            self.crop_box.top + normalized.top * height,
            self.crop_box.left + normalized.right * width,
            self.crop_box.top + normalized.bottom * height,
        )

    def page_to_pixels(self, bbox: BBox) -> tuple[int, int, int, int]:
        bbox.require_normalized("page bbox")
        crop_left, crop_top, crop_right, crop_bottom = self.crop_pixel_box
        crop_width = crop_right - crop_left
        crop_height = crop_bottom - crop_top
        left = crop_left + max(0, min(crop_width - 1, math.floor(bbox.left * crop_width)))
        top = crop_top + max(0, min(crop_height - 1, math.floor(bbox.top * crop_height)))
        right = crop_left + max(
            left - crop_left + 1, min(crop_width, math.ceil(bbox.right * crop_width))
        )
        bottom = crop_top + max(
            top - crop_top + 1, min(crop_height, math.ceil(bbox.bottom * crop_height))
        )
        return left, top, right, bottom

    def pixels_to_page(self, bbox: tuple[int, int, int, int]) -> BBox:
        if not isinstance(bbox, tuple) or len(bbox) != 4:
            raise TypeError("pixel bbox must be a four-item tuple")
        left, top, right, bottom = bbox
        for value in bbox:
            _require_int("pixel coordinate", value)
        crop_left, crop_top, crop_right, crop_bottom = self.crop_pixel_box
        if (
            left < crop_left
            or top < crop_top
            or right > crop_right
            or bottom > crop_bottom
            or left >= right
            or top >= bottom
            or left < 0
            or top < 0
            or right > self.raster_width
            or bottom > self.raster_height
        ):
            raise ValueError("pixel bbox must be a positive rectangle within raster crop")
        crop_width = crop_right - crop_left
        crop_height = crop_bottom - crop_top
        return BBox(
            (left - crop_left) / crop_width,
            (top - crop_top) / crop_height,
            (right - crop_left) / crop_width,
            (bottom - crop_top) / crop_height,
        ).require_normalized("page bbox")

    def crop_to_page(self, bbox: BBox, crop_bbox: BBox) -> BBox:
        bbox.require_normalized("crop bbox")
        crop_bbox.require_normalized("page crop bbox")
        width = crop_bbox.right - crop_bbox.left
        height = crop_bbox.bottom - crop_bbox.top
        mapped = BBox(
            crop_bbox.left + bbox.left * width,
            crop_bbox.top + bbox.top * height,
            crop_bbox.left + bbox.right * width,
            crop_bbox.top + bbox.bottom * height,
        )
        return mapped.require_normalized("mapped bbox")
