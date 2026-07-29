from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox
from opendocs.parsers.pdf.extract import (
    build_native_candidates,
    build_table_candidate,
    is_text_reliable,
    measure_text_quality,
    normalize_grid,
    select_canonical_tables,
)
from opendocs.parsers.pdf.models import NativeTableCandidate, NativeTextCandidate, PdfWord


def _word(text: str, bbox: tuple[float, float, float, float], index: int) -> PdfWord:
    return PdfWord(text, BBox(*bbox), index)


def _table(
    bbox: tuple[float, float, float, float],
    grid: list[list[str | None]],
    index: int,
    words: list[PdfWord] | None = None,
) -> NativeTableCandidate:
    if words is None:
        words = [
            _word(cell or "", bbox, cell_index)
            for cell_index, cell in enumerate(cell for row in grid for cell in row)
            if cell
        ]
    return build_table_candidate(
        bbox=BBox(*bbox),
        raw_grid=grid,
        words=words,
        source_index=index,
    )


def test_text_quality_and_reliability_match_native_reference_edges() -> None:
    word = _word("usable", (0.1, 0.1, 0.2, 0.2), 0)

    assert is_text_reliable("正常的原生文本" * 10, [word], False) is True
    assert is_text_reliable("A" * 40, [word], False) is False
    assert is_text_reliable("┌" + "─" * 80 + "┐\n│ 产品架构与商业模式 │", [word], False) is True
    assert is_text_reliable("正常文本���", [word], False) is False
    assert is_text_reliable("(cid:123)" * 10, [word], False) is False
    assert is_text_reliable("normal", [word], True) is False
    assert measure_text_quality("甲 乙\n�").bad_char_ratio == pytest.approx(1 / 3)


def test_normalize_grid_strips_and_pads_without_converting_values() -> None:
    assert normalize_grid([[" A ", ""], ["B"], [None, " 3 "]]) == (
        ("A", ""),
        ("B", None),
        (None, "3"),
    )


def test_table_candidate_preserves_explicit_multirow_header_metadata() -> None:
    words = [_word("value", (0.1, 0.1, 0.8, 0.8), 0)]
    candidate = build_table_candidate(
        bbox=BBox(0.1, 0.1, 0.8, 0.8),
        raw_grid=[["group", "year"], [None, "value"], ["row", "1"]],
        words=words,
        source_index=0,
        header_rows=2,
    )

    assert candidate.header_rows == 2
    assert candidate.grid[1] == (None, "value")
    with pytest.raises(ValueError, match="header"):
        build_table_candidate(
            bbox=candidate.bbox,
            raw_grid=[["only"]],
            words=words,
            source_index=0,
            header_rows=2,
        )


def test_canonical_selection_prefers_more_cells_and_removes_nested_and_high_iou() -> None:
    outer = _table((0.1, 0.1, 0.8, 0.8), [["a", "b"], ["c", "d"]], 1)
    nested = _table((0.2, 0.2, 0.5, 0.5), [["a", "b"], ["c", "d"]], 0)
    richer = _table(
        (0.11, 0.11, 0.81, 0.81),
        [["a", "b", "c"], ["d", "e", "f"]],
        2,
    )
    adjacent = _table((0.82, 0.1, 0.98, 0.8), [["x", "y"], ["1", "2"]], 3)

    selected = select_canonical_tables((outer, nested, richer, adjacent))

    assert [table.source_index for table in selected] == [3, 2]


def test_richer_inner_table_suppresses_later_containing_outer_below_iou_threshold() -> None:
    inner = _table(
        (0.30, 0.30, 0.50, 0.50),
        [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]],
        0,
    )
    outer = _table((0.10, 0.10, 0.90, 0.90), [["a", "b"], ["c", "d"]], 1)

    selected = select_canonical_tables((outer, inner))

    assert selected == (inner,)


def test_distinct_partial_overlap_below_threshold_remains_separate() -> None:
    left = _table((0.1, 0.1, 0.6, 0.6), [["a", "b"], ["c", "d"]], 0)
    right = _table((0.5, 0.1, 0.9, 0.6), [["e", "f"], ["g", "h"]], 1)

    assert len(select_canonical_tables((left, right))) == 2


def test_invalid_table_words_fall_back_to_native_text() -> None:
    word = _word("fallback", (0.2, 0.2, 0.3, 0.3), 0)
    invalid = _table(
        (0.1, 0.1, 0.6, 0.6),
        [[None, None]],
        0,
        [word],
    )

    accepted = select_canonical_tables((invalid,))
    candidates = build_native_candidates((word,), accepted)

    assert accepted == ()
    assert candidates == (NativeTextCandidate("fallback", word.bbox, 0),)


def test_accepted_table_owns_internal_words_and_rejected_table_words_fall_back() -> None:
    inside = _word("inside", (0.2, 0.2, 0.3, 0.3), 0)
    outside = _word("outside", (0.1, 0.8, 0.3, 0.9), 1)
    accepted = _table(
        (0.1, 0.1, 0.6, 0.6),
        [["inside", "value"], ["row", "2"]],
        0,
        [inside],
    )

    candidates = build_native_candidates((inside, outside), (accepted,))

    assert [type(item) for item in candidates] == [NativeTableCandidate, NativeTextCandidate]
    assert all(
        not isinstance(item, NativeTextCandidate) or item.text != "inside" for item in candidates
    )
    assert isinstance(candidates[-1], NativeTextCandidate) and candidates[-1].text == "outside"
