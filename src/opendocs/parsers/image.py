from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from opendocs._models import (
    DocumentType,
    ParsedDocument,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs._runtime import ParserRuntime
from opendocs.errors import (
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelPermissionError,
    NoUsableContentError,
    OpenDocsError,
    RuntimeDependencyError,
    VisionRequiredError,
)
from opendocs.options import ParseOptions, VisionConfig
from opendocs.source import ResolvedSource
from opendocs.vision.base import (
    VisionClient,
    VisionRequest,
    VisionRequestKind,
    VisionResult,
    VisionTableElement,
    VisionTextElement,
)
from opendocs.vision.images import (
    MAX_HEIGHT,
    MAX_MODEL_LONG_SIDE,
    MAX_PIXELS,
    MAX_WIDTH,
    ULTRA_WIDE_RATIO,
    merge_tiled_results,
    prepare_image,
    prepared_paths,
    sanitize_image,
    tile_prompt,
)
from opendocs.vision.prompts import GENERAL_IMAGE_PROMPT, TABLE_IMAGE_PROMPT

_MAX_WIDTH = MAX_WIDTH
_MAX_HEIGHT = MAX_HEIGHT
_MAX_PIXELS = MAX_PIXELS
_MAX_MODEL_LONG_SIDE = MAX_MODEL_LONG_SIDE
_ULTRA_WIDE_RATIO = ULTRA_WIDE_RATIO
_FATAL_VISUAL_ERRORS = (
    ModelAuthenticationError,
    ModelPermissionError,
    ModelInvalidRequestError,
    RuntimeDependencyError,
)


def _sanitize_image(
    source_path: Path, output_path: Path, original_name: str | None
) -> tuple[int, int]:
    return sanitize_image(
        source_path,
        output_path,
        original_name,
        max_width=_MAX_WIDTH,
        max_height=_MAX_HEIGHT,
        max_pixels=_MAX_PIXELS,
    )


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
        prepared = await self._runtime.run_native(
            prepare_image,
            source.path,
            self._runtime.workspace.path,
            "sanitized-image",
            source.original_name,
            "standalone",
        )
        paths = prepared_paths(prepared, self._runtime.workspace.path)
        try:
            if bool(prepared.get("skipped")):
                raise NoUsableContentError("image produced no usable content")
            width = prepared.get("width")
            height = prepared.get("height")
            if not isinstance(width, int) or not isinstance(height, int):
                raise RuntimeDependencyError("native image worker returned invalid dimensions")
            is_table = width / height >= _ULTRA_WIDE_RATIO
            prompt = TABLE_IMAGE_PROMPT if is_table else GENERAL_IMAGE_PROMPT
            kind = VisionRequestKind.TABLE if is_table else VisionRequestKind.PROSE
            requests = [
                VisionRequest(
                    path,
                    tile_prompt(prompt, index, len(paths)),
                    index,
                    kind,
                )
                for index, path in enumerate(paths)
            ]
            outcomes = await asyncio.gather(
                *(self._vision.analyze(request) for request in requests),
                return_exceptions=True,
            )

            results: list[VisionResult | None] = []
            failures: list[OpenDocsError] = []
            for outcome in outcomes:
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                if isinstance(outcome, _FATAL_VISUAL_ERRORS):
                    raise outcome
                if isinstance(outcome, OpenDocsError):
                    failures.append(outcome)
                    results.append(None)
                elif isinstance(outcome, BaseException):
                    failures.append(
                        RuntimeDependencyError(
                            f"image vision client failed: {type(outcome).__name__}"
                        )
                    )
                    results.append(None)
                elif isinstance(outcome, VisionResult):
                    results.append(outcome)
                else:
                    failures.append(
                        RuntimeDependencyError("image vision client returned invalid data")
                    )
                    results.append(None)
            if failures and all(result is None for result in results):
                raise failures[0]
            result = merge_tiled_results(prepared, results)
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
            warnings = (
                (
                    WarningRecord(
                        "image_tile_failed",
                        f"image: {len(failures)} of {len(requests)} tiles failed",
                    ),
                )
                if failures
                else ()
            )
            return ParsedDocument(DocumentType.IMAGE, tuple(blocks), warnings)
        finally:
            for path in paths:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
