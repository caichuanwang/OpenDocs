from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest  # pyright: ignore[reportMissingImports]
from PIL import Image  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox, CoordinateTransform, PageBreakBlock, TextBlock
from opendocs._runtime import ParserRuntime
from opendocs.errors import ModelInvalidResponseError, NoUsableContentError, VisionRequiredError
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.pdf.extract import measure_text_quality
from opendocs.parsers.pdf.models import (
    NativeTextCandidate,
    PageFacts,
    PdfAnalysis,
    VisualRegion,
)
from opendocs.parsers.pdf.parser import PDFParser
from opendocs.parsers.pdf.render import RenderedPdfPage
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import VisionRequest, VisionRequestKind, VisionResult, VisionTextElement


def _page(
    number: int,
    *,
    native: tuple[NativeTextCandidate, ...] = (),
    text: str = "",
    regions: tuple[VisualRegion, ...] = (),
    reliable: bool = True,
    failed: bool = False,
) -> PageFacts:
    box = BBox(0.0, 0.0, 100.0, 100.0)
    return PageFacts(
        number,
        box,
        box,
        0,
        100.0,
        100.0,
        (),
        (),
        native,
        regions,
        measure_text_quality(text),
        0.0,
        0,
        failed,
        False,
        reliable,
    )


def _native(text: str, bbox: BBox, index: int = 0) -> NativeTextCandidate:
    return NativeTextCandidate(text, bbox, index)


class FakeRenderer:
    def __init__(self, path: Path, *, transform: CoordinateTransform | None = None) -> None:
        self.path = path
        self.calls: list[int] = []
        self.close_calls = 0
        self.transform = transform or CoordinateTransform(BBox(0, 0, 100, 100), 100, 100)

    @asynccontextmanager
    async def render_page(
        self,
        pdf_path: Path,
        page: PageFacts,
        *,
        deadline: float,
        use_crop_box: bool = True,
    ) -> AsyncIterator[RenderedPdfPage]:
        del pdf_path, deadline, use_crop_box
        self.calls.append(page.page_number)
        image_path = self.path / f"fake-page-{page.page_number}.png"
        Image.new("RGB", (100, 100), "white").save(image_path, "PNG")
        try:
            yield RenderedPdfPage(image_path, self.transform)
        finally:
            image_path.unlink(missing_ok=True)

    async def aclose(self) -> None:
        self.close_calls += 1


class RecordingVision:
    def __init__(self, results: dict[int, VisionResult | BaseException]) -> None:
        self.results = results
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.requests.append(request)
        assert request.image_path.exists()
        result = self.results[request.source_index]
        if isinstance(result, BaseException):
            raise result
        return result


async def _parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pages: tuple[PageFacts, ...],
    vision: RecordingVision | None,
    renderer: FakeRenderer,
) -> tuple[PDFParser, ParserRuntime, ResolvedSource]:
    async def fake_analyze(*_args: object, **_kwargs: object) -> PdfAnalysis:
        return PdfAnalysis(pages)

    monkeypatch.setattr("opendocs.parsers.pdf.parser.analyze_pdf", fake_analyze)
    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"%PDF-")
    parser = PDFParser(
        runtime,
        vision,
        VisionConfig("model") if vision is not None else None,
        renderer=renderer,
    )
    return parser, runtime, ResolvedSource(source_path, source_path.name, False)


@pytest.mark.asyncio
async def test_parser_aclose_closes_injected_renderer_once_and_rejects_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(_native("native", BBox(0.1, 0.1, 0.2, 0.2)),), text="native"),),
        None,
        renderer,
    )
    try:
        await asyncio.gather(parser.aclose(), parser.aclose())
        with pytest.raises(RuntimeError, match="closed"):
            await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert renderer.close_calls == 1


@pytest.mark.asyncio
async def test_native_and_blank_pages_never_render_or_call_vision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = _native("native", BBox(0.1, 0.1, 0.2, 0.2))
    renderer = FakeRenderer(tmp_path)
    vision = RecordingVision({})
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(native,), text="native"), _page(2)),
        vision,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert renderer.calls == []
    assert vision.requests == []
    assert result.blocks == (PageBreakBlock(1), TextBlock("native"), PageBreakBlock(2))
    assert [warning.code for warning in result.warnings] == ["blank_page"]


@pytest.mark.asyncio
async def test_all_blank_is_no_content_without_visual_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(tmp_path, monkeypatch, (_page(1),), None, renderer)
    try:
        with pytest.raises(NoUsableContentError):
            await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()
    assert renderer.calls == []


@pytest.mark.asyncio
async def test_hybrid_crop_maps_coordinates_and_suppresses_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    region = VisualRegion(BBox(0.203, 0.207, 0.597, 0.601), ("image",), 0)
    native = (
        _native("duplicate", BBox(0.3, 0.3, 0.4, 0.4), 0),
        _native("floor-ceil edge", BBox(0.598, 0.603, 0.599, 0.609), 1),
        _native("kept", BBox(0.7, 0.7, 0.8, 0.8), 2),
    )
    vision = RecordingVision(
        {10_000: VisionResult((VisionTextElement("visual", 0, BBox(0, 0, 1, 1)),))}
    )
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=native, text="native", regions=(region,)),),
        vision,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert vision.requests[0].kind is VisionRequestKind.HYBRID_CROP
    assert vision.requests[0].coordinate_space == "crop-normalized-v1"
    assert result.blocks == (
        PageBreakBlock(1),
        TextBlock("visual"),
        TextBlock("kept"),
    )
    assert not tuple(tmp_path.glob("pdf-crop-*"))


class UnreliableTransform(CoordinateTransform):
    def page_to_pixels(self, bbox: BBox) -> tuple[int, int, int, int]:
        del bbox
        raise ValueError("not invertible")


@pytest.mark.asyncio
async def test_unreliable_hybrid_crop_upgrades_to_full_vision_sole_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    region = VisualRegion(BBox(0.2, 0.2, 0.6, 0.6), ("image",), 0)
    native = _native("discarded", BBox(0.7, 0.7, 0.8, 0.8))
    vision = RecordingVision({0: VisionResult((VisionTextElement("full", 0),))})
    renderer = FakeRenderer(
        tmp_path,
        transform=UnreliableTransform(BBox(0, 0, 100, 100), 100, 100),
    )
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(native,), text="native", regions=(region,)),),
        vision,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert [request.kind for request in vision.requests] == [VisionRequestKind.FULL_PAGE]
    assert result.blocks == (PageBreakBlock(1), TextBlock("full"))


@pytest.mark.asyncio
async def test_visual_completion_order_does_not_change_page_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = asyncio.Event()

    class OutOfOrderVision(RecordingVision):
        async def analyze(self, request: VisionRequest) -> VisionResult:
            self.requests.append(request)
            if request.source_index == 0:
                await release.wait()
            else:
                release.set()
            return VisionResult((VisionTextElement(f"page {request.source_index + 1}", 0),))

    vision = OutOfOrderVision({})
    pages = (
        _page(1, regions=(VisualRegion(BBox(0, 0, 1, 1), ("image",), 0),)),
        _page(2, regions=(VisualRegion(BBox(0, 0, 1, 1), ("image",), 0),)),
    )
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(tmp_path, monkeypatch, pages, vision, renderer)
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert result.blocks == (
        PageBreakBlock(1),
        TextBlock("page 1"),
        PageBreakBlock(2),
        TextBlock("page 2"),
    )


@pytest.mark.asyncio
async def test_hybrid_region_partial_failure_keeps_successful_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    regions = (
        VisualRegion(BBox(0.1, 0.1, 0.3, 0.3), ("image",), 0),
        VisualRegion(BBox(0.5, 0.5, 0.7, 0.7), ("image",), 1),
    )
    native = _native("kept", BBox(0.8, 0.8, 0.9, 0.9))
    vision = RecordingVision(
        {
            10_000: VisionResult((VisionTextElement("success", 0, BBox(0, 0, 1, 1)),)),
            10_001: ModelInvalidResponseError("bad second region"),
        }
    )
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(native,), text="native", regions=regions),),
        vision,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert result.blocks == (
        PageBreakBlock(1),
        TextBlock("success"),
        TextBlock("kept"),
    )
    assert [warning.code for warning in result.warnings] == ["visual_region_failed"]
    assert "region 1" in result.warnings[0].message


@pytest.mark.asyncio
async def test_partial_visual_failure_preserves_native_with_stable_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    region = VisualRegion(BBox(0.2, 0.2, 0.4, 0.4), ("image",), 0)
    native = _native("kept", BBox(0.7, 0.7, 0.8, 0.8))
    vision = RecordingVision({10_000: ModelInvalidResponseError("unsafe private detail")})
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(native,), text="native", regions=(region,)),),
        vision,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert result.blocks == (PageBreakBlock(1), TextBlock("kept"))
    assert [warning.code for warning in result.warnings] == ["visual_processing_failed"]
    assert "private" not in result.warnings[0].message


@pytest.mark.asyncio
async def test_full_page_failure_preserves_native_or_is_typed_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    region = VisualRegion(BBox(0, 0, 1, 1), ("image",), 0)
    native = _native("fallback", BBox(0.1, 0.1, 0.2, 0.2))
    vision = RecordingVision({0: ModelInvalidResponseError("unsafe model payload")})
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(native,), text="native", regions=(region,), reliable=False),),
        vision,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert result.blocks == (PageBreakBlock(1), TextBlock("fallback"))
    assert [warning.code for warning in result.warnings] == ["visual_processing_failed"]

    vision = RecordingVision({0: ModelInvalidResponseError("unsafe model payload")})
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, regions=(region,)),),
        vision,
        renderer,
    )
    try:
        with pytest.raises(ModelInvalidResponseError):
            await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_no_vision_preserves_native_but_visual_only_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    region = VisualRegion(BBox(0.2, 0.2, 0.4, 0.4), ("image",), 0)
    native = _native("kept", BBox(0.7, 0.7, 0.8, 0.8))
    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, native=(native,), text="native", regions=(region,)),),
        None,
        renderer,
    )
    try:
        result = await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()

    assert result.blocks == (PageBreakBlock(1), TextBlock("kept"))
    assert [warning.code for warning in result.warnings] == ["vision_unavailable_native_only"]
    assert renderer.calls == []

    renderer = FakeRenderer(tmp_path)
    parser, runtime, source = await _parser(
        tmp_path,
        monkeypatch,
        (_page(1, regions=(region,)),),
        None,
        renderer,
    )
    try:
        with pytest.raises(VisionRequiredError):
            await parser.parse(source, options=ParseOptions())
    finally:
        await runtime.aclose()
    assert renderer.calls == []
