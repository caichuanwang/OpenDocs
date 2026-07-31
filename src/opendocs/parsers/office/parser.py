from __future__ import annotations

import asyncio
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
from opendocs.parsers.image import _sanitize_embedded_image
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
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import VisionClient, VisionRequest, VisionRequestKind, VisionResult
from opendocs.vision.prompts import GENERAL_IMAGE_PROMPT, TABLE_IMAGE_PROMPT

_ULTRA_WIDE_RATIO = 4.0
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
) -> dict[str, object]:
    document_type = DocumentType(document_type_value)
    workspace = ParseWorkspace(workspace_path)
    if document_type is DocumentType.DOCX:
        from opendocs.parsers.office.docx import extract_docx

        document = extract_docx(path, workspace)
    elif document_type is DocumentType.PPTX:
        from opendocs.parsers.office.pptx import extract_pptx

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
                document = await self._extract(source)
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
        if images and (self._vision is None or self._vision_config is None):
            raise VisionRequiredError(
                f"{self._document_type.value.upper()} requires vision but none was configured"
            )
        if failures:
            raise failures[0]
        raise NoUsableContentError(
            f"{self._document_type.value.upper()} produced no usable content"
        )

    async def _extract(self, source: ResolvedSource) -> OfficeDocument:
        wire = await self._runtime.run_native(
            _extract_office_to_wire,
            self._document_type.value,
            source.path,
            self._runtime.workspace.path,
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
        if self._vision is None or self._vision_config is None:
            return {
                image.content_sha256: OfficeVisualOutcome(
                    None,
                    "vision_unavailable_native_only",
                )
                for image in images
            }, ()

        outcomes: dict[str, OfficeVisualOutcome] = {}
        failures: list[OpenDocsError] = []
        prepared: list[tuple[ImageSlot, Path, VisionRequest]] = []
        try:
            for admission_index, image in enumerate(images):
                source_path = self._runtime.workspace.output_path(image.artifact_name)
                sanitized_path = self._runtime.workspace.output_path(
                    f"office-sanitized-{admission_index}.png"
                )
                try:
                    width, height = await self._runtime.run_native(
                        _sanitize_embedded_image,
                        source_path,
                        sanitized_path,
                    )
                except asyncio.CancelledError:
                    raise
                except OpenDocsError as error:
                    failures.append(error)
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        None,
                        _RECOVERABLE_VISUAL_WARNING,
                    )
                    continue
                is_table = width / height >= _ULTRA_WIDE_RATIO
                prepared.append(
                    (
                        image,
                        sanitized_path,
                        VisionRequest(
                            sanitized_path,
                            TABLE_IMAGE_PROMPT if is_table else GENERAL_IMAGE_PROMPT,
                            admission_index,
                            VisionRequestKind.TABLE if is_table else VisionRequestKind.PROSE,
                        ),
                    )
                )

            model_results = await asyncio.gather(
                *(self._vision.analyze(request) for _, _, request in prepared),
                return_exceptions=True,
            )
            for (image, _, _), result in zip(prepared, model_results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, _FATAL_VISUAL_ERRORS):
                    raise result
                if isinstance(result, OpenDocsError):
                    failures.append(result)
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        None,
                        "vision_image_failed",
                    )
                elif isinstance(result, BaseException):
                    wrapped = RuntimeDependencyError(
                        f"Office vision client failed: {type(result).__name__}"
                    )
                    failures.append(wrapped)
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        None,
                        "vision_image_failed",
                    )
                elif isinstance(result, VisionResult):
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        result,
                        None if result.elements else "vision_image_empty",
                    )
                else:
                    wrapped = RuntimeDependencyError("Office vision client returned invalid data")
                    failures.append(wrapped)
                    outcomes[image.content_sha256] = OfficeVisualOutcome(
                        None,
                        "vision_image_failed",
                    )
        finally:
            for _, sanitized_path, _ in prepared:
                with suppress(OSError):
                    sanitized_path.unlink(missing_ok=True)
        return outcomes, tuple(failures)
