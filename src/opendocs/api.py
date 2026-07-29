from __future__ import annotations

import asyncio
import inspect
import sys
import warnings

from opendocs._models import DocumentType, RenderResult
from opendocs._runtime import ParserRuntime
from opendocs.detection import detect_document_type
from opendocs.errors import (
    DocumentTimeoutError,
    OpenDocsError,
    OpenDocsWarning,
    SyncInAsyncContextError,
)
from opendocs.markdown import render_markdown
from opendocs.options import ParseOptions, VisionConfig
from opendocs.parsers.base import DocumentParser
from opendocs.parsers.registry import build_default_registry
from opendocs.source import ResolvedSource, Source, materialize_source, parse_workspace
from opendocs.vision.litellm import LiteLLMVisionClient


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


def _emit_public_warnings(result: RenderResult) -> None:
    for warning in result.warnings:
        warnings.warn(
            OpenDocsWarning(warning.message, code=warning.code),
            stacklevel=3,
        )


async def _close_parser(parser: DocumentParser | None) -> None:
    if parser is None:
        return
    close = getattr(parser, "aclose", None)
    if close is None:
        return
    primary_error = sys.exception()
    try:
        if not callable(close):
            raise TypeError("parser aclose must be callable")
        result = close()
        if not inspect.isawaitable(result):
            raise TypeError("parser aclose must return an awaitable")
        await result
    except BaseException as error:
        if primary_error is None:
            raise
        primary_error.add_note(f"Parser cleanup failed: {error}")


async def _close_parser_resources(
    vision_client: LiteLLMVisionClient | None,
    runtime: ParserRuntime,
) -> None:
    primary_error = sys.exception()
    cleanup_errors: list[BaseException] = []
    if vision_client is not None:
        try:
            await vision_client.aclose()
        except BaseException as error:
            cleanup_errors.append(error)
    try:
        await runtime.aclose()
    except BaseException as error:
        cleanup_errors.append(error)

    if not cleanup_errors:
        return
    if primary_error is not None:
        for error in cleanup_errors:
            primary_error.add_note(f"Parser resource cleanup failed: {error}")
        return
    raise cleanup_errors[0]


async def _parse_with_timeout(
    source: Source,
    *,
    options: ParseOptions,
    vision: VisionConfig | None,
    wait_for_cleanup_on_cancel: bool = False,
) -> RenderResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(options.timeout)
    async with parse_workspace() as workspace:
        runtime = ParserRuntime(workspace)
        vision_client: LiteLLMVisionClient | None = None
        try:
            if vision is not None:
                vision_client = LiteLLMVisionClient(
                    vision,
                    concurrency=options.vision_concurrency,
                    deadline=deadline,
                )
            async with asyncio.timeout_at(deadline):
                async with materialize_source(
                    source,
                    wait_for_cleanup_on_cancel=wait_for_cleanup_on_cancel,
                ) as resolved:
                    parser: DocumentParser | None = None
                    try:
                        document_type = await _detect(resolved)
                        parser = build_default_registry(
                            runtime,
                            vision_client,
                            vision,
                            deadline=deadline,
                        ).get(document_type)
                        document = await parser.parse(resolved, options=options)
                        return render_markdown(
                            document,
                            max_output_chars=options.max_output_chars,
                        )
                    finally:
                        await _close_parser(parser)
        finally:
            await _close_parser_resources(vision_client, runtime)


async def aparse(
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> str:
    resolved_options = _resolve_options(options)
    resolved_vision = _resolve_vision(vision)
    try:
        result = await _parse_with_timeout(
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
    _emit_public_warnings(result)
    return result.markdown


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
        try:
            result = asyncio.run(
                _parse_with_timeout(
                    source,
                    options=resolved_options,
                    vision=resolved_vision,
                    wait_for_cleanup_on_cancel=True,
                )
            )
        except TimeoutError as error:
            if not isinstance(error.__cause__, asyncio.CancelledError):
                raise
            raise DocumentTimeoutError(
                f"document parsing exceeded {resolved_options.timeout} seconds"
            ) from error
        except OpenDocsError:
            raise
        _emit_public_warnings(result)
        return result.markdown
    raise SyncInAsyncContextError()
