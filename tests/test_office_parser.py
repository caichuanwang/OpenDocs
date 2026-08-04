from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

import opendocs.parsers.office.parser as parser_module
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
from opendocs.parsers.office.parser import OfficeParser, _extract_office_to_wire
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


def _image(
    source_index: int,
    digest: str = "a" * 64,
    bbox: BBox | None = None,
    alt_text: str | None = None,
) -> ImageSlot:
    return ImageSlot(source_index, "embedded.png", digest, bbox or BBox(0, 0, 1, 1), alt_text)


def _runtime_with_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    document: OfficeDocument,
    *,
    size: tuple[int, int] = (20, 10),
    facts: dict[str, object] | None = None,
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
        if function.__name__ == "prepare_image":
            output_directory = args[1]
            output_stem = args[2]
            assert isinstance(output_directory, Path)
            assert isinstance(output_stem, str)
            name = f"{output_stem}-0.png"
            (output_directory / name).write_bytes(b"sanitized")
            return {
                "skipped": False,
                "reason": None,
                "width": size[0],
                "height": size[1],
                "parts": [
                    {
                        "name": name,
                        "top": 0.0,
                        "bottom": 1.0,
                        "core_top": 0.0,
                        "core_bottom": 1.0,
                        "width": size[0],
                        "height": size[1],
                    }
                ],
                "facts": facts
                or {
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


def test_native_pptx_page_limit_runs_before_document_extraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def reject_page_limit(path: Path, *, max_pages: int) -> None:
        del path, max_pages
        events.append("limit")
        raise LimitExceededError("page limit")

    def forbidden_extract(path: Path, workspace: ParseWorkspace) -> OfficeDocument:
        del path, workspace
        events.append("extract")
        raise AssertionError("PPTX extraction must not start after the page limit fails")

    monkeypatch.setattr(parser_module, "enforce_pptx_page_limit", reject_page_limit)
    monkeypatch.setattr("opendocs.parsers.office.pptx.extract_pptx", forbidden_extract)

    with pytest.raises(LimitExceededError, match="page limit"):
        _extract_office_to_wire("pptx", tmp_path / "source.pptx", tmp_path, 1)

    assert events == ["limit"]


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
    assert calls == ["_extract_office_to_wire", "prepare_image"]
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
async def test_office_parser_fatal_native_preparation_error_is_not_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.DOCX,
        (OfficePage(1, (NativeSlot(0, (TextBlock("native"),)), _image(1))),),
    )
    runtime, _ = _runtime_with_document(monkeypatch, tmp_path, document)

    async def run_native(function: Any, *_args: object, **_kwargs: object) -> object:
        if function.__name__ == "_extract_office_to_wire":
            return document_to_wire(document)
        if function.__name__ == "prepare_image":
            raise ModelAuthenticationError("authentication")
        raise AssertionError(f"unexpected native function: {function.__name__}")

    monkeypatch.setattr(runtime, "run_native", run_native)
    try:
        with pytest.raises(ModelAuthenticationError):
            await _parse(
                OfficeParser(DocumentType.DOCX, runtime, None, None),
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
async def test_office_parser_skips_decorative_occurrence_without_vision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.PPTX,
        (
            OfficePage(
                1,
                (
                    NativeSlot(0, (TextBlock("native"),)),
                    _image(1, bbox=BBox(0, 0, 0.1, 0.1), alt_text="Picture 1"),
                ),
            ),
        ),
    )
    runtime, calls = _runtime_with_document(
        monkeypatch,
        tmp_path,
        document,
        size=(120, 120),
        facts={
            "alpha_coverage": 0.11,
            "components": 1,
            "edge_density": 0.08,
            "color_count": 4,
            "nearly_blank": False,
        },
    )
    try:
        result = await _parse(OfficeParser(DocumentType.PPTX, runtime, None, None), tmp_path)
    finally:
        await runtime.aclose()

    assert TextBlock("native") in result.blocks
    assert result.warnings == ()
    assert calls == ["_extract_office_to_wire", "prepare_image"]
    assert not tuple((tmp_path / "workspace").glob("office-sanitized-*.png"))


@pytest.mark.asyncio
async def test_office_parser_admits_only_large_occurrence_of_same_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.PPTX,
        (
            OfficePage(
                1,
                (
                    NativeSlot(0, (TextBlock("before"),)),
                    _image(1, bbox=BBox(0, 0, 0.1, 0.1)),
                    NativeSlot(2, (TextBlock("middle"),)),
                    _image(3, bbox=BBox(0.1, 0.1, 0.8, 0.8)),
                ),
            ),
        ),
    )
    runtime, _ = _runtime_with_document(
        monkeypatch,
        tmp_path,
        document,
        size=(120, 120),
        facts={
            "alpha_coverage": 0.11,
            "components": 1,
            "edge_density": 0.08,
            "color_count": 4,
            "nearly_blank": False,
        },
    )
    vision = RecordingVision(VisionResult((VisionTextElement("visual", 0),)))
    try:
        result = await _parse(
            OfficeParser(DocumentType.PPTX, runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert len(vision.requests) == 1
    assert result.blocks == (
        PageBreakBlock(1),
        TextBlock("before"),
        TextBlock("middle"),
        TextBlock("visual"),
    )


@pytest.mark.asyncio
async def test_office_parser_meaningful_alt_text_prevents_decorative_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = OfficeDocument(
        DocumentType.PPTX,
        (OfficePage(1, (_image(0, bbox=BBox(0, 0, 0.1, 0.1), alt_text="Revenue trend"),)),),
    )
    runtime, _ = _runtime_with_document(
        monkeypatch,
        tmp_path,
        document,
        size=(120, 120),
        facts={
            "alpha_coverage": 0.11,
            "components": 1,
            "edge_density": 0.08,
            "color_count": 4,
            "nearly_blank": False,
        },
    )
    vision = RecordingVision(VisionResult((VisionTextElement("trend", 0),)))
    try:
        result = await _parse(
            OfficeParser(DocumentType.PPTX, runtime, vision, VisionConfig("model")),
            tmp_path,
        )
    finally:
        await runtime.aclose()

    assert TextBlock("trend") in result.blocks
    assert len(vision.requests) == 1


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
        "prepare_image",
        "_extract_office_to_wire",
        "prepare_image",
    ]
    assert [(request.source_index, request.kind) for request in vision.requests] == [
        (0, VisionRequestKind.PROSE),
        (0, VisionRequestKind.PROSE),
    ]
