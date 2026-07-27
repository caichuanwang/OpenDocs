from __future__ import annotations

import asyncio
import warnings

from opendocs._models import DocumentType
from opendocs.detection import detect_document_type
from opendocs.errors import (
    DocumentTimeoutError,
    OpenDocsError,
    OpenDocsWarning,
    SyncInAsyncContextError,
)
from opendocs.markdown import render_markdown
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.registry import build_default_registry
from opendocs.source import ResolvedSource, Source, materialize_source


def _resolve_options(options: ParseOptions | None) -> ParseOptions:
    if options is None:
        return ParseOptions()
    if not isinstance(options, ParseOptions):
        raise TypeError("options must be ParseOptions or None")
    return options


def _resolve_vision(vision: VisionConfig | None) -> VisionConfig | None:
    if vision is None:
        return None
    if not isinstance(vision, VisionConfig):
        raise TypeError("vision must be VisionConfig or None")
    return vision


async def _detect(source: ResolvedSource) -> DocumentType:
    return await asyncio.to_thread(detect_document_type, source)


def _cleanup_cancelled_owned_source(source: ResolvedSource) -> None:
    if source.owned:
        source.path.unlink(missing_ok=True)


async def _parse_with_timeout(
    source: Source,
    *,
    options: ParseOptions,
    vision: VisionConfig | None,
) -> str:
    del vision
    async with asyncio.timeout(options.timeout):
        async with materialize_source(source) as resolved:
            try:
                document_type = await _detect(resolved)
                parser = build_default_registry().get(document_type)
                document = await parser.parse(resolved, options=options)
                result = render_markdown(
                    document,
                    max_output_chars=options.max_output_chars,
                )
            except asyncio.CancelledError:
                _cleanup_cancelled_owned_source(resolved)
                raise

    for warning in result.warnings:
        warnings.warn(
            OpenDocsWarning(warning.message, code=warning.code),
            stacklevel=3,
        )
    return result.markdown


async def aparse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str:
    resolved_options = _resolve_options(options)
    resolved_vision = _resolve_vision(vision)
    try:
        return await _parse_with_timeout(
            source,
            options=resolved_options,
            vision=resolved_vision,
        )
    except TimeoutError as error:
        if not isinstance(error.__cause__, asyncio.CancelledError):
            raise
        raise DocumentTimeoutError(
            f"document parsing exceeded {resolved_options.timeout} seconds"
        ) from error
    except OpenDocsError:
        raise


def parse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str:
    resolved_options = _resolve_options(options)
    resolved_vision = _resolve_vision(vision)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(aparse(source, options=resolved_options, vision=resolved_vision))
    raise SyncInAsyncContextError()
