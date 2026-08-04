from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from pathlib import Path

from opendocs._models import DocumentType, ParsedDocument
from opendocs._runtime import ParserRuntime
from opendocs.errors import (
    DocumentTimeoutError,
    LimitExceededError,
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelPermissionError,
    NoUsableContentError,
    OpenDocsError,
    RuntimeDependencyError,
    VisionRequiredError,
)
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.office.merge import (
    OfficeVisualOutcome,
    has_semantic_office_content,
    merge_office_document,
)
from opendocs.parsers.office.models import (
    ImageSlot,
    OfficeDocument,
    document_from_wire,
    document_to_wire,
)
from opendocs.parsers.office.package import enforce_pptx_page_limit
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import VisionClient, VisionRequest, VisionRequestKind, VisionResult
from opendocs.vision.images import (
    ULTRA_WIDE_RATIO,
    PreparedImage,
    is_decorative_embedded,
    merge_tiled_results,
    prepare_image,
    prepared_paths,
    tile_prompt,
)
from opendocs.vision.prompts import GENERAL_IMAGE_PROMPT, TABLE_IMAGE_PROMPT

_ULTRA_WIDE_RATIO = ULTRA_WIDE_RATIO
_RECOVERABLE_VISUAL_WARNING = "embedded_image_failed"
_FATAL_VISUAL_ERRORS = (
    ModelAuthenticationError,
    ModelPermissionError,
    ModelInvalidRequestError,
    RuntimeDependencyError,
    DocumentTimeoutError,
)


def _extract_office_to_wire(
    document_type_value: str,
    path: Path,
    workspace_path: Path,
    max_pages: int,
) -> dict[str, object]:
    document_type = DocumentType(document_type_value)
    workspace = ParseWorkspace(workspace_path)
    if document_type is DocumentType.DOCX:
        from opendocs.parsers.office.docx import extract_docx

        document = extract_docx(path, workspace)
    elif document_type is DocumentType.PPTX:
        from opendocs.parsers.office.pptx import extract_pptx

        enforce_pptx_page_limit(path, max_pages=max_pages)
        document = extract_pptx(path, workspace)
    else:
        raise ValueError("native Office extraction requires DOCX or PPTX")
    return document_to_wire(document)


def _image_slots(document: OfficeDocument) -> tuple[ImageSlot, ...]:
    return tuple(
        slot
        for page in document.pages
        for slot in sorted(page.slots, key=lambda item: item.source_index)
        if isinstance(slot, ImageSlot)
    )


def _unique_images(slots: tuple[ImageSlot, ...]) -> tuple[ImageSlot, ...]:
    unique: list[ImageSlot] = []
    seen: set[str] = set()
    for slot in slots:
        if slot.content_sha256 not in seen:
            seen.add(slot.content_sha256)
            unique.append(slot)
    return tuple(unique)


def _image_occurrences(document: OfficeDocument) -> dict[str, tuple[tuple[int, ImageSlot], ...]]:
    grouped: dict[str, list[tuple[int, ImageSlot]]] = {}
    for page in document.pages:
        for slot in page.slots:
            if isinstance(slot, ImageSlot):
                grouped.setdefault(slot.content_sha256, []).append((page.page_number, slot))
    return {digest: tuple(items) for digest, items in grouped.items()}


def _placement_area(document_type: DocumentType, slot: ImageSlot) -> float | None:
    if document_type is DocumentType.DOCX:
        return None
    return (slot.bbox.right - slot.bbox.left) * (slot.bbox.bottom - slot.bbox.top)


def _meaningful_alt_text(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    return re.fullmatch(r"(?:picture|image|graphic)\s*\d+", value.strip(), re.IGNORECASE) is None


class OfficeParser:
    def __init__(
        self,
        document_type: DocumentType,
        runtime: ParserRuntime,
        vision: VisionClient | None,
        vision_config: VisionConfig | None,
        *,
        deadline: float | None = None,
    ) -> None:
        if document_type not in {DocumentType.DOCX, DocumentType.PPTX}:
            raise ValueError("document_type must be DOCX or PPTX")
        if not isinstance(runtime, ParserRuntime):
            raise TypeError("runtime must be a ParserRuntime")
        if vision_config is not None and not isinstance(vision_config, VisionConfig):
            raise TypeError("vision_config must be a VisionConfig or None")
        self._document_type = document_type
        self._runtime = runtime
        self._vision = vision
        self._vision_config = vision_config
        self._deadline = deadline

    async def parse(self, source: ResolvedSource, *, options: ParseOptions) -> ParsedDocument:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(options.timeout)
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        try:
            async with asyncio.timeout_at(deadline):
                document = await self._extract(source, max_pages=options.max_pages)
                if (
                    self._document_type is DocumentType.PPTX
                    and len(document.pages) > options.max_pages
                ):
                    raise LimitExceededError(
                        f"PPTX exceeds the configured {options.max_pages} page limit"
                    )
                visual_outcomes, failures = await self._visual_outcomes(document)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise DocumentTimeoutError("Office parsing exceeded the document deadline") from None

        merged = merge_office_document(document, visual_outcomes)
        if has_semantic_office_content(merged):
            return merged
        images = _image_slots(document)
        if (
            images
            and (self._vision is None or self._vision_config is None)
            and any(
                outcome.warning_code == "vision_unavailable_native_only"
                for outcome in visual_outcomes.values()
            )
        ):
            raise VisionRequiredError(
                f"{self._document_type.value.upper()} requires vision but none was configured"
            )
        if failures:
            raise failures[0]
        raise NoUsableContentError(
            f"{self._document_type.value.upper()} produced no usable content"
        )

    async def _extract(self, source: ResolvedSource, *, max_pages: int) -> OfficeDocument:
        wire = await self._runtime.run_native(
            _extract_office_to_wire,
            self._document_type.value,
            source.path,
            self._runtime.workspace.path,
            max_pages,
        )
        try:
            document = document_from_wire(wire)
        except (TypeError, ValueError) as error:
            raise RuntimeDependencyError("native Office worker returned invalid data") from error
        if document.document_type is not self._document_type:
            raise RuntimeDependencyError("native Office worker returned the wrong document type")
        return document

    async def _visual_outcomes(
        self,
        document: OfficeDocument,
    ) -> tuple[dict[str, OfficeVisualOutcome], tuple[OpenDocsError, ...]]:
        images = _unique_images(_image_slots(document))
        if not images:
            return {}, ()
        occurrences = _image_occurrences(document)
        outcomes: dict[str, OfficeVisualOutcome] = {}
        failures: list[OpenDocsError] = []
        prepared_items: list[
            tuple[int, ImageSlot, PreparedImage, tuple[Path, ...], frozenset[tuple[int, int]]]
        ] = []
        try:
            for admission_index, image in enumerate(images):
                source_path = self._runtime.workspace.output_path(image.artifact_name)
                try:
                    prepared = await self._runtime.run_native(
                        prepare_image,
                        source_path,
                        self._runtime.workspace.path,
                        f"office-sanitized-{admission_index}",
                        None,
                        "embedded",
                        None,
                    )
                except asyncio.CancelledError:
                    raise
                except _FATAL_VISUAL_ERRORS:
                    raise
                except OpenDocsError as error:
                    failures.append(error)
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        None,
                        _RECOVERABLE_VISUAL_WARNING,
                    )
                    continue
                paths = prepared_paths(prepared, self._runtime.workspace.path)
                admitted = frozenset(
                    (page_number, slot.source_index)
                    for page_number, slot in occurrences[image.content_sha256]
                    if not is_decorative_embedded(
                        prepared,
                        _placement_area(document.document_type, slot),
                        meaningful_alt_text=_meaningful_alt_text(slot.alt_text),
                    )
                )
                if bool(prepared.get("skipped")) or not admitted:
                    outcomes[image.content_sha256] = OfficeVisualOutcome(None, None, admitted)
                    for path in paths:
                        with suppress(OSError):
                            path.unlink(missing_ok=True)
                    continue
                if self._vision is None or self._vision_config is None:
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        None,
                        "vision_unavailable_native_only",
                        admitted,
                    )
                    for path in paths:
                        with suppress(OSError):
                            path.unlink(missing_ok=True)
                    continue
                prepared_items.append((admission_index, image, prepared, paths, admitted))

            vision = self._vision
            if vision is None:
                if prepared_items:
                    raise RuntimeDependencyError("Office vision client is unavailable")
                return outcomes, tuple(failures)
            active_vision: VisionClient = vision

            async def analyze_item(
                item: tuple[
                    int,
                    ImageSlot,
                    PreparedImage,
                    tuple[Path, ...],
                    frozenset[tuple[int, int]],
                ],
            ) -> tuple[str, OfficeVisualOutcome, tuple[OpenDocsError, ...]]:
                admission_index, image, prepared, paths, admitted = item
                width = prepared.get("width")
                height = prepared.get("height")
                if not isinstance(width, int) or not isinstance(height, int):
                    error = RuntimeDependencyError("Office image dimensions are invalid")
                    return (
                        image.content_sha256,
                        OfficeVisualOutcome(None, _RECOVERABLE_VISUAL_WARNING, admitted),
                        (error,),
                    )
                is_table = width / height >= _ULTRA_WIDE_RATIO
                prompt = TABLE_IMAGE_PROMPT if is_table else GENERAL_IMAGE_PROMPT
                kind = VisionRequestKind.TABLE if is_table else VisionRequestKind.PROSE
                requests = [
                    VisionRequest(
                        path,
                        tile_prompt(prompt, tile_index, len(paths)),
                        admission_index * 10_000 + tile_index,
                        kind,
                    )
                    for tile_index, path in enumerate(paths)
                ]
                model_results = await asyncio.gather(
                    *(active_vision.analyze(request) for request in requests),
                    return_exceptions=True,
                )
                tile_results: list[VisionResult | None] = []
                item_failures: list[OpenDocsError] = []
                for result in model_results:
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    if isinstance(result, _FATAL_VISUAL_ERRORS):
                        raise result
                    if isinstance(result, OpenDocsError):
                        item_failures.append(result)
                        tile_results.append(None)
                    elif isinstance(result, BaseException):
                        item_failures.append(
                            RuntimeDependencyError(
                                f"Office vision client failed: {type(result).__name__}"
                            )
                        )
                        tile_results.append(None)
                    elif isinstance(result, VisionResult):
                        tile_results.append(result)
                    else:
                        item_failures.append(
                            RuntimeDependencyError("Office vision client returned invalid data")
                        )
                        tile_results.append(None)
                if item_failures and all(result is None for result in tile_results):
                    outcome = OfficeVisualOutcome(None, "vision_image_failed", admitted)
                else:
                    result = merge_tiled_results(prepared, tile_results)
                    warning = "vision_image_empty" if not result.elements else None
                    if item_failures and result.elements:
                        warning = "vision_image_tile_failed"
                    outcome = OfficeVisualOutcome(result, warning, admitted)
                return image.content_sha256, outcome, tuple(item_failures)

            analyzed = await asyncio.gather(
                *(analyze_item(item) for item in prepared_items),
                return_exceptions=True,
            )
            for _item, analyzed_item in zip(prepared_items, analyzed, strict=True):
                if isinstance(analyzed_item, asyncio.CancelledError):
                    raise analyzed_item
                if isinstance(analyzed_item, _FATAL_VISUAL_ERRORS):
                    raise analyzed_item
                if isinstance(analyzed_item, BaseException):
                    raise RuntimeDependencyError(
                        f"Office vision processing failed: {type(analyzed_item).__name__}"
                    ) from analyzed_item
                digest, outcome, item_failures = analyzed_item
                outcomes[digest] = outcome
                failures.extend(item_failures)
        finally:
            for _, _, _, paths, _ in prepared_items:
                for path in paths:
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
        return outcomes, tuple(failures)
