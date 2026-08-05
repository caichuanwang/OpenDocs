from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox
from opendocs.parsers.pdf.extract import (
    build_native_candidates,
    build_native_text_lines,
    build_table_candidate,
    is_text_reliable,
    measure_text_quality,
    normalize_grid,
    select_canonical_tables,
)
from opendocs.parsers.pdf.models import NativeTableCandidate, NativeTextCandidate, PdfWord


def _word(
    text: str,
    bbox: tuple[float, float, float, float],
    index: int,
    *,
    font_size: float | None = None,
    font_name: str | None = None,
) -> PdfWord:
    return PdfWord(text, BBox(*bbox), index, font_size, font_name)


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


def test_nested_tables_with_distinct_text_are_both_preserved() -> None:
    outer = _table(
        (0.1, 0.1, 0.9, 0.9),
        [["outer", "summary"], ["total", "100"]],
        0,
    )
    inner = _table(
        (0.3, 0.3, 0.5, 0.5),
        [["inner", "matrix"], ["x", "y"]],
        1,
    )

    selected = select_canonical_tables((outer, inner))

    assert selected == (outer, inner)


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


@pytest.mark.parametrize(
    "grid",
    (
        [["boxed heading"]],
        [["label", "value"]],
        [["label"], ["value"]],
    ),
)
def test_one_dimensional_table_is_rejected_as_text_fallback(
    grid: list[list[str | None]],
) -> None:
    text = " ".join(cell for row in grid for cell in row if cell is not None)
    word = _word(text, (0.2, 0.2, 0.4, 0.3), 0)
    candidate = _table((0.1, 0.1, 0.6, 0.4), grid, 0, [word])

    accepted = select_canonical_tables((candidate,))

    assert accepted == ()
    assert build_native_candidates((word,), accepted) == (NativeTextCandidate(text, word.bbox, 0),)


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


def test_native_candidates_group_words_into_lines_and_preserve_font_metadata() -> None:
    words = (
        _word("Open", (0.1, 0.1, 0.2, 0.15), 0, font_size=18, font_name="Inter-Bold"),
        _word("Docs", (0.21, 0.1, 0.32, 0.15), 1, font_size=18, font_name="Inter-Bold"),
        _word("下一行", (0.1, 0.2, 0.3, 0.25), 2, font_size=10, font_name="NotoSansCJK"),
    )

    word_candidates = build_native_candidates(words, ())
    assert len(word_candidates) == 3

    candidates = build_native_text_lines(
        tuple(
            candidate for candidate in word_candidates if isinstance(candidate, NativeTextCandidate)
        )
    )

    assert candidates == (
        NativeTextCandidate(
            "Open Docs",
            BBox(0.1, 0.1, 0.32, 0.15),
            0,
            18.0,
            "Inter-Bold",
        ),
        NativeTextCandidate(
            "下一行",
            BBox(0.1, 0.2, 0.3, 0.25),
            2,
            10.0,
            "NotoSansCJK",
        ),
    )


def test_native_candidate_order_reads_columns_before_advancing_right() -> None:
    words = (
        _word("左一", (0.05, 0.1, 0.35, 0.15), 0),
        _word("右一", (0.65, 0.1, 0.95, 0.15), 1),
        _word("左二", (0.05, 0.2, 0.35, 0.25), 2),
        _word("右二", (0.65, 0.2, 0.95, 0.25), 3),
    )

    raw_candidates = build_native_candidates(words, ())
    candidates = build_native_text_lines(
        tuple(
            candidate for candidate in raw_candidates if isinstance(candidate, NativeTextCandidate)
        )
    )

    assert [candidate.text for candidate in candidates] == ["左一", "左二", "右一", "右二"]


def test_native_candidate_order_keeps_wide_header_and_footer_around_columns() -> None:
    words = (
        _word("标题", (0.05, 0.02, 0.95, 0.07), 0),
        _word("左一", (0.05, 0.15, 0.35, 0.20), 1),
        _word("右一", (0.65, 0.15, 0.95, 0.20), 2),
        _word("左二", (0.05, 0.30, 0.35, 0.35), 3),
        _word("右二", (0.65, 0.30, 0.95, 0.35), 4),
        _word("脚注", (0.05, 0.90, 0.95, 0.95), 5),
    )

    raw_candidates = build_native_candidates(words, ())
    candidates = build_native_text_lines(
        tuple(
            candidate for candidate in raw_candidates if isinstance(candidate, NativeTextCandidate)
        )
    )

    assert [candidate.text for candidate in candidates] == [
        "标题",
        "左一",
        "左二",
        "右一",
        "右二",
        "脚注",
    ]


def test_three_column_layout_uses_stable_geometric_fallback() -> None:
    words = (
        _word("one", (0.02, 0.1, 0.17, 0.15), 0),
        _word("two", (0.42, 0.1, 0.57, 0.15), 1),
        _word("three", (0.82, 0.1, 0.97, 0.15), 2),
        _word("next", (0.02, 0.2, 0.17, 0.25), 3),
    )
    raw_candidates = build_native_candidates(words, ())

    candidates = build_native_text_lines(
        tuple(
            candidate for candidate in raw_candidates if isinstance(candidate, NativeTextCandidate)
        )
    )

    assert [candidate.text for candidate in candidates] == ["one", "two", "three", "next"]


def test_table_is_inserted_within_its_reading_order_column() -> None:
    words = (
        _word("left one", (0.05, 0.1, 0.35, 0.15), 0),
        _word("right one", (0.65, 0.1, 0.95, 0.15), 1),
        _word("left two", (0.05, 0.3, 0.35, 0.35), 2),
        _word("right two", (0.65, 0.3, 0.95, 0.35), 3),
    )
    table = _table(
        (0.62, 0.18, 0.98, 0.28),
        [["A", "B"], ["1", "2"]],
        0,
    )

    candidates = build_native_candidates(words, (table,))

    assert [
        candidate.text if isinstance(candidate, NativeTextCandidate) else "<table>"
        for candidate in candidates
    ] == ["left one", "left two", "right one", "<table>", "right two"]


def test_spanning_table_splits_column_run_at_its_vertical_band() -> None:
    words = (
        _word("left one", (0.05, 0.1, 0.35, 0.15), 0),
        _word("right one", (0.65, 0.1, 0.95, 0.15), 1),
        _word("left two", (0.05, 0.3, 0.35, 0.35), 2),
        _word("right two", (0.65, 0.3, 0.95, 0.35), 3),
    )
    table = _table((0.1, 0.18, 0.9, 0.28), [["A", "B"], ["1", "2"]], 0)

    candidates = build_native_candidates(words, (table,))

    assert [
        candidate.text if isinstance(candidate, NativeTextCandidate) else "<table>"
        for candidate in candidates
    ] == ["left one", "right one", "<table>", "left two", "right two"]


def test_text_quality_flags_unicode_noncharacters_as_bad_encoding() -> None:
    text = "valid\ufdd0broken"
    quality = measure_text_quality(text)

    assert quality.bad_char_ratio > 0
    assert not is_text_reliable(text, (_word(text, (0.1, 0.1, 0.8, 0.2), 0),), False)


@pytest.mark.parametrize(
    "bad_text",
    (
        "a\ud800b",
        "a\ufffeb",
        "a\uffffb",
        "a\U0001fffeb",
        "a\U0001ffffb",
        "a\ufdefb",
    ),
)
def test_text_quality_flags_noncharacters_across_unicode_planes(
    bad_text: str,
) -> None:
    quality = measure_text_quality(bad_text)

    assert quality.bad_char_ratio > 0
    assert not is_text_reliable(
        bad_text,
        (_word(bad_text, (0.1, 0.1, 0.8, 0.2), 0),),
        False,
    )


def test_text_quality_accepts_valid_unicode() -> None:
    text = "emoji\U0001f600 中文 拡張 平仮名\u0301\U0000200d PUA\ue000"

    assert is_text_reliable(
        text,
        (_word(text, (0.1, 0.1, 0.8, 0.2), 0),),
        False,
    )
