from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from PIL import Image, PngImagePlugin

from opendocs._models import DocumentType, ParsedDocument, TableBlock, TextBlock
from opendocs._runtime import ParserRuntime
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTypeMismatchError,
    LimitExceededError,
    NoUsableContentError,
    UnsupportedDocumentError,
    VisionRequiredError,
)
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers import image as image_module
from opendocs.parsers.image import ImageParser
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import (
    VisionRequest,
    VisionResult,
    VisionTableElement,
    VisionTextElement,
)


class RecordingVision:
    def __init__(self, result: VisionResult) -> None:
        self.result = result
        self.requests: list[VisionRequest] = []
        self.observed: dict[str, object] = {}

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.requests.append(request)
        with Image.open(request.image_path) as image:
            self.observed = {
                "format": image.format,
                "mode": image.mode,
                "size": image.size,
                "info": dict(image.info),
            }
            image.load()
        return self.result


class BlockingVision:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.path: Path | None = None

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.path = request.image_path
        self.started.set()
        await self.release.wait()
        return VisionResult((VisionTextElement("unused", 0),))


def _save(path: Path, image_format: str, *, size=(20, 10), **kwargs) -> None:
    Image.new("RGB", size, "white").save(path, image_format, **kwargs)


async def _parse(
    tmp_path: Path,
    source: ResolvedSource,
    vision,
    config: VisionConfig | None = None,
):
    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    try:
        resolved_config = config if config is not None else VisionConfig("model")
        return await ImageParser(runtime, vision, resolved_config).parse(
            source, options=ParseOptions()
        )
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "image_format"),
    [(".png", "PNG"), (".jpg", "JPEG"), (".webp", "WEBP")],
)
async def test_image_parser_sanitizes_supported_formats(
    tmp_path: Path, suffix: str, image_format: str
) -> None:
    source_path = tmp_path / f"source{suffix}"
    _save(source_path, image_format, comment=b"private metadata")
    vision = RecordingVision(VisionResult((VisionTextElement("content", 0),)))

    result = await _parse(
        tmp_path,
        ResolvedSource(source_path, source_path.name, owned=False),
        vision,
    )

    assert result == ParsedDocument(DocumentType.IMAGE, (TextBlock("content"),))
    assert vision.observed["format"] == "PNG"
    assert vision.observed["mode"] == "RGB"
    assert vision.observed["info"] == {}
    assert not (tmp_path / "sanitized-image.png").exists()


@pytest.mark.asyncio
async def test_image_parser_requires_vision_before_native_work(tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    _save(source_path, "PNG")
    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    try:
        parser = ImageParser(runtime, None, None)
        with pytest.raises(VisionRequiredError):
            await parser.parse(
                ResolvedSource(source_path, source_path.name, owned=False),
                options=ParseOptions(),
            )
        assert runtime.native_worker.pid is None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_image_parser_rejects_corrupt_and_suffix_mismatch(tmp_path: Path) -> None:
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not an image")
    vision = RecordingVision(VisionResult((VisionTextElement("unused", 0),)))
    with pytest.raises(CorruptDocumentError):
        await _parse(tmp_path, ResolvedSource(corrupt, corrupt.name, False), vision)

    mismatch = tmp_path / "image.jpg"
    _save(mismatch, "PNG")
    with pytest.raises(DocumentTypeMismatchError):
        await _parse(tmp_path, ResolvedSource(mismatch, mismatch.name, False), vision)
    assert vision.requests == []


def test_sanitizer_removes_exif_icc_and_comment_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "private.jpg"
    output_path = tmp_path / "sanitized.png"
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private description"
    exif[315] = "private author"
    gps = exif.get_ifd(34853)
    gps[1] = "N"
    gps[2] = (1.0, 2.0, 3.0)
    Image.new("RGB", (40, 20), "white").save(
        source_path,
        "JPEG",
        exif=exif,
        icc_profile=b"private-icc-profile",
        comment=b"private-comment",
    )

    assert image_module._sanitize_image(source_path, output_path, source_path.name) == (20, 40)

    with Image.open(output_path) as sanitized:
        sanitized.load()
        assert sanitized.getexif() == {}
        assert "icc_profile" not in sanitized.info
        assert "comment" not in sanitized.info
        assert "exif" not in sanitized.info


def test_png_comment_metadata_is_removed(tmp_path: Path) -> None:
    source_path = tmp_path / "private.png"
    output_path = tmp_path / "sanitized.png"
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text("Comment", "private-comment")
    Image.new("RGB", (20, 10), "white").save(source_path, "PNG", pnginfo=pnginfo)

    image_module._sanitize_image(source_path, output_path, source_path.name)

    with Image.open(output_path) as sanitized:
        sanitized.load()
        assert sanitized.info == {}


@pytest.mark.asyncio
async def test_image_parser_applies_exif_rotation_and_resize(tmp_path: Path) -> None:
    source_path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    _save(source_path, "JPEG", size=(40, 20), exif=exif)
    vision = RecordingVision(VisionResult((VisionTextElement("rotated", 0),)))

    await _parse(tmp_path, ResolvedSource(source_path, source_path.name, False), vision)

    assert vision.observed["size"] == (20, 40)


@pytest.mark.asyncio
async def test_ultrawide_image_requires_and_returns_table(tmp_path: Path) -> None:
    source_path = tmp_path / "table.png"
    _save(source_path, "PNG", size=(400, 50))
    table = VisionTableElement((("A", "B"), ("1", "2")), 1, 0)
    vision = RecordingVision(VisionResult((table,)))

    result = await _parse(tmp_path, ResolvedSource(source_path, source_path.name, False), vision)

    assert vision.requests[0].structured_required
    assert result.blocks == (TableBlock(table.grid, 1),)

    empty = RecordingVision(VisionResult((VisionTextElement("not a table", 0),)))
    with pytest.raises(NoUsableContentError):
        await _parse(tmp_path, ResolvedSource(source_path, source_path.name, False), empty)


@pytest.mark.asyncio
async def test_animated_webp_is_rejected(tmp_path: Path) -> None:
    source_path = tmp_path / "animated.webp"
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
    frames[0].save(source_path, "WEBP", save_all=True, append_images=frames[1:], duration=10)
    vision = RecordingVision(VisionResult((VisionTextElement("unused", 0),)))

    with pytest.raises(UnsupportedDocumentError, match="animated"):
        await _parse(tmp_path, ResolvedSource(source_path, source_path.name, False), vision)


@pytest.mark.parametrize(
    ("size", "constant", "value"),
    [((20, 10), "_MAX_WIDTH", 10), ((20, 10), "_MAX_HEIGHT", 5), ((20, 10), "_MAX_PIXELS", 100)],
)
def test_native_image_limits_are_hard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    size: tuple[int, int],
    constant: str,
    value: int,
) -> None:
    source_path = tmp_path / "source.png"
    output_path = tmp_path / "clean.png"
    _save(source_path, "PNG", size=size)
    monkeypatch.setattr(image_module, constant, value)

    with pytest.raises(LimitExceededError):
        image_module._sanitize_image(source_path, output_path, source_path.name)


def test_native_image_promotes_decompression_warning_to_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_path = tmp_path / "source.png"
    output_path = tmp_path / "clean.png"
    _save(source_path, "PNG", size=(20, 20))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(LimitExceededError):
        image_module._sanitize_image(source_path, output_path, source_path.name)


@pytest.mark.asyncio
async def test_image_parser_downscales_model_artifact(tmp_path: Path) -> None:
    source_path = tmp_path / "large.png"
    _save(source_path, "PNG", size=(3_000, 1_000))
    vision = RecordingVision(VisionResult((VisionTextElement("content", 0),)))

    await _parse(tmp_path, ResolvedSource(source_path, source_path.name, False), vision)

    observed_size = vision.observed["size"]
    assert isinstance(observed_size, tuple)
    assert max(cast(tuple[int, int], observed_size)) == image_module._MAX_MODEL_LONG_SIDE


@pytest.mark.asyncio
async def test_image_parser_cancellation_removes_sanitized_artifact(tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    _save(source_path, "PNG")
    vision = BlockingVision()
    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    parser = ImageParser(runtime, vision, VisionConfig("model"))
    task = asyncio.create_task(
        parser.parse(
            ResolvedSource(source_path, source_path.name, False),
            options=ParseOptions(),
        )
    )
    await vision.started.wait()
    assert vision.path is not None and vision.path.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not vision.path.exists()
    await runtime.aclose()
