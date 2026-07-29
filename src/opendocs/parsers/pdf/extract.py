from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import pairwise

from opendocs._models import BBox
from opendocs.parsers.pdf.models import (
    NativeCandidate,
    NativeTableCandidate,
    NativeTextCandidate,
    PdfWord,
    TextQuality,
)

_BAD_CHAR_RATIO_MAX = 0.02
_REPEATED_CHAR_RATIO_MAX = 0.35
_REPEATED_CHAR_MIN_COUNT = 40
_TABLE_NONEMPTY_RATIO_MIN = 0.30
_TABLE_UNKNOWN_RATIO_MAX = 0.40
_TABLE_TEXT_CAPTURE_RATIO_MIN = 0.80
_TABLE_DUPLICATE_IOU_MIN = 0.80
_CID_PATTERN = re.compile(r"\(cid:\d+\)")


def bbox_area(bbox: BBox) -> float:
    return float((bbox.right - bbox.left) * (bbox.bottom - bbox.top))


def intersection_area(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.right, right.right) - max(left.left, right.left))
    height = max(0.0, min(left.bottom, right.bottom) - max(left.top, right.top))
    return width * height


def intersection_over_union(left: BBox, right: BBox) -> float:
    intersection = intersection_area(left, right)
    union = bbox_area(left) + bbox_area(right) - intersection
    return intersection / union if union > 0 else 0.0


def bbox_contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and outer.right >= inner.right
        and outer.bottom >= inner.bottom
    )


def bbox_contains_center(outer: BBox, inner: BBox) -> bool:
    center_x = (inner.left + inner.right) / 2
    center_y = (inner.top + inner.bottom) / 2
    return outer.left <= center_x <= outer.right and outer.top <= center_y <= outer.bottom


def union_area(bboxes: Sequence[BBox]) -> float:
    if not bboxes:
        return 0.0
    edges = sorted({edge for bbox in bboxes for edge in (bbox.left, bbox.right)})
    area = 0.0
    for left, right in pairwise(edges):
        intervals = sorted(
            (bbox.top, bbox.bottom) for bbox in bboxes if bbox.left < right and bbox.right > left
        )
        covered = 0.0
        current_top: float | None = None
        current_bottom: float | None = None
        for top, bottom in intervals:
            if current_top is None:
                current_top, current_bottom = top, bottom
            elif current_bottom is not None and top <= current_bottom:
                current_bottom = max(current_bottom, bottom)
            else:
                if current_bottom is not None:
                    covered += current_bottom - current_top
                current_top, current_bottom = top, bottom
        if current_top is not None and current_bottom is not None:
            covered += current_bottom - current_top
        area += (right - left) * covered
    return area


def measure_text_quality(text: str) -> TextQuality:
    characters = [character for character in text if not character.isspace()]
    denominator = max(len(characters), 1)
    bad_indexes: set[int] = set()
    for index, character in enumerate(text):
        if character == "�" or (
            unicodedata.category(character) == "Cc" and character not in "\t\n\r"
        ):
            bad_indexes.add(index)
    for match in _CID_PATTERN.finditer(text):
        bad_indexes.update(range(match.start(), match.end()))
    bad_count = sum(index < len(text) and not text[index].isspace() for index in bad_indexes)
    most_common = Counter(characters).most_common(1)
    repeated_ratio = most_common[0][1] / denominator if most_common else 0.0
    return TextQuality(len(characters), bad_count / denominator, repeated_ratio)


def _box_drawing_dominates_varied_text(text: str) -> bool:
    characters = [character for character in text if not character.isspace()]
    most_common = Counter(characters).most_common(1)
    if not most_common or not unicodedata.name(most_common[0][0], "").startswith("BOX DRAWINGS"):
        return False
    content = [
        character
        for character in characters
        if not unicodedata.name(character, "").startswith("BOX DRAWINGS")
    ]
    content_common = Counter(content).most_common(1)
    return bool(content_common and content_common[0][1] / len(content) <= _REPEATED_CHAR_RATIO_MAX)


def is_text_reliable(text: str, words: Sequence[PdfWord], extraction_failed: bool) -> bool:
    if extraction_failed or (text.strip() and not words):
        return False
    quality = measure_text_quality(text)
    if quality.bad_char_ratio > _BAD_CHAR_RATIO_MAX:
        return False
    return not (
        quality.non_whitespace_chars >= _REPEATED_CHAR_MIN_COUNT
        and quality.repeated_char_ratio > _REPEATED_CHAR_RATIO_MAX
        and not _box_drawing_dominates_varied_text(text)
    )


def normalize_grid(raw_grid: object) -> tuple[tuple[str | None, ...], ...]:
    if not isinstance(raw_grid, list) or not raw_grid:
        raise ValueError("table grid must contain at least one row")
    rows: list[list[str | None]] = []
    width = 0
    for raw_row in raw_grid:
        if not isinstance(raw_row, list):
            raise ValueError("table rows must be arrays")
        row: list[str | None] = []
        for cell in raw_row:
            if cell is None:
                row.append(None)
            elif isinstance(cell, str):
                row.append(cell.strip())
            else:
                row.append(str(cell).strip())
        width = max(width, len(row))
        rows.append(row)
    if width == 0:
        raise ValueError("table grid must contain at least one column")
    return tuple(tuple([*row, *([None] * (width - len(row)))]) for row in rows)


def build_table_candidate(
    *,
    bbox: BBox,
    raw_grid: object,
    words: Sequence[PdfWord],
    source_index: int,
    header_rows: int = 0,
) -> NativeTableCandidate:
    grid = normalize_grid(raw_grid)
    if not 0 <= header_rows <= len(grid):
        raise ValueError("table header rows are invalid")
    cells = [cell for row in grid for cell in row]
    nonempty = sum(cell is not None and bool(cell.strip()) for cell in cells)
    unknown = sum(cell is None for cell in cells)
    bbox_text = "".join(word.text for word in words if bbox_contains_center(bbox, word.bbox))
    captured_text = "".join(cell for cell in cells if cell is not None)
    bbox_chars = Counter(character for character in bbox_text if not character.isspace())
    captured_chars = Counter(character for character in captured_text if not character.isspace())
    captured_count = sum((bbox_chars & captured_chars).values())
    return NativeTableCandidate(
        bbox=bbox,
        grid=grid,
        header_rows=header_rows,
        source_index=source_index,
        nonempty_ratio=nonempty / max(len(cells), 1),
        unknown_ratio=unknown / max(len(cells), 1),
        text_capture_ratio=captured_count / max(sum(bbox_chars.values()), 1),
    )


def is_table_valid(candidate: NativeTableCandidate) -> bool:
    widths = {len(row) for row in candidate.grid}
    return bool(
        candidate.grid
        and widths
        and min(widths) > 0
        and len(widths) == 1
        and candidate.nonempty_ratio >= _TABLE_NONEMPTY_RATIO_MIN
        and candidate.unknown_ratio <= _TABLE_UNKNOWN_RATIO_MAX
        and candidate.text_capture_ratio >= _TABLE_TEXT_CAPTURE_RATIO_MIN
    )


def select_canonical_tables(
    candidates: Iterable[NativeTableCandidate],
) -> tuple[NativeTableCandidate, ...]:
    ordered = sorted(
        (candidate for candidate in candidates if is_table_valid(candidate)),
        key=lambda candidate: (
            -candidate.cell_count,
            -bbox_area(candidate.bbox),
            candidate.bbox.top,
            candidate.bbox.left,
            candidate.source_index,
        ),
    )
    accepted: list[NativeTableCandidate] = []
    for candidate in ordered:
        if any(
            bbox_contains(existing.bbox, candidate.bbox)
            or bbox_contains(candidate.bbox, existing.bbox)
            or intersection_over_union(existing.bbox, candidate.bbox) >= _TABLE_DUPLICATE_IOU_MIN
            for existing in accepted
        ):
            continue
        accepted.append(candidate)
    return tuple(
        sorted(
            accepted,
            key=lambda item: (item.bbox.top, item.bbox.left, item.source_index),
        )
    )


def _line_key(word: PdfWord) -> tuple[float, float, int]:
    return word.bbox.top, word.bbox.left, word.source_index


def build_native_candidates(
    words: Sequence[PdfWord], tables: Sequence[NativeTableCandidate]
) -> tuple[NativeCandidate, ...]:
    owned_words = [
        word
        for word in words
        if not any(bbox_contains_center(table.bbox, word.bbox) for table in tables)
    ]
    candidates: list[NativeCandidate] = [
        NativeTextCandidate(word.text, word.bbox, word.source_index)
        for word in sorted(owned_words, key=_line_key)
        if word.text.strip()
    ]
    candidates.extend(tables)
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.bbox.top,
                candidate.bbox.left,
                candidate.source_index,
            ),
        )
    )
