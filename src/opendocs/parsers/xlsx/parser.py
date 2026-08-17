from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from opendocs._models import ParsedDocument
from opendocs._runtime import ParserRuntime
from opendocs.errors import DocumentTimeoutError, RuntimeDependencyError
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.xlsx.extract import extract_xlsx
from opendocs.parsers.xlsx.media import (
    XlsxVisualRequest,
    build_xlsx_visual_requests,
    prepare_xlsx_visual_artifact,
)
from opendocs.parsers.xlsx.merge import XlsxVisualOutcome, merge_xlsx_document
from opendocs.parsers.xlsx.models import (
    XlsxChartSlot,
    XlsxDocument,
    XlsxImageSlot,
    document_from_wire,
    document_to_wire,
)
from opendocs.parsers.xlsx.preflight import preflight_xlsx
from opendocs.source import ResolvedSource
from opendocs.vision.base import VisionClient, VisionRequest, VisionResult, VisionTextElement
from opendocs.vision.images import (
    PreparedImage,
    merge_tiled_results,
    prepared_paths,
    tile_prompt,
)


def _cleanup_worker_artifacts(workspace_path: Path) -> None:
    for pattern in ("xlsx-media-*", "xlsx-chart-*", "xlsx-prepared-*"):
        for artifact in workspace_path.glob(pattern):
            with suppress(OSError):
                artifact.unlink(missing_ok=True)


def _extract_xlsx_to_wire(path: Path, workspace_path: Path) -> dict[str, object]:
    preflight = preflight_xlsx(path)
    try:
        document = extract_xlsx(path, preflight, artifact_dir=workspace_path)
        return document_to_wire(document)
    except BaseException:
        _cleanup_worker_artifacts(workspace_path)
        raise


def _artifact_slot_to_wire(slot: XlsxImageSlot | XlsxChartSlot) -> dict[str, object]:
    return {
        "type": "xlsx_visual_artifact",
        "source_index": slot.source_index,
        "anchor": slot.anchor,
        "artifact_name": slot.artifact_name,
        "content_sha256": slot.content_sha256,
        "alt_text": slot.alt_text,
        "object_name": slot.object_name,
        "title": slot.title,
    }


def _artifact_slot_from_wire(value: object) -> XlsxImageSlot:
    if not isinstance(value, dict) or set(value) != {
        "type",
        "source_index",
        "anchor",
        "artifact_name",
        "content_sha256",
        "alt_text",
        "object_name",
        "title",
    }:
        raise ValueError("XLSX visual artifact wire is invalid")
    if value.get("type") != "xlsx_visual_artifact":
        raise ValueError("XLSX visual artifact wire is invalid")
    payload = cast(dict[str, object], value)
    return XlsxImageSlot(
        source_index=cast(int, payload["source_index"]),
        anchor=cast(str, payload["anchor"]),
        artifact_name=cast(str, payload["artifact_name"]),
        content_sha256=cast(str, payload["content_sha256"]),
        alt_text=cast(str | None, payload["alt_text"]),
        object_name=cast(str | None, payload["object_name"]),
        title=cast(str | None, payload["title"]),
    )


def _prepare_xlsx_visual_to_wire(
    slot_wire: dict[str, object],
    artifact_dir: Path,
    output_directory: Path,
    output_stem: str,
) -> PreparedImage:
    return prepare_xlsx_visual_artifact(
        _artifact_slot_from_wire(slot_wire),
        artifact_dir,
        output_directory,
        output_stem,
    )


def _visual_slots(document: XlsxDocument) -> tuple[XlsxImageSlot | XlsxChartSlot, ...]:
    return tuple(
        slot
        for sheet in document.sheets
        for slot in sheet.slots
        if isinstance(slot, XlsxImageSlot | XlsxChartSlot)
    )


def _unique_requests(
    document: XlsxDocument,
    artifact_dir: Path,
) -> tuple[XlsxVisualRequest, ...]:
    unique: list[XlsxVisualRequest] = []
    seen: set[str] = set()
    for request in build_xlsx_visual_requests(document, artifact_dir):
        if request.digest in seen:
            continue
        seen.add(request.digest)
        unique.append(request)
    return tuple(unique)


def _has_visual_content(result: VisionResult) -> bool:
    return any(
        not isinstance(element, VisionTextElement) or bool(element.text.strip())
        for element in result.elements
    )


@dataclass(frozen=True, slots=True)
class _PreparedVisual:
    request: XlsxVisualRequest
    prepared: PreparedImage
    paths: tuple[Path, ...]


class XlsxParser:
    def __init__(
        self,
        runtime: ParserRuntime,
        vision: VisionClient | None,
        vision_config: VisionConfig | None,
        *,
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

    async def parse(
        self,
        source: ResolvedSource,
        *,
        options: ParseOptions,
    ) -> ParsedDocument:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(options.timeout)
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        try:
            async with asyncio.timeout_at(deadline):
                document = await self._extract(source)
                visual_outcomes = await self._visual_outcomes(document)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise DocumentTimeoutError("XLSX parsing exceeded the document deadline") from None
        return merge_xlsx_document(document, visual_outcomes)

    async def _extract(self, source: ResolvedSource) -> XlsxDocument:
        wire = await self._runtime.run_native(
            _extract_xlsx_to_wire,
            source.path,
            self._runtime.workspace.path,
        )
        try:
            return document_from_wire(wire)
        except (TypeError, ValueError) as error:
            _cleanup_worker_artifacts(self._runtime.workspace.path)
            raise RuntimeDependencyError("native XLSX worker returned invalid data") from error

    async def _visual_outcomes(
        self,
        document: XlsxDocument,
    ) -> dict[str, XlsxVisualOutcome]:
        slots = _visual_slots(document)
        if not slots:
            return {}
        representatives: dict[str, XlsxImageSlot | XlsxChartSlot] = {}
        for slot in slots:
            representatives.setdefault(slot.content_sha256, slot)
        artifact_dir = self._runtime.workspace.path
        raw_paths = {artifact_dir / slot.artifact_name for slot in slots}
        prepared_items: list[_PreparedVisual] = []
        outcomes: dict[str, XlsxVisualOutcome] = {}
        try:
            if self._vision is None or self._vision_config is None:
                return {
                    digest: XlsxVisualOutcome(None, "xlsx_vision_unavailable")
                    for digest in representatives
                }
            for request in _unique_requests(document, artifact_dir):
                slot = representatives[request.digest]
                try:
                    prepared = await self._runtime.run_native(
                        _prepare_xlsx_visual_to_wire,
                        _artifact_slot_to_wire(slot),
                        artifact_dir,
                        artifact_dir,
                        f"xlsx-prepared-{request.source_index}",
                    )
                    paths = prepared_paths(prepared, artifact_dir)
                    if bool(prepared.get("skipped")) or not paths:
                        outcomes[request.digest] = XlsxVisualOutcome(
                            None,
                            "xlsx_vision_failed",
                        )
                        for path in paths:
                            with suppress(OSError):
                                path.unlink(missing_ok=True)
                        continue
                    prepared_items.append(_PreparedVisual(request, prepared, paths))
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    outcomes[request.digest] = XlsxVisualOutcome(None, "xlsx_vision_failed")

            analyzed = await asyncio.gather(
                *(self._analyze_prepared(item) for item in prepared_items),
                return_exceptions=True,
            )
            for item, outcome in zip(prepared_items, analyzed, strict=True):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                if isinstance(outcome, BaseException):
                    outcomes[item.request.digest] = XlsxVisualOutcome(
                        None,
                        "xlsx_vision_failed",
                    )
                else:
                    outcomes[item.request.digest] = outcome
            return outcomes
        finally:
            for item in prepared_items:
                for path in item.paths:
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
            for path in raw_paths:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            _cleanup_worker_artifacts(artifact_dir)

    async def _analyze_prepared(self, item: _PreparedVisual) -> XlsxVisualOutcome:
        vision = self._vision
        config = self._vision_config
        if vision is None or config is None:
            return XlsxVisualOutcome(None, "xlsx_vision_unavailable")

        async def analyze_tile(request: VisionRequest) -> VisionResult | BaseException | object:
            try:
                return await vision.analyze(request)
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                return error

        requests = tuple(
            VisionRequest(
                path,
                tile_prompt(item.request.prompt, tile_index, len(item.paths)),
                item.request.source_index * 10_000 + tile_index,
                item.request.kind,
            )
            for tile_index, path in enumerate(item.paths)
        )
        try:
            async with asyncio.timeout(float(config.timeout)):
                results = await asyncio.gather(*(analyze_tile(request) for request in requests))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return XlsxVisualOutcome(None, "xlsx_vision_timeout")
        if any(isinstance(result, TimeoutError) for result in results):
            return XlsxVisualOutcome(None, "xlsx_vision_timeout")
        if any(not isinstance(result, VisionResult) for result in results):
            return XlsxVisualOutcome(None, "xlsx_vision_failed")
        typed_results = cast(tuple[VisionResult, ...], results)
        if any(not _has_visual_content(result) for result in typed_results):
            return XlsxVisualOutcome(None, "xlsx_vision_failed")
        try:
            merged = merge_tiled_results(item.prepared, typed_results)
        except asyncio.CancelledError:
            raise
        except BaseException:
            return XlsxVisualOutcome(None, "xlsx_vision_failed")
        if not _has_visual_content(merged):
            return XlsxVisualOutcome(None, "xlsx_vision_failed")
        return XlsxVisualOutcome(merged)
