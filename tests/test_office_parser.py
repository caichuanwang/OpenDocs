from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opendocs._models import BBox, DocumentType, PageBreakBlock, TextBlock
from opendocs._runtime import ParserRuntime
from opendocs.errors import (
    LimitExceededError,
    ModelAuthenticationError,
    ModelUnavailableError,
    NoUsableContentError,
    VisionRequiredError,
)
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.office.models import (
    ImageSlot,
    NativeSlot,
    OfficeDocument,
    OfficePage,
    document_to_wire,
)
from opendocs.parsers.office.parser import OfficeParser
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import VisionRequest, VisionRequestKind, VisionResult, VisionTextElement


class RecordingVision:
    def __init__(self, result: VisionResult | BaseException) -> None:
        self.result = result
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _image(source_index: int, digest: str = "a" * 64) -> ImageSlot:
    return ImageSlot(source_index, "embedded.png", digest, BBox(0, 0, 1, 1))


def _runtime_with_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    document: OfficeDocument,
    *,
    size: tuple[int, int] = (20, 10),
) -> tuple[ParserRuntime, list[str]]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "embedded.png").write_bytes(b"embedded")
    runtime = ParserRuntime(ParseWorkspace(workspace_path))
    calls: list[str] = []

    async def run_native(function: Any, *args: object, **kwargs: object) -> object:
        del kwargs
        calls.append(function.__name__)
        if function.__name__ == "_extract_office_to_wire":
            return document_to_wire(document)
        if function.__name__ == "_sanitize_embedded_image":
            output_path = args[1]
            assert isinstance(output_path, Path)
            output_path.write_bytes(b"sanitized")
            return size
        raise AssertionError(f"unexpected native function: {function.__name__}")

    monkeypatch.setattr(runtime, "run_native", run_native)
    return runtime, calls


async def _parse(
    parser: OfficeParser,
    tmp_path: Path,
    *,
    options: ParseOptions | None = None,
):
    source = tmp_path / "source.office"
    source.write_bytes(b"source")
    return await parser.parse(
        ResolvedSource(source, source.name, False),
        options=options or ParseOptions(),
    )


@pytest.mark.asyncio
async def test_office_parser_native_only_preserves_slots_without_model_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (OfficePage(1, (NativeSlot(0, (TextBlock("native"),)),)),),
    )
    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(VisionResult((VisionTextElement("unused", 0),)))
    try:
        result = await _parse(
            OfficeParser(DocumentType.DOCX, runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert result.blocks == (TextBlock("native"),)
    assert vision.requests == []
    assert calls == ["_extract_office_to_wire"]


@pytest.mark.asyncio
async def test_office_parser_deduplicates_images_and_replays_in_place(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (
            OfficePage(
                1,
                (
                    NativeSlot(0, (TextBlock("before"),)),
                    _image(1),
                    NativeSlot(2, (TextBlock("middle"),)),
                    _image(3),
                ),
            ),
        ),
    )
    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(VisionResult((VisionTextElement("visual", 0),)))
    try:
        result = await _parse(
            OfficeParser(DocumentType.DOCX, runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert result.blocks == (
        TextBlock("before"),
        TextBlock("visual"),
        TextBlock("middle"),
        TextBlock("visual"),
    )
    assert len(vision.requests) == 1
    assert calls == ["_extract_office_to_wire", "_sanitize_embedded_image"]
    assert not (tmp_path / "workspace" / "office-sanitized-0.png").exists()


@pytest.mark.asyncio
async def test_office_parser_missing_vision_degrades_only_with_native_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    native_document = OfficeDocument(
        DocumentType.DOCX,
        (OfficePage(1, (NativeSlot(0, (TextBlock("native"),)), _image(1))),),
    )
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, native_document)
    try:
        result = await _parse(OfficeParser(DocumentType.DOCX, runtime, None, None), tmp_path)
    finally:
        await runtime.aclose()

    assert result.blocks == (TextBlock("native"),)
    assert [warning.code for warning in result.warnings] == ["vision_unavailable_native_only"]

    image_only_path = tmp_path / "image-only"
    image_only_path.mkdir()
    image_only = OfficeDocument(DocumentType.DOCX, (OfficePage(1, (_image(0),)),))
    runtime, _ = _runtime_with_document(monkeypatch, image_only_path, image_only)
    try:
        with pytest.raises(VisionRequiredError):
            await _parse(OfficeParser(DocumentType.DOCX, runtime, None, None), image_only_path)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_office_parser_recoverable_model_failure_retains_native_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (OfficePage(1, (NativeSlot(0, (TextBlock("native"),)), _image(1))),),
    )
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(ModelUnavailableError("unavailable"))
    try:
        result = await _parse(
            OfficeParser(DocumentType.DOCX, runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert result.blocks == (TextBlock("native"),)
    assert [warning.code for warning in result.warnings] == ["vision_image_failed"]


@pytest.mark.asyncio
async def test_office_parser_raises_recoverable_failure_when_everything_visual_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(DocumentType.DOCX, (OfficePage(1, (_image(0),)),))
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(ModelUnavailableError("unavailable"))
    try:
        with pytest.raises(ModelUnavailableError):
            await _parse(
                OfficeParser(DocumentType.DOCX, runtime, vision, VisionConfig("model")),
                tmp_path,
            )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_office_parser_fatal_model_configuration_error_is_never_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (OfficePage(1, (NativeSlot(0, (TextBlock("native"),)), _image(1))),),
    )
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(ModelAuthenticationError("authentication"))
    try:
        with pytest.raises(ModelAuthenticationError):
            await _parse(
                OfficeParser(DocumentType.DOCX, runtime, vision, VisionConfig("model")),
                tmp_path,
            )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_office_parser_applies_pptx_page_limit_before_visual_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.PPTX,
        (OfficePage(1, (_image(0),)), OfficePage(2, ())),
    )
    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(VisionResult((VisionTextElement("unused", 0),)))
    try:
        with pytest.raises(LimitExceededError):
            await _parse(
                OfficeParser(DocumentType.PPTX, runtime, vision, VisionConfig("model")),
                tmp_path,
                options=ParseOptions(max_pages=1),
            )
    finally:
        await runtime.aclose()

    assert calls == ["_extract_office_to_wire"]
    assert vision.requests == []


@pytest.mark.asyncio
async def test_office_parser_blank_deck_is_not_semantic_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(DocumentType.PPTX, (OfficePage(1, ()),))
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, document)
    try:
        with pytest.raises(NoUsableContentError):
            await _parse(OfficeParser(DocumentType.PPTX, runtime, None, None), tmp_path)
    finally:
        await runtime.aclose()

    merged = PageBreakBlock(1)
    assert merged.page_number == 1


@pytest.mark.asyncio
async def test_office_parser_fixed_vision_replay_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (
            OfficePage(
                1,
                (
                    NativeSlot(0, (TextBlock("before"),)),
                    _image(1),
                    _image(2),
                ),
            ),
        ),
    )
    runtime, calls = _runtime_with_document(monkeypatch, tmp_path, document)
    vision = RecordingVision(VisionResult((VisionTextElement("visual", 0),)))
    parser = OfficeParser(DocumentType.DOCX, runtime, vision, VisionConfig("model"))
    try:
        first = await _parse(parser, tmp_path)
        second = await _parse(parser, tmp_path)
    finally:
        await runtime.aclose()

    assert first == second
    assert calls == [
        "_extract_office_to_wire",
        "_sanitize_embedded_image",
        "_extract_office_to_wire",
        "_sanitize_embedded_image",
    ]
    assert [(request.source_index, request.kind) for request in vision.requests] == [
        (0, VisionRequestKind.PROSE),
        (0, VisionRequestKind.PROSE),
    ]
