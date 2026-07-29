from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox, CoordinateTransform
from opendocs._runtime import ParserRuntime
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.parsers.pdf.analyze import (
    _analyze_page,
    _normalized_box,
    analyze_pdf,
    analyze_pdf_native,
)
from opendocs.parsers.pdf.models import NativeTableCandidate, NativeTextCandidate, PageRoute
from opendocs.parsers.pdf.routing import route_page
from opendocs.source import parse_workspace


class FakeTable:
    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        grid: list[list[str | None]],
    ) -> None:
        self.bbox = bbox
        self._grid = grid

    def extract(self) -> list[list[str | None]]:
        return self._grid


class FakePage:
    width = 100.0
    height = 100.0

    def __init__(
        self,
        *,
        words: list[dict[str, object]] | None = None,
        tables: list[FakeTable] | None = None,
        images: list[dict[str, float]] | None = None,
        lines: list[dict[str, float]] | None = None,
        rects: list[dict[str, float]] | None = None,
        curves: list[dict[str, float]] | None = None,
        media_box: tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0),
        crop_box: tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0),
        rotation: int = 0,
    ) -> None:
        self.mediabox = media_box
        self.cropbox = crop_box
        self.rotation = rotation
        self._words = [] if words is None else words
        self._tables = [] if tables is None else tables
        self.images = [] if images is None else images
        self.lines = [] if lines is None else lines
        self.rects = [] if rects is None else rects
        self.curves = [] if curves is None else curves
        self.closed = False

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        return self._words

    def find_tables(self) -> list[FakeTable]:
        return self._tables

    def close(self) -> None:
        self.closed = True


class FailingProbePage(FakePage):
    @property
    def images(self) -> list[dict[str, float]]:
        raise RuntimeError("image probe failed")

    @images.setter
    def images(self, _value: object) -> None:
        return None


class FakePdf:
    def __init__(self, pages: list[FakePage], *, extractable: bool = True) -> None:
        self.pages = pages
        self.doc = SimpleNamespace(is_extractable=extractable)

    def __enter__(self) -> FakePdf:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _word(
    text: str,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> dict[str, object]:
    return {
        "text": text,
        "x0": left,
        "top": top,
        "x1": right,
        "bottom": bottom,
        "size": 11.0,
        "fontname": "Regular",
    }


def _box(left: float, top: float, right: float, bottom: float) -> dict[str, float]:
    return {"x0": left, "top": top, "x1": right, "bottom": bottom}


def _minimal_pdf(text: str = "hello") -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def test_page_analysis_normalizes_content_from_rotated_negative_crop_box() -> None:
    page = FakePage(
        words=[
            _word("hello", 10, 10, 30, 20),
            _word("a", 35, 30, 45, 40),
            _word("b", 50, 30, 60, 40),
        ],
        tables=[FakeTable((30, 25, 65, 55), [["a", "b"], ["row", "2"]])],
        images=[_box(-5, -10, 15, 10)],
        lines=[_box(65, 50, 75, 60)],
        media_box=(-10.0, -20.0, 90.0, 80.0),
        crop_box=(-5.0, -10.0, 85.0, 70.0),
        rotation=90,
    )

    facts = _analyze_page(page, 1)

    assert facts.media_box == BBox(-10.0, -20.0, 90.0, 80.0)
    assert facts.crop_box == BBox(-5.0, -10.0, 85.0, 70.0)
    assert facts.rotation == 90
    assert facts.display_width == 80.0
    assert facts.display_height == 90.0
    assert facts.words[0].bbox == pytest.approx(BBox(0.625, 1 / 6, 0.75, 7 / 18))
    assert facts.tables[0].bbox == pytest.approx(BBox(0.1875, 7 / 18, 0.5625, 7 / 9))
    assert facts.image_bboxes == pytest.approx((BBox(0.75, 0.0, 1.0, 2 / 9),))
    assert facts.line_bboxes == pytest.approx((BBox(0.125, 7 / 9, 0.25, 8 / 9),))


def test_valid_table_owns_words_and_borders_do_not_create_visual_region() -> None:
    words = [
        _word("name", 20, 20, 35, 25),
        _word("value", 45, 20, 60, 25),
        _word("outside", 10, 70, 30, 75),
    ]
    drawings = [
        _box(12 + index % 10 * 7, 12 + index // 10 * 8, 14 + index % 10 * 7, 14 + index // 10 * 8)
        for index in range(40)
    ]
    page = FakePage(
        words=words,
        tables=[FakeTable((10, 10, 90, 50), [["name", "value"], ["row", "2"]])],
        rects=drawings,
    )

    facts = _analyze_page(page, 1)

    assert len(facts.tables) == 1
    assert [type(candidate) for candidate in facts.native_candidates] == [
        NativeTableCandidate,
        NativeTextCandidate,
    ]
    assert all(region.reasons != ("dense_drawing",) for region in facts.visual_regions)


def test_overlapping_nested_and_invalid_tables_are_deterministic() -> None:
    words = [
        _word("a", 20, 20, 30, 25),
        _word("b", 40, 20, 50, 25),
        _word("c", 20, 30, 30, 35),
        _word("d", 40, 30, 50, 35),
    ]
    page = FakePage(
        words=words,
        tables=[
            FakeTable((10, 10, 90, 60), [["a", "b"], ["c", "d"]]),
            FakeTable((20, 20, 50, 40), [["a", "b"], ["c", "d"]]),
            FakeTable((70, 70, 95, 90), [[None, None]]),
        ],
    )

    facts = _analyze_page(page, 1)

    assert len(facts.tables) == 1
    assert facts.tables[0].bbox == BBox(0.1, 0.1, 0.9, 0.6)
    assert any("table_structure_uncertain" in region.reasons for region in facts.visual_regions)


def test_scanned_image_and_sparse_drawing_pages_are_not_blank() -> None:
    image = _analyze_page(FakePage(images=[_box(1, 1, 99, 99)]), 1)
    drawing = _analyze_page(FakePage(lines=[_box(10, 10, 20, 20)]), 2)

    assert image.image_bboxes == (BBox(0.01, 0.01, 0.99, 0.99),)
    assert drawing.line_bboxes == (BBox(0.1, 0.1, 0.2, 0.2),)
    assert route_page(image).route is PageRoute.FULL_VISION
    assert route_page(drawing).route is PageRoute.FULL_VISION


def test_blank_and_dense_drawing_routes() -> None:
    blank = _analyze_page(FakePage(), 1)
    dense = _analyze_page(
        FakePage(lines=[_box(20 + index * 0.1, 20, 30 + index * 0.1, 30) for index in range(30)]),
        2,
    )

    assert route_page(blank).route is PageRoute.BLANK
    assert any("dense_drawing" in region.reasons for region in dense.visual_regions)
    assert route_page(dense).route is PageRoute.FULL_VISION


def test_page_probe_failure_becomes_full_vision_without_aborting_document() -> None:
    facts = _analyze_page(FailingProbePage(words=[_word("text", 10, 10, 30, 20)]), 1)

    assert facts.native_extraction_failed is True
    assert route_page(facts).route is PageRoute.FULL_VISION


def test_overlapping_words_mark_reading_order_ambiguous() -> None:
    page = FakePage(
        words=[
            _word("left", 10, 10, 60, 30),
            _word("right", 20, 15, 70, 35),
        ]
    )

    facts = _analyze_page(page, 1)

    assert facts.reading_order_ambiguous is True
    assert any("reading_order_ambiguous" in region.reasons for region in facts.visual_regions)
    assert route_page(facts).route is PageRoute.HYBRID


def test_bbox_accepts_negative_coordinates_inside_crop_and_rejects_escape() -> None:
    transform = CoordinateTransform(
        BBox(-20, -10, 80, 90),
        1,
        1,
        media_box=BBox(-30, -20, 100, 100),
    )

    assert _normalized_box((-10, 0, 10, 20), transform) == BBox(0.1, 0.1, 0.3, 0.3)
    with pytest.raises(ValueError, match="outside"):
        _normalized_box((-21, 0, 10, 10), transform)


@pytest.mark.parametrize(
    ("header", "pdf", "error_type"),
    [
        (b"NOPE!", FakePdf([]), CorruptDocumentError),
        (b"%PDF-", FakePdf([], extractable=False), CorruptDocumentError),
        (b"%PDF-", FakePdf([FakePage(), FakePage()]), LimitExceededError),
    ],
)
def test_preflight_maps_corrupt_encrypted_and_page_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    header: bytes,
    pdf: FakePdf,
    error_type: type[Exception],
) -> None:
    path = tmp_path / "input.pdf"
    path.write_bytes(header)
    monkeypatch.setattr("opendocs.parsers.pdf.analyze.pdfplumber.open", lambda _path: pdf)

    with pytest.raises(error_type):
        analyze_pdf_native(path, 1)


@pytest.mark.asyncio
async def test_native_worker_preserves_typed_corrupt_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a PDF")

    async with parse_workspace() as workspace:
        runtime = ParserRuntime(workspace)
        try:
            with pytest.raises(CorruptDocumentError, match="valid PDF"):
                await analyze_pdf(runtime, path, 10)
        finally:
            await runtime.aclose()


@pytest.mark.asyncio
async def test_analysis_runs_through_importable_native_worker(tmp_path: Path) -> None:
    path = tmp_path / "native.pdf"
    path.write_bytes(_minimal_pdf())

    async with parse_workspace() as workspace:
        runtime = ParserRuntime(workspace)
        try:
            result = await analyze_pdf(runtime, path, 10)
        finally:
            await runtime.aclose()

    assert len(result.pages) == 1
    assert result.pages[0].quality.non_whitespace_chars == 5
    assert route_page(result.pages[0]).route is PageRoute.NATIVE


def test_pdfplumber_runtime_failure_maps_to_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-")

    def fail_open(_path: Path) -> object:
        raise RuntimeError("unsafe parser detail")

    monkeypatch.setattr("opendocs.parsers.pdf.analyze.pdfplumber.open", fail_open)

    with pytest.raises(CorruptDocumentError, match="cannot be decoded") as raised:
        analyze_pdf_native(path, 10)

    assert "unsafe parser detail" not in str(raised.value)


def test_native_word_budget_fails_typed_before_reading_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-")
    page = FakePage(words=[_word("x", 10, 10, 20, 20)] * 101)
    monkeypatch.setattr("opendocs.parsers.pdf.analyze._MAX_NATIVE_WORDS", 100)
    reading_order_called = False

    def unexpected_reading_order(_words: object) -> object:
        nonlocal reading_order_called
        reading_order_called = True
        raise AssertionError("reading order must not run after the word limit")

    monkeypatch.setattr(
        "opendocs.parsers.pdf.analyze._reading_order_ambiguity", unexpected_reading_order
    )
    monkeypatch.setattr(
        "opendocs.parsers.pdf.analyze.pdfplumber.open",
        lambda _path: FakePdf([page]),
    )

    with pytest.raises(LimitExceededError, match="word count"):
        analyze_pdf_native(path, 1)

    assert reading_order_called is False
    assert page.closed is True


def test_each_page_cache_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-")
    page = FakePage(words=[_word("hello", 10, 10, 30, 20)])
    monkeypatch.setattr(
        "opendocs.parsers.pdf.analyze.pdfplumber.open",
        lambda _path: FakePdf([page]),
    )

    pages = analyze_pdf_native(path, 1)

    assert len(pages) == 1
    assert page.closed is True
