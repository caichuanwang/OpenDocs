from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image, ImageOps

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
from opendocs.source import ResolvedSource
from opendocs.vision.base import (
    VisionClient,
    VisionRequest,
    VisionRequestKind,
    VisionTableElement,
    VisionTextElement,
)
from opendocs.vision.prompts import GENERAL_IMAGE_PROMPT, TABLE_IMAGE_PROMPT

_MAX_WIDTH = 50_000
_MAX_HEIGHT = 50_000
_MAX_PIXELS = 80_000_000
_MAX_MODEL_LONG_SIDE = 2_048
_ULTRA_WIDE_RATIO = 4.0
_ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
_SUFFIX_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}


def _sanitize_image(
    source_path: Path, output_path: Path, original_name: str | None
) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source_path) as candidate:
                detected_format = candidate.format
                width, height = candidate.size
                frames = getattr(candidate, "n_frames", 1)
                candidate.verify()
            if detected_format not in _ALLOWED_FORMATS:
                raise UnsupportedDocumentError("image format is not supported in this release")
            if original_name:
                declared = _SUFFIX_FORMATS.get(Path(original_name).suffix.lower())
                if declared is not None and declared != detected_format:
                    raise DocumentTypeMismatchError(
                        f"image extension declares {declared.lower()} but content is "
                        f"{detected_format.lower()}"
                    )
            if frames != 1:
                raise UnsupportedDocumentError("animated images are not supported in this release")
            if width <= 0 or height <= 0:
                raise CorruptDocumentError("image dimensions are invalid")
            if width > _MAX_WIDTH or height > _MAX_HEIGHT or width * height > _MAX_PIXELS:
                raise LimitExceededError("image dimensions exceed the safety budget")

            with Image.open(source_path) as opened:
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
                try:
                    clean = oriented.convert("RGB")
                finally:
                    if oriented is not opened:
                        oriented.close()
                clean.thumbnail(
                    (_MAX_MODEL_LONG_SIDE, _MAX_MODEL_LONG_SIDE), Image.Resampling.LANCZOS
                )
                clean.info.clear()
                final_size = clean.size
                clean.save(output_path, format="PNG", optimize=False)
                clean.close()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise LimitExceededError("image dimensions exceed the safety budget") from error
    except (DocumentTypeMismatchError, LimitExceededError, UnsupportedDocumentError):
        raise
    except (OSError, SyntaxError, ValueError) as error:
        raise CorruptDocumentError("image is corrupt or cannot be decoded") from error
    return final_size


def _sanitize_embedded_image(source_path: Path, output_path: Path) -> tuple[int, int]:
    """Sanitize trusted-workspace media without applying a caller filename policy."""
    return _sanitize_image(source_path, output_path, None)


class ImageParser:
    def __init__(
        self,
        runtime: ParserRuntime,
        vision: VisionClient | None,
        vision_config: VisionConfig | None,
    ) -> None:
        if not isinstance(runtime, ParserRuntime):
            raise TypeError("runtime must be a ParserRuntime")
        if vision_config is not None and not isinstance(vision_config, VisionConfig):
            raise TypeError("vision_config must be a VisionConfig or None")
        self._runtime = runtime
        self._vision = vision
        self._vision_config = vision_config

    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        del options
        if self._vision_config is None or self._vision is None:
            raise VisionRequiredError("standalone images require a vision configuration")
        output_path = self._runtime.workspace.output_path("sanitized-image.png")
        try:
            width, height = await self._runtime.run_native(
                _sanitize_image,
                source.path,
                output_path,
                source.original_name,
            )
            is_table = width / height >= _ULTRA_WIDE_RATIO
            result = await self._vision.analyze(
                VisionRequest(
                    output_path,
                    TABLE_IMAGE_PROMPT if is_table else GENERAL_IMAGE_PROMPT,
                    0,
                    VisionRequestKind.TABLE if is_table else VisionRequestKind.PROSE,
                )
            )
        finally:
            output_path.unlink(missing_ok=True)
        blocks: list[TextBlock | TableBlock] = []
        for element in result.elements:
            if isinstance(element, VisionTextElement) and element.text.strip():
                blocks.append(TextBlock(element.text.strip()))
            elif isinstance(element, VisionTableElement):
                blocks.append(TableBlock(element.grid, element.header_rows))
        if not blocks:
            raise NoUsableContentError("image produced no usable content")
        if is_table and not any(isinstance(block, TableBlock) for block in blocks):
            raise NoUsableContentError("table image produced no usable table")
        return ParsedDocument(DocumentType.IMAGE, tuple(blocks))
