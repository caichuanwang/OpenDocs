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
_TABLE_NESTED_TEXT_SIMILARITY_MIN = 0.80
_CID_PATTERN = re.compile(r"\(cid:\d+\)")
_CJK_RANGES = (
    (0x2E80, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_LINE_CENTER_TOLERANCE = 0.55
_LINE_SPLIT_GAP_FACTOR = 2.5
_MAX_ACTIVE_LINES = 64
_COLUMN_GAP_MIN = 0.12
_COLUMN_OVERLAP_MIN = 0.20
_WIDE_BLOCK_WIDTH_MIN = 0.70
_HEURISTIC_TABLE_MAX_WORDS = 500
_HEURISTIC_TABLE_MIN_ROWS = 3
_HEURISTIC_TABLE_MIN_COLUMNS = 2
_HEURISTIC_TABLE_COLUMN_TOLERANCE = 0.025
_HEURISTIC_TABLE_NUMERIC_RATIO_MIN = 0.40


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
        category = unicodedata.category(character)
        value = ord(character)
        if (
            character == "�"
            or (category == "Cc" and character not in "\t\n\r")
            or category == "Cs"
            or value & 0xFFFF in {0xFFFE, 0xFFFF}
            or 0xFDD0 <= value <= 0xFDEF
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
        and min(widths) >= 2
        and len(candidate.grid) >= 2
        and len(widths) == 1
        and candidate.nonempty_ratio >= _TABLE_NONEMPTY_RATIO_MIN
        and candidate.unknown_ratio <= _TABLE_UNKNOWN_RATIO_MAX
        and candidate.text_capture_ratio >= _TABLE_TEXT_CAPTURE_RATIO_MIN
    )


def _table_text_similarity(left: NativeTableCandidate, right: NativeTableCandidate) -> float:
    left_text = Counter(
        character
        for row in left.grid
        for cell in row
        if cell is not None
        for character in cell
        if not character.isspace()
    )
    right_text = Counter(
        character
        for row in right.grid
        for cell in row
        if cell is not None
        for character in cell
        if not character.isspace()
    )
    shared = sum((left_text & right_text).values())
    return shared / max(min(sum(left_text.values()), sum(right_text.values())), 1)


def _tables_are_duplicates(
    left: NativeTableCandidate,
    right: NativeTableCandidate,
) -> bool:
    if intersection_over_union(left.bbox, right.bbox) >= _TABLE_DUPLICATE_IOU_MIN:
        return True
    nested = bbox_contains(left.bbox, right.bbox) or bbox_contains(right.bbox, left.bbox)
    return nested and _table_text_similarity(left, right) >= _TABLE_NESTED_TEXT_SIMILARITY_MIN


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
        if any(_tables_are_duplicates(existing, candidate) for existing in accepted):
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


def _is_cjk(character: str) -> bool:
    value = ord(character)
    return any(start <= value <= end for start, end in _CJK_RANGES)


def _word_separator(left: PdfWord, right: PdfWord) -> str:
    if not left.text or not right.text:
        return ""
    if _is_cjk(left.text[-1]) or _is_cjk(right.text[0]):
        return ""
    if right.text[0] in ",.;:!?)]}%" or left.text[-1] in "([{$/":
        return ""
    return " "


def _same_line(left: PdfWord, right: PdfWord) -> bool:
    left_center = (left.bbox.top + left.bbox.bottom) / 2
    right_center = (right.bbox.top + right.bbox.bottom) / 2
    height = min(left.bbox.bottom - left.bbox.top, right.bbox.bottom - right.bbox.top)
    return abs(left_center - right_center) <= max(height * _LINE_CENTER_TOLERANCE, 0.002)


def _line_word_groups(words: Sequence[PdfWord]) -> list[list[PdfWord]]:
    lines: list[list[PdfWord]] = []
    for word in sorted(words, key=_line_key):
        matched = False
        for line in reversed(lines[-_MAX_ACTIVE_LINES:]):
            if _same_line(line[0], word):
                line.append(word)
                matched = True
                break
            if word.bbox.top - line[0].bbox.bottom > 0.02:
                break
        if not matched:
            lines.append([word])
    return [sorted(line, key=lambda item: (item.bbox.left, item.source_index)) for line in lines]


def _word_groups(words: Sequence[PdfWord]) -> list[list[PdfWord]]:
    groups: list[list[PdfWord]] = []
    for ordered in _line_word_groups(words):
        current: list[PdfWord] = []
        for word in ordered:
            if current:
                previous = current[-1]
                typical_height = max(previous.bbox.bottom - previous.bbox.top, 0.001)
                if word.bbox.left - previous.bbox.right > typical_height * _LINE_SPLIT_GAP_FACTOR:
                    groups.append(current)
                    current = []
            current.append(word)
        if current:
            groups.append(current)
    return groups


def _line_candidate(words: Sequence[PdfWord]) -> NativeTextCandidate:
    ordered = sorted(words, key=lambda word: (word.bbox.left, word.source_index))
    text = ordered[0].text
    for left, right in pairwise(ordered):
        text += _word_separator(left, right) + right.text
    font_sizes = [word.font_size for word in ordered if word.font_size is not None]
    font_names = [word.font_name for word in ordered if word.font_name]
    font_size = sum(font_sizes) / len(font_sizes) if font_sizes else None
    font_name = Counter(font_names).most_common(1)[0][0] if font_names else None
    return NativeTextCandidate(
        text=text,
        bbox=BBox(
            min(word.bbox.left for word in ordered),
            min(word.bbox.top for word in ordered),
            max(word.bbox.right for word in ordered),
            max(word.bbox.bottom for word in ordered),
        ),
        source_index=min(word.source_index for word in ordered),
        font_size=font_size,
        font_name=font_name,
    )


def _column_order_narrow(
    candidates: Sequence[NativeTextCandidate],
) -> list[NativeTextCandidate]:
    if len(candidates) < 2:
        return list(candidates)

    def fallback() -> list[NativeTextCandidate]:
        return sorted(
            candidates,
            key=lambda item: (item.bbox.top, item.bbox.left, item.source_index),
        )

    ordered = sorted(candidates, key=lambda item: (item.bbox.left, item.bbox.top))
    columns: list[tuple[float, float, list[NativeTextCandidate]]] = []
    for candidate in ordered:
        for index, (column_left, column_right, column) in enumerate(columns):
            overlap = max(
                0.0,
                min(column_right, candidate.bbox.right) - max(column_left, candidate.bbox.left),
            )
            narrower = min(column_right - column_left, candidate.bbox.right - candidate.bbox.left)
            if overlap / max(narrower, 0.001) >= _COLUMN_OVERLAP_MIN:
                column.append(candidate)
                columns[index] = (
                    min(column_left, candidate.bbox.left),
                    max(column_right, candidate.bbox.right),
                    column,
                )
                break
        else:
            columns.append((candidate.bbox.left, candidate.bbox.right, [candidate]))
            if len(columns) > 2:
                return fallback()

    left_right = sorted(columns, key=lambda item: item[0])
    if len(left_right) != 2 or left_right[1][0] - left_right[0][1] < _COLUMN_GAP_MIN:
        return fallback()
    return [
        item
        for _, _, column in left_right
        for item in sorted(
            column,
            key=lambda value: (value.bbox.top, value.bbox.left, value.source_index),
        )
    ]


def _column_order(candidates: Sequence[NativeTextCandidate]) -> list[NativeTextCandidate]:
    if len(candidates) < 2:
        return list(candidates)

    wide = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.bbox.right - candidate.bbox.left >= _WIDE_BLOCK_WIDTH_MIN
        ),
        key=lambda item: (item.bbox.top, item.bbox.left, item.source_index),
    )
    if wide:
        narrow = [candidate for candidate in candidates if candidate not in wide]
        result: list[NativeTextCandidate] = []
        for spanning in wide:
            before = [candidate for candidate in narrow if candidate.bbox.top < spanning.bbox.top]
            result.extend(_column_order_narrow(before))
            narrow = [candidate for candidate in narrow if candidate not in before]
            result.append(spanning)
        result.extend(_column_order_narrow(narrow))
        return result
    return _column_order_narrow(candidates)


def _numeric_cell(text: str) -> bool:
    normalized = text.strip().replace(",", "").replace("%", "")
    normalized = normalized.removeprefix("$").removeprefix("¥").removeprefix("€")
    normalized = normalized.strip("()")
    try:
        float(normalized)
    except ValueError:
        return False
    return True


def detect_heuristic_table(words: Sequence[PdfWord]) -> NativeTableCandidate | None:
    if not words or len(words) > _HEURISTIC_TABLE_MAX_WORDS:
        return None
    lines = [line for line in _line_word_groups(words) if len(line) >= 2]
    if len(lines) < _HEURISTIC_TABLE_MIN_ROWS:
        return None

    clusters: list[list[float]] = []
    for word in sorted(words, key=lambda item: item.bbox.left):
        for cluster in clusters:
            anchor = sum(cluster) / len(cluster)
            if abs(word.bbox.left - anchor) <= _HEURISTIC_TABLE_COLUMN_TOLERANCE:
                cluster.append(word.bbox.left)
                break
        else:
            clusters.append([word.bbox.left])
    anchors = sorted(
        sum(cluster) / len(cluster)
        for cluster in clusters
        if len(cluster) >= _HEURISTIC_TABLE_MIN_ROWS
    )
    if not _HEURISTIC_TABLE_MIN_COLUMNS <= len(anchors) <= 8:
        return None

    grid: list[list[str | None]] = []
    for line in lines:
        row: list[list[str]] = [[] for _ in anchors]
        for word in line:
            distances = [abs(word.bbox.left - anchor) for anchor in anchors]
            column = min(range(len(anchors)), key=distances.__getitem__)
            if distances[column] <= _HEURISTIC_TABLE_COLUMN_TOLERANCE:
                row[column].append(word.text)
        cells = [" ".join(parts) if parts else None for parts in row]
        if sum(bool(cell) for cell in cells) >= _HEURISTIC_TABLE_MIN_COLUMNS:
            grid.append(cells)
    if len(grid) < _HEURISTIC_TABLE_MIN_ROWS:
        return None

    data_cells = [cell for row in grid[1:] for cell in row if cell]
    numeric_ratio = sum(_numeric_cell(cell) for cell in data_cells) / max(len(data_cells), 1)
    if numeric_ratio < _HEURISTIC_TABLE_NUMERIC_RATIO_MIN:
        return None

    bbox = BBox(
        min(word.bbox.left for word in words),
        min(word.bbox.top for word in words),
        max(word.bbox.right for word in words),
        max(word.bbox.bottom for word in words),
    )
    candidate = build_table_candidate(
        bbox=bbox,
        raw_grid=grid,
        words=words,
        source_index=0,
    )
    return candidate if is_table_valid(candidate) else None


def build_native_text_lines(
    candidates: Sequence[NativeTextCandidate],
) -> tuple[NativeTextCandidate, ...]:
    words = tuple(
        PdfWord(
            candidate.text,
            candidate.bbox,
            candidate.source_index,
            candidate.font_size,
            candidate.font_name,
        )
        for candidate in candidates
    )
    return tuple(_column_order([_line_candidate(group) for group in _word_groups(words)]))


def _ordered_words(words: Sequence[PdfWord]) -> list[PdfWord]:
    groups = _word_groups(words)
    group_by_source = {
        min(word.source_index for word in group): sorted(
            group,
            key=lambda word: (word.bbox.left, word.source_index),
        )
        for group in groups
    }
    ordered_lines = _column_order([_line_candidate(group) for group in groups])
    return [word for line in ordered_lines for word in group_by_source.get(line.source_index, ())]


def _same_layout_column(left: BBox, right: BBox) -> bool:
    overlap = max(0.0, min(left.right, right.right) - max(left.left, right.left))
    narrower = min(left.right - left.left, right.right - right.left)
    return overlap / max(narrower, 0.001) >= _COLUMN_OVERLAP_MIN


def build_native_candidates(
    words: Sequence[PdfWord], tables: Sequence[NativeTableCandidate]
) -> tuple[NativeCandidate, ...]:
    owned_words = [
        word
        for word in words
        if not any(bbox_contains_center(table.bbox, word.bbox) for table in tables)
        and word.text.strip()
    ]
    ordered: list[NativeCandidate] = [
        NativeTextCandidate(
            word.text,
            word.bbox,
            word.source_index,
            word.font_size,
            word.font_name,
        )
        for word in _ordered_words(owned_words)
    ]
    for table in sorted(
        tables,
        key=lambda candidate: (
            candidate.bbox.top,
            candidate.bbox.left,
            candidate.source_index,
        ),
    ):
        spanning = table.bbox.right - table.bbox.left >= _WIDE_BLOCK_WIDTH_MIN
        if spanning:
            before = [candidate for candidate in ordered if candidate.bbox.bottom <= table.bbox.top]
            after = [candidate for candidate in ordered if candidate not in before]
            ordered = [*before, table, *after]
            continue
        same_column = [
            index
            for index, candidate in enumerate(ordered)
            if _same_layout_column(candidate.bbox, table.bbox)
        ]
        insert_at = next(
            (index for index in same_column if ordered[index].bbox.top >= table.bbox.top),
            same_column[-1] + 1 if same_column else len(ordered),
        )
        ordered.insert(insert_at, table)
    return tuple(ordered)
