from __future__ import annotations

import asyncio
import inspect
import uuid
from contextlib import AbstractAsyncContextManager, suppress
from pathlib import Path
from typing import Protocol

from opendocs._models import (
    BBox,
    DocumentType,
    ParsedDocument,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs._runtime import ParserRuntime
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTimeoutError,
    LimitExceededError,
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelInvalidResponseError,
    ModelPermissionError,
    NoUsableContentError,
    OpenDocsError,
    RuntimeDependencyError,
    VisionRequiredError,
)
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.pdf.analyze import analyze_pdf
from opendocs.parsers.pdf.merge import (
    CROP_NORMALIZED_V1,
    PAGE_NORMALIZED_V1,
    PageVisionResult,
    merge_pdf_pages,
)
from opendocs.parsers.pdf.models import PageFacts, PageRoute, PageRouteDecision, VisualRegion
from opendocs.parsers.pdf.render import PopplerRenderer, RenderedPdfPage
from opendocs.parsers.pdf.routing import route_page
from opendocs.source import ResolvedSource
from opendocs.vision.base import (
    VisionClient,
    VisionElement,
    VisionRequest,
    VisionRequestKind,
    VisionResult,
    VisionTableElement,
    VisionTextElement,
)
from opendocs.vision.images import (
    PreparedImage,
    crop_image,
    map_result_to_bbox,
    merge_tiled_results,
    prepare_image,
    prepared_paths,
    tile_prompt,
)

_HYBRID_PROMPT = """Extract all semantic content in this PDF crop in source order.
Return structured elements with a crop-normalized-v1 bbox for every element. Do not describe
ornamentation. Preserve tables as table elements and do not duplicate table text.
"""
_FULL_PAGE_PROMPT = """Extract all semantic content from this PDF page in source order.
Return concise Markdown or structured elements. Do not describe decorative content.
"""


class PdfPageRenderer(Protocol):
    def render_page(
        self,
        pdf_path: Path,
        page: PageFacts,
        *,
        deadline: float,
        use_crop_box: bool = True,
    ) -> AbstractAsyncContextManager[RenderedPdfPage]: ...


class _UnreliableCropError(ValueError):
    pass


_FATAL_VISUAL_ERRORS = (
    ModelAuthenticationError,
    ModelPermissionError,
    ModelInvalidRequestError,
    RuntimeDependencyError,
    DocumentTimeoutError,
    CorruptDocumentError,
    LimitExceededError,
)


def _crop_page_image(
    source_path: Path,
    output_path: Path,
    pixel_box: tuple[int, int, int, int],
) -> None:
    try:
        crop_image(source_path, output_path, pixel_box)
    except CorruptDocumentError as error:
        raise RuntimeDependencyError("PDF visual crop could not be prepared") from error


def _map_crop_element(element: VisionElement, crop_bbox: BBox) -> VisionElement:
    if element.bbox is None:
        raise ModelInvalidResponseError("hybrid vision result requires an explicit bbox")
    mapped = BBox(
        crop_bbox.left + element.bbox.left * (crop_bbox.right - crop_bbox.left),
        crop_bbox.top + element.bbox.top * (crop_bbox.bottom - crop_bbox.top),
        crop_bbox.left + element.bbox.right * (crop_bbox.right - crop_bbox.left),
        crop_bbox.top + element.bbox.bottom * (crop_bbox.bottom - crop_bbox.top),
    ).require_normalized("mapped visual bbox")
    if (
        mapped.left < crop_bbox.left
        or mapped.top < crop_bbox.top
        or mapped.right > crop_bbox.right
        or mapped.bottom > crop_bbox.bottom
    ):
        raise ValueError("hybrid crop element escapes its owning region")
    if isinstance(element, VisionTableElement):
        return VisionTableElement(element.grid, element.header_rows, element.source_index, mapped)
    return VisionTextElement(element.text, element.source_index, mapped)


def _has_semantic_blocks(blocks: tuple[object, ...]) -> bool:
    for block in blocks:
        if isinstance(block, TextBlock) and block.text.strip():
            return True
        if isinstance(block, TableBlock) and any(
            cell.strip() for row in block.grid for cell in row
        ):
            return True
    return False


def _warning(code: str, page_number: int, detail: str = "") -> WarningRecord:
    suffix = f" ({detail})" if detail else ""
    return WarningRecord(code, f"PDF page {page_number}: {code.replace('_', ' ')}{suffix}")


class PDFParser:
    def __init__(
        self,
        runtime: ParserRuntime,
        vision: VisionClient | None,
        vision_config: VisionConfig | None,
        *,
        renderer: PdfPageRenderer | None = None,
        deadline: float | None = None,
    ) -> None:
        if not isinstance(runtime, ParserRuntime):
            raise TypeError("runtime must be a ParserRuntime")
        if vision_config is not None and not isinstance(vision_config, VisionConfig):
            raise TypeError("vision_config must be a VisionConfig or None")
        self._runtime = runtime
        self._vision = vision
        self._vision_config = vision_config
        self._deadline = deadline
        self._renderer = renderer or PopplerRenderer(
            runtime.workspace,
            native_runner=runtime.run_native,
        )
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        if self._closed:
            raise RuntimeError("PDF parser is closed")
        loop = asyncio.get_running_loop()
        local_deadline = loop.time() + float(options.timeout)
        deadline = local_deadline
        if self._deadline is not None:
            deadline = min(local_deadline, self._deadline)
        try:
            async with asyncio.timeout_at(deadline):
                analysis = await analyze_pdf(self._runtime, source.path, options.max_pages)
                decisions = tuple(route_page(page) for page in analysis.pages)
                visual_results, warnings, failures = await self._visual_pages(
                    source.path,
                    analysis.pages,
                    decisions,
                    deadline,
                    options.vision_concurrency,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise DocumentTimeoutError("PDF parsing exceeded the document deadline") from None

        merged = merge_pdf_pages(analysis.pages, visual_results, warnings)
        if not _has_semantic_blocks(merged.blocks):
            if self._vision is None and any(
                decision.route in {PageRoute.HYBRID, PageRoute.FULL_VISION}
                for decision in decisions
            ):
                raise VisionRequiredError(
                    "PDF requires vision but no vision configuration was provided"
                )
            if failures:
                raise failures[0]
            raise NoUsableContentError("PDF produced no usable content")
        return ParsedDocument(DocumentType.PDF, merged.blocks, merged.warnings)

    async def _close_renderer(self) -> None:
        close = getattr(self._renderer, "aclose", None)
        if close is not None:
            if not callable(close):
                raise TypeError("PDF renderer aclose must be callable")
            result = close()
            if not inspect.isawaitable(result):
                raise TypeError("PDF renderer aclose must return an awaitable")
            await result

    def _ensure_close_task(self) -> asyncio.Task[None]:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_renderer())
        return self._close_task

    async def aclose(self) -> None:
        close_task = self._ensure_close_task()
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await asyncio.shield(close_task)
            raise

    async def _visual_pages(
        self,
        pdf_path: Path,
        pages: tuple[PageFacts, ...],
        decisions: tuple[PageRouteDecision, ...],
        deadline: float,
        concurrency: int,
    ) -> tuple[tuple[PageVisionResult, ...], tuple[WarningRecord, ...], tuple[OpenDocsError, ...]]:
        page_map = {page.page_number: page for page in pages}
        warnings: list[WarningRecord] = []
        visual_targets: list[tuple[PageFacts, PageRouteDecision]] = []
        for decision in decisions:
            page = page_map[decision.page_number]
            if decision.route is PageRoute.BLANK:
                warnings.append(_warning("blank_page", page.page_number))
            elif decision.route is PageRoute.NATIVE:
                continue
            elif self._vision is None or self._vision_config is None:
                warnings.append(_warning("vision_unavailable_native_only", page.page_number))
            else:
                visual_targets.append((page, decision))
        if not visual_targets:
            return (), tuple(warnings), ()

        semaphore = asyncio.Semaphore(concurrency)

        async def run_target(page: PageFacts, decision: PageRouteDecision) -> PageVisionResult:
            async with semaphore:
                return await self._visual_page(pdf_path, page, decision, deadline)

        tasks = [
            asyncio.create_task(run_target(page, decision)) for page, decision in visual_targets
        ]
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        results: list[PageVisionResult] = []
        failures: list[OpenDocsError] = []
        for (page, _decision), outcome in zip(visual_targets, outcomes, strict=True):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, _FATAL_VISUAL_ERRORS):
                raise outcome
            if isinstance(outcome, BaseException):
                error = (
                    outcome
                    if isinstance(outcome, OpenDocsError)
                    else RuntimeDependencyError("PDF visual processing failed")
                )
                failures.append(error)
                warnings.append(_warning("visual_processing_failed", page.page_number))
                results.append(PageVisionResult(page.page_number, PageRoute.NATIVE, (), None))
            else:
                results.append(outcome)
        return tuple(results), tuple(warnings), tuple(failures)

    async def _visual_page(
        self,
        pdf_path: Path,
        page: PageFacts,
        decision: PageRouteDecision,
        deadline: float,
    ) -> PageVisionResult:
        async with self._renderer.render_page(
            pdf_path,
            page,
            deadline=deadline,
        ) as rendered:
            if decision.route is PageRoute.FULL_VISION:
                return await self._full_page(page, rendered)
            try:
                return await self._hybrid_page(page, decision.regions, rendered)
            except _UnreliableCropError:
                return await self._full_page(page, rendered)

    async def _analyze_prepared(
        self,
        prepared: PreparedImage,
        *,
        prompt: str,
        source_index: int,
        kind: VisionRequestKind,
        coordinate_space: str | None = None,
    ) -> tuple[VisionResult, tuple[OpenDocsError, ...]]:
        if self._vision is None:
            raise VisionRequiredError("PDF page requires vision")
        paths = prepared_paths(prepared, self._runtime.workspace.path)
        requests = [
            VisionRequest(
                path,
                tile_prompt(prompt, index, len(paths)),
                source_index if len(paths) == 1 else source_index * 10_000 + index,
                kind,
                coordinate_space,
            )
            for index, path in enumerate(paths)
        ]
        try:
            outcomes = await asyncio.gather(
                *(self._vision.analyze(request) for request in requests),
                return_exceptions=True,
            )
        finally:
            for path in paths:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
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
                failures.append(RuntimeDependencyError("PDF visual processing failed"))
                results.append(None)
            elif isinstance(outcome, VisionResult):
                results.append(outcome)
            else:
                failures.append(RuntimeDependencyError("PDF vision client returned invalid data"))
                results.append(None)
        if failures and all(result is None for result in results):
            raise failures[0]
        return merge_tiled_results(prepared, results), tuple(failures)

    async def _full_page(
        self,
        page: PageFacts,
        rendered: RenderedPdfPage,
    ) -> PageVisionResult:
        prepared = await self._runtime.run_native(
            prepare_image,
            rendered.image_path,
            self._runtime.workspace.path,
            f"pdf-page-{page.page_number}-{uuid.uuid4().hex}",
            None,
            "full_page",
        )
        if bool(prepared.get("skipped")):
            return PageVisionResult(page.page_number, PageRoute.FULL_VISION, (), None)
        result, failures = await self._analyze_prepared(
            prepared,
            prompt=(
                f"{_FULL_PAGE_PROMPT.rstrip()}\nIgnore decorative icons and continue extracting "
                "all other page content.\n"
            ),
            source_index=page.page_number - 1,
            kind=VisionRequestKind.FULL_PAGE,
        )
        warnings = (_warning("visual_page_tile_failed", page.page_number),) if failures else ()
        return PageVisionResult(
            page.page_number,
            PageRoute.FULL_VISION,
            result.elements,
            None,
            warnings,
        )

    async def _hybrid_page(
        self,
        page: PageFacts,
        regions: tuple[VisualRegion, ...],
        rendered: RenderedPdfPage,
    ) -> PageVisionResult:
        if self._vision is None or not regions:
            raise ValueError("hybrid page requires vision regions")
        transform = rendered.transform
        crop_left, crop_top, _, _ = transform.crop_pixel_box
        crops: list[Path] = []
        prepared_regions: list[tuple[VisualRegion, BBox, PreparedImage]] = []
        try:
            for region_index, region in enumerate(regions):
                try:
                    full_pixels = transform.page_to_pixels(region.bbox)
                    actual_crop_bbox = transform.pixels_to_page(full_pixels)
                except (TypeError, ValueError) as error:
                    raise _UnreliableCropError("PDF hybrid crop is not invertible") from error
                local_pixels = (
                    full_pixels[0] - crop_left,
                    full_pixels[1] - crop_top,
                    full_pixels[2] - crop_left,
                    full_pixels[3] - crop_top,
                )
                token = uuid.uuid4().hex
                crop_path = self._runtime.workspace.output_path(
                    f"pdf-crop-{page.page_number}-{region_index}-{token}.png"
                )
                crops.append(crop_path)
                await self._runtime.run_native(
                    _crop_page_image,
                    rendered.image_path,
                    crop_path,
                    local_pixels,
                )
                prepared = await self._runtime.run_native(
                    prepare_image,
                    crop_path,
                    self._runtime.workspace.path,
                    f"pdf-crop-prepared-{page.page_number}-{region_index}-{token}",
                    None,
                    "hybrid_crop",
                )
                if bool(prepared.get("skipped")):
                    continue
                prepared_regions.append((region, actual_crop_bbox, prepared))

            async def analyze_region(
                region: VisualRegion,
                actual_crop_bbox: BBox,
                prepared: PreparedImage,
            ) -> tuple[VisionResult, tuple[OpenDocsError, ...]]:
                outcome, tile_failures = await self._analyze_prepared(
                    prepared,
                    prompt=_HYBRID_PROMPT,
                    source_index=page.page_number * 10_000 + region.source_index,
                    kind=VisionRequestKind.HYBRID_CROP,
                    coordinate_space=CROP_NORMALIZED_V1,
                )
                try:
                    mapped = map_result_to_bbox(outcome, actual_crop_bbox)
                except (TypeError, ValueError) as error:
                    raise ModelInvalidResponseError(
                        "hybrid vision result could not be mapped to the PDF page"
                    ) from error
                return mapped, tile_failures

            analyzed = await asyncio.gather(
                *(
                    analyze_region(region, bbox, prepared)
                    for region, bbox, prepared in prepared_regions
                ),
                return_exceptions=True,
            )
            mapped: list[VisionElement] = []
            warnings: list[WarningRecord] = []
            failures: list[OpenDocsError] = []
            for (region, _, _), result in zip(prepared_regions, analyzed, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, _FATAL_VISUAL_ERRORS):
                    raise result
                if isinstance(result, OpenDocsError):
                    failures.append(result)
                    warnings.append(
                        _warning(
                            "visual_region_failed",
                            page.page_number,
                            f"region {region.source_index}",
                        )
                    )
                    continue
                if isinstance(result, BaseException):
                    raise RuntimeDependencyError("PDF hybrid visual processing failed") from result
                outcome, tile_failures = result
                failures.extend(tile_failures)
                if tile_failures:
                    warnings.append(
                        _warning(
                            "visual_region_tile_failed",
                            page.page_number,
                            f"region {region.source_index}",
                        )
                    )
                mapped.extend(outcome.elements)
            if failures and not mapped:
                raise failures[0]
            return PageVisionResult(
                page.page_number,
                PageRoute.HYBRID,
                tuple(mapped),
                PAGE_NORMALIZED_V1,
                tuple(warnings),
            )
        finally:
            for _, _, prepared in prepared_regions:
                for path in prepared_paths(prepared, self._runtime.workspace.path):
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
            for crop_path in crops:
                with suppress(OSError):
                    crop_path.unlink(missing_ok=True)
