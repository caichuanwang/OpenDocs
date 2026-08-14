from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image

import opendocs.parsers.xlsx.parser as parser_module
from opendocs._models import (
    DocumentType,
    HeadingBlock,
    InlineText,
    MarkdownBlock,
    ParagraphBlock,
    TextBlock,
)
from opendocs._runtime import ParserRuntime
from opendocs.errors import (
    DocumentTimeoutError,
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelInvalidResponseError,
    ModelPermissionError,
    ModelUnavailableError,
    RuntimeDependencyError,
)
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.xlsx.media import build_xlsx_visual_requests
from opendocs.parsers.xlsx.models import (
    XlsxChartSlot,
    XlsxDocument,
    XlsxImageSlot,
    XlsxNativeSlot,
    XlsxSheet,
    XlsxSheetKind,
    XlsxSheetState,
    document_to_wire,
)
from opendocs.parsers.xlsx.parser import XlsxParser, _extract_xlsx_to_wire
from opendocs.parsers.xlsx.preflight import XlsxPreflight
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import VisionRequest, VisionResult, VisionTextElement
from tests.xlsx_fixtures import write_structured_xlsx


class RecordingVision:
    def __init__(self, result: object | BaseException | None = None) -> None:
        self.requests: list[VisionRequest] = []
        self.result = result

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result is not None:
            return cast(VisionResult, self.result)
        return VisionResult((VisionTextElement("视觉解释: 收入总体上升", request.source_index),))


class FatalVisionFailure(BaseException):
    pass


def _document_with_images(*, duplicate: bool = False) -> XlsxDocument:
    first = XlsxImageSlot(1, "B2", "same.png", "b" * 64, alt_text="First")
    second = XlsxImageSlot(1, "C3", "same.png", "b" * 64, alt_text="Second")
    sheets = [
        XlsxSheet(
            1,
            "One",
            XlsxSheetKind.WORKSHEET,
            XlsxSheetState.VISIBLE,
            (
                XlsxNativeSlot(
                    0,
                    "A1",
                    (
                        MarkdownBlock("<!-- xlsx-sheet: 1 -->"),
                        HeadingBlock(1, (InlineText("One"),)),
                    ),
                ),
                first,
            ),
        )
    ]
    if duplicate:
        sheets.append(
            XlsxSheet(
                2,
                "Two",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    XlsxNativeSlot(
                        0,
                        "A1",
                        (
                            MarkdownBlock("<!-- xlsx-sheet: 2 -->"),
                            HeadingBlock(1, (InlineText("Two"),)),
                        ),
                    ),
                    second,
                ),
            )
        )
    return XlsxDocument(tuple(sheets))


def _runtime_with_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    document: XlsxDocument | object,
) -> tuple[ParserRuntime, list[str]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact_names = (
        {
            slot.artifact_name
            for sheet in document.sheets
            for slot in sheet.slots
            if isinstance(slot, XlsxImageSlot | XlsxChartSlot)
        }
        if isinstance(document, XlsxDocument)
        else set()
    )
    for artifact_name in artifact_names or {"same.png"}:
        (workspace / artifact_name).write_bytes(b"raw")
    runtime = ParserRuntime(ParseWorkspace(workspace))
    calls: list[str] = []

    async def run_native(function: Any, *args: object, **kwargs: object) -> object:
        del kwargs
        calls.append(function.__name__)
        if function.__name__ == "_extract_xlsx_to_wire":
            return document_to_wire(document) if isinstance(document, XlsxDocument) else document
        if function.__name__ == "_prepare_xlsx_visual_to_wire":
            output_directory = args[-2]
            output_stem = args[-1]
            assert isinstance(output_directory, Path)
            assert isinstance(output_stem, str)
            name = f"{output_stem}-0.png"
            (output_directory / name).write_bytes(b"sanitized")
            return {
                "skipped": False,
                "reason": None,
                "width": 20,
                "height": 10,
                "parts": [
                    {
                        "name": name,
                        "top": 0.0,
                        "bottom": 1.0,
                        "core_top": 0.0,
                        "core_bottom": 1.0,
                        "width": 20,
                        "height": 10,
                    }
                ],
                "facts": {
                    "alpha_coverage": 1.0,
                    "components": 1,
                    "edge_density": 0.5,
                    "color_count": 8,
                    "nearly_blank": False,
                },
            }
        raise AssertionError(f"unexpected native function: {function.__name__}")

    monkeypatch.setattr(runtime, "run_native", run_native)
    return runtime, calls


async def _parse(parser: XlsxParser, tmp_path: Path, options: ParseOptions | None = None):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")
    return await parser.parse(
        ResolvedSource(source, source.name, False),
        options=options or ParseOptions(),
    )


@pytest.mark.asyncio
async def test_xlsx_visual_request_seam_uses_fake_client_and_bounded_chart_prompt(
    tmp_path: Path,
) -> None:
    artifact_name = "chart.png"
    image = Image.new("RGB", (32, 16), "white")
    try:
        image.save(tmp_path / artifact_name, "PNG")
    finally:
        image.close()
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "Data",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    XlsxChartSlot(
                        1,
                        "D2",
                        artifact_name,
                        "a" * 64,
                        (HeadingBlock(2, (InlineText("Revenue"),)),),
                    ),
                ),
            ),
        )
    )
    specs = build_xlsx_visual_requests(document, tmp_path)
    vision = RecordingVision()

    result = await vision.analyze(specs[0].to_vision_request())

    assert result.elements == (VisionTextElement("视觉解释: 收入总体上升", 0),)
    assert len(vision.requests) == 1
    assert vision.requests[0].image_path == tmp_path / artifact_name
    assert "视觉解释" in vision.requests[0].prompt
    assert "Excel 外观还原" in vision.requests[0].prompt


def test_native_worker_preflights_before_extract_and_strictly_serializes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    write_structured_xlsx(
        source,
        sheets=(("Data", "worksheet", "visible", "A1", ("A1",)),),
    )
    events: list[str] = []
    real_preflight = parser_module.preflight_xlsx
    real_extract = parser_module.extract_xlsx

    def record_preflight(path: Path):
        events.append("preflight")
        return real_preflight(path)

    def record_extract(path: Path, preflight: XlsxPreflight, *, artifact_dir: Path):
        events.append("extract")
        return real_extract(path, preflight, artifact_dir=artifact_dir)

    monkeypatch.setattr(parser_module, "preflight_xlsx", record_preflight)
    monkeypatch.setattr(parser_module, "extract_xlsx", record_extract)

    wire = _extract_xlsx_to_wire(source, tmp_path / "artifacts")

    assert wire["type"] == "xlsx_document"
    assert events == ["preflight", "extract"]


@pytest.mark.asyncio
async def test_parser_deduplicates_visual_work_and_replays_success_per_occurrence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = _document_with_images(duplicate=True)
    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(VisionResult((VisionTextElement("visual", 0),)))
    try:
        result = await _parse(
            XlsxParser(runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert [block for block in result.blocks if block == TextBlock("visual")] == [
        TextBlock("visual"),
        TextBlock("visual"),
    ]
    assert len(vision.requests) == 1
    assert calls == ["_extract_xlsx_to_wire", "_prepare_xlsx_visual_to_wire"]
    assert not (tmp_path / "workspace" / "same.png").exists()
    assert not tuple((tmp_path / "workspace").glob("xlsx-prepared-*.png"))


@pytest.mark.asyncio
async def test_parser_without_vision_returns_native_and_occurrence_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, _document_with_images())
    try:
        result = await _parse(XlsxParser(runtime, None, None), tmp_path)
    finally:
        await runtime.aclose()

    assert result.document_type is DocumentType.XLSX
    assert [warning.code for warning in result.warnings] == ["xlsx_vision_unavailable"]
    assert "One!B2" in result.warnings[0].message
    assert calls == ["_extract_xlsx_to_wire"]
    assert not (tmp_path / "workspace" / "same.png").exists()


@pytest.mark.asyncio
async def test_parser_partial_failure_is_replayed_in_anchor_order_not_completion_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    slow_digest = "d" * 64
    failed_digest = "e" * 64
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "Mixed",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    XlsxNativeSlot(
                        0,
                        "A1",
                        (
                            MarkdownBlock("<!-- xlsx-sheet: 1 -->"),
                            HeadingBlock(1, (InlineText("Mixed"),)),
                        ),
                    ),
                    XlsxImageSlot(1, "A5", "slow.png", slow_digest, alt_text="Slow"),
                    XlsxImageSlot(2, "A2", "failed.png", failed_digest, alt_text="Failed"),
                ),
            ),
        )
    )

    class ReverseCompletionVision:
        async def analyze(self, request: VisionRequest) -> VisionResult:
            if request.source_index == 0:
                await asyncio.sleep(0.02)
                return VisionResult((VisionTextElement("slow success", 0),))
            raise ModelUnavailableError("fast failure")

    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, document)
    try:
        result = await _parse(
            XlsxParser(runtime, ReverseCompletionVision(), VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    failed_metadata = ParagraphBlock((InlineText("Image description: Failed"),))
    slow_metadata = ParagraphBlock((InlineText("Image description: Slow"),))
    assert result.blocks.index(failed_metadata) < result.blocks.index(slow_metadata)
    assert TextBlock("slow success") in result.blocks
    assert [warning.code for warning in result.warnings] == ["xlsx_vision_failed"]
    assert "Mixed!A2" in result.warnings[0].message
    assert calls == [
        "_extract_xlsx_to_wire",
        "_prepare_xlsx_visual_to_wire",
        "_prepare_xlsx_visual_to_wire",
    ]
    assert not tuple((tmp_path / "workspace").glob("*.png"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ModelAuthenticationError("auth"),
        ModelPermissionError("permission"),
        ModelInvalidRequestError("invalid"),
        ModelUnavailableError("provider"),
        ModelInvalidResponseError("invalid response"),
        RuntimeDependencyError("runtime"),
        ValueError("plain provider error"),
        FatalVisionFailure("base exception"),
        object(),
        VisionResult(()),
    ],
)
async def test_parser_fails_open_for_every_non_timeout_visual_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: object,
) -> None:
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, _document_with_images())
    vision = RecordingVision(failure)
    try:
        result = await _parse(
            XlsxParser(runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert [warning.code for warning in result.warnings] == ["xlsx_vision_failed"]
    assert HeadingBlock(1, (InlineText("One (Visible)"),)) in result.blocks


@pytest.mark.asyncio
async def test_parser_classifies_per_object_timeout_but_document_deadline_is_fatal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class SlowVision:
        async def analyze(self, request: VisionRequest) -> VisionResult:
            del request
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, _document_with_images())
    try:
        result = await _parse(
            XlsxParser(runtime, SlowVision(), VisionConfig("model", timeout=0.01)),
            tmp_path,
            ParseOptions(timeout=1),
        )
        assert [warning.code for warning in result.warnings] == ["xlsx_vision_timeout"]
    finally:
        await runtime.aclose()

    second_path = tmp_path / "deadline"
    second_path.mkdir()
    runtime, _ = _runtime_with_document(monkeypatch, second_path, _document_with_images())

    async def slow_native(function: Any, *args: object, **kwargs: object) -> object:
        del function, args, kwargs
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    monkeypatch.setattr(runtime, "run_native", slow_native)
    try:
        with pytest.raises(DocumentTimeoutError):
            await _parse(
                XlsxParser(runtime, None, None),
                second_path,
                ParseOptions(timeout=0.01),
            )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_parser_propagates_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, _document_with_images())

    async def cancelled(function: Any, *args: object, **kwargs: object) -> object:
        del function, args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "run_native", cancelled)
    try:
        with pytest.raises(asyncio.CancelledError):
            await _parse(XlsxParser(runtime, None, None), tmp_path)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", [{"type": "wrong"}, object()])
async def test_parser_maps_invalid_native_wire_to_runtime_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wire: object,
) -> None:
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, wire)
    try:
        with pytest.raises(RuntimeDependencyError, match="invalid data"):
            await _parse(XlsxParser(runtime, None, None), tmp_path)
    finally:
        await runtime.aclose()
