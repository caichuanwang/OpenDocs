from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, cast

from opendocs._models import BBox
from opendocs.errors import (
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelInvalidResponseError,
    ModelPermissionError,
    ModelUnavailableError,
)
from opendocs.options import VisionConfig
from opendocs.vision.base import (
    DispatchAgain,
    DispatchAttemptKind,
    VisionDispatcher,
    VisionRequest,
    VisionRequestKind,
    VisionResult,
    VisionTableElement,
    VisionTextElement,
)
from opendocs.vision.prompts import REPAIR_PROMPT, VISION_RESPONSE_FORMAT

_MAX_IMAGE_BYTES = 12 * 1024 * 1024


class _ResponseMode(IntEnum):
    PLAIN = 0
    JSON_OBJECT = 1
    STRICT_SCHEMA = 2


@dataclass(frozen=True, slots=True)
class _CompletionOutcome:
    content: str | None = None
    failure: str | None = None


def _litellm() -> Any:
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    return litellm


def _response_content(response: object) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise ValueError("vision model returned no message content")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        raise ValueError("vision model returned non-text content")
    return content.strip()


def _bbox(value: object) -> BBox | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, int | float) for item in value)
    ):
        raise ValueError("bbox must be null or a four-item numeric array")
    left, top, right, bottom = cast(list[int | float], value)
    return BBox(left, top, right, bottom).require_normalized()


def _json_object_instruction(request: VisionRequest) -> str:
    bbox_rule = (
        '"bbox" must be a normalized [left, top, right, bottom] array.'
        if request.coordinate_space is not None
        else '"bbox" may be omitted.'
    )
    table_rule = (
        "Include at least one table element."
        if request.kind is VisionRequestKind.TABLE
        else "Use the element type that matches the visible content."
    )
    return (
        'Return JSON with exactly one top-level key, "elements". '
        "Each element must be either "
        '{"type": "text", "text": "...", '
        f'"source_index": {request.source_index}}} or '
        '{"type": "table", "grid": [["..."]], "header_rows": 0, '
        f'"source_index": {request.source_index}}}. '
        f"{bbox_rule} {table_rule} Do not use any other top-level keys."
    )


def _parse_result(content: str, request: VisionRequest, *, allow_markdown: bool) -> VisionResult:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        if allow_markdown and content and not content.lstrip().startswith(("{", "[", "```")):
            return VisionResult((VisionTextElement(content, request.source_index),))
        raise ValueError("response is not valid JSON") from None
    if (
        isinstance(payload, dict)
        and set(payload) in ({"text"}, {"content"})
        and not request.structured_required
        and request.coordinate_space is None
    ):
        text = next(iter(payload.values()))
        if isinstance(text, list) and all(isinstance(item, str) for item in text):
            text = "\n".join(cast(list[str], text))
        if isinstance(text, str):
            return VisionResult((VisionTextElement(text, request.source_index),))
    if not isinstance(payload, dict) or set(payload) != {"elements"}:
        raise ValueError("response must contain only elements")
    raw_elements = payload["elements"]
    if not isinstance(raw_elements, list):
        raise ValueError("elements must be an array")
    elements: list[VisionTextElement | VisionTableElement] = []
    for index, item in enumerate(raw_elements):
        if not isinstance(item, dict):
            raise ValueError("element must be an object")
        element_type = item.get("type")
        source_index = item.get("source_index")
        bbox = _bbox(item.get("bbox"))
        if element_type == "text" and set(item) <= {"type", "text", "source_index", "bbox"}:
            text = item.get("text")
            if (
                not isinstance(text, str)
                or isinstance(source_index, bool)
                or not isinstance(source_index, int)
            ):
                raise ValueError("text element fields are invalid")
            elements.append(VisionTextElement(text, source_index, bbox))
            continue
        if element_type == "table" and set(item) <= {
            "type",
            "grid",
            "header_rows",
            "source_index",
            "bbox",
        }:
            raw_grid = item.get("grid")
            header_rows = item.get("header_rows")
            if not isinstance(raw_grid, list) or any(not isinstance(row, list) for row in raw_grid):
                raise ValueError("table grid must be an array of arrays")
            if isinstance(header_rows, bool) or not isinstance(header_rows, int):
                raise ValueError("table header_rows must be an int")
            if isinstance(source_index, bool) or not isinstance(source_index, int):
                raise ValueError("table source_index must be an int")
            rows = cast(list[list[object]], raw_grid)
            if any(cell is not None and not isinstance(cell, str) for row in rows for cell in row):
                raise ValueError("table cells must be strings or null")
            grid = cast(tuple[tuple[str | None, ...], ...], tuple(tuple(row) for row in rows))
            elements.append(VisionTableElement(grid, header_rows, source_index, bbox))
            continue
        raise ValueError(f"element {index} does not match a tagged variant")
    if request.structured_required and not elements:
        raise ValueError("structured response contains no elements")
    if request.kind is VisionRequestKind.TABLE and not any(
        isinstance(element, VisionTableElement) for element in elements
    ):
        raise ValueError("table response contains no table element")
    if request.coordinate_space is not None and any(element.bbox is None for element in elements):
        raise ValueError("coordinate response elements require bbox")
    return VisionResult(tuple(elements))


def _unsupported_response_format(error: BaseException) -> bool:
    text = str(error).lower()
    return ("response_format" in text or "json_schema" in text) and any(
        marker in text for marker in ("unsupported", "not support", "unknown", "invalid parameter")
    )


def _matches(error: BaseException, lib: Any, *names: str) -> bool:
    classes = tuple(
        value for name in names if isinstance((value := getattr(lib, name, None)), type)
    )
    return bool(classes) and isinstance(error, classes)


def _provider_failure(error: BaseException, lib: Any) -> str:
    if _matches(error, lib, "AuthenticationError"):
        return "authentication"
    if _matches(error, lib, "PermissionDeniedError"):
        return "permission"
    if _matches(error, lib, "BadRequestError"):
        return "unsupported_format" if _unsupported_response_format(error) else "invalid_request"
    if _matches(error, lib, "APIResponseValidationError", "JSONSchemaValidationError"):
        return "invalid_response"
    if _matches(
        error,
        lib,
        "RateLimitError",
        "Timeout",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIConnectionError",
        "APIError",
        "OpenAIError",
    ):
        return "unavailable"
    return "unavailable"


class LiteLLMVisionClient:
    def __init__(
        self,
        config: VisionConfig,
        *,
        concurrency: int,
        deadline: float | None = None,
    ) -> None:
        if not isinstance(config, VisionConfig):
            raise TypeError("config must be a VisionConfig")
        self._config = config
        self._deadline = deadline
        self.dispatcher = VisionDispatcher(concurrency)
        self._mode: _ResponseMode | None = None
        self._mode_lock = asyncio.Lock()

    def _remaining_timeout(self) -> float:
        timeout = float(self._config.timeout)
        if self._deadline is not None:
            timeout = min(timeout, self._deadline - asyncio.get_running_loop().time())
        if timeout <= 0:
            raise ModelUnavailableError("vision model deadline was exceeded")
        return timeout

    async def _get_mode(self) -> _ResponseMode:
        if self._mode is not None:
            return self._mode
        async with self._mode_lock:
            if self._mode is not None:
                return self._mode
            timeout = self._remaining_timeout()
            lib = _litellm()

            def probe() -> tuple[bool, bool, list[object]]:
                return (
                    lib.supports_vision(model=self._config.model),
                    bool(lib.supports_response_schema(model=self._config.model)),
                    lib.get_supported_openai_params(model=self._config.model) or [],
                )

            failure: str | None = None
            vision, strict, parameters = True, False, []
            try:
                async with asyncio.timeout(timeout):
                    vision, strict, parameters = await asyncio.to_thread(probe)
            except TimeoutError:
                failure = "vision capability probe exceeded the deadline"
            except (LookupError, TypeError, ValueError):
                vision, strict, parameters = True, False, []
            except Exception:
                failure = "vision capability probe failed"
            if failure is not None:
                raise ModelUnavailableError(failure)
            if vision is False and self._config.api_base is None:
                raise ModelInvalidRequestError("configured model does not support vision")
            self._mode = (
                _ResponseMode.STRICT_SCHEMA
                if strict
                else _ResponseMode.JSON_OBJECT
                if "response_format" in parameters
                else _ResponseMode.PLAIN
            )
            return self._mode

    async def _downgrade_mode(self, attempted: _ResponseMode) -> None:
        async with self._mode_lock:
            current = self._mode if self._mode is not None else attempted
            if current >= attempted:
                self._mode = _ResponseMode(max(_ResponseMode.PLAIN, attempted - 1))

    @staticmethod
    def _image_data_uri(path: Path) -> str:
        try:
            data = path.read_bytes()
        except OSError:
            raise ModelInvalidRequestError("sanitized image is not readable") from None
        if not data or len(data) > _MAX_IMAGE_BYTES:
            raise ModelInvalidRequestError("sanitized image exceeds the vision request limit")
        return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"

    async def _completion(
        self,
        request: VisionRequest,
        *,
        repair_payload: str | None,
        mode: _ResponseMode,
    ) -> _CompletionOutcome:
        lib = _litellm()
        image_url = self._image_data_uri(request.image_path)
        prompt = (
            f"{REPAIR_PROMPT}\nInvalid response:\n{repair_payload}"
            if repair_payload is not None
            else request.prompt
        )
        if mode is _ResponseMode.JSON_OBJECT:
            prompt = f"{prompt.rstrip()}\n{_json_object_instruction(request)}"
        content: list[dict[str, object]] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        timeout = self._remaining_timeout()
        kwargs: dict[str, object] = {
            "model": self._config.model,
            "messages": [{"role": "user", "content": content}],
            "timeout": timeout,
            "max_retries": 0,
        }
        if self._config.api_key is not None:
            kwargs["api_key"] = self._config.api_key
        if self._config.api_base is not None:
            kwargs["api_base"] = self._config.api_base
        if mode is _ResponseMode.STRICT_SCHEMA:
            kwargs["response_format"] = VISION_RESPONSE_FORMAT
        elif mode is _ResponseMode.JSON_OBJECT:
            kwargs["response_format"] = {"type": "json_object"}

        failure: str | None = None
        response: object | None = None
        try:
            async with asyncio.timeout(timeout):
                response = await lib.acompletion(**kwargs)
        except TimeoutError:
            failure = "unavailable"
        except Exception as error:
            failure = _provider_failure(error, lib)
        if failure is not None:
            return _CompletionOutcome(failure=failure)
        try:
            return _CompletionOutcome(content=_response_content(response))
        except ValueError:
            return _CompletionOutcome(failure="invalid_response")

    async def _attempt(
        self,
        request: VisionRequest,
        kind: DispatchAttemptKind,
        retry_index: int,
        repair_index: int,
        repair_payload: str | None,
    ) -> VisionResult | DispatchAgain:
        mode = await self._get_mode()
        outcome = await self._completion(
            request,
            repair_payload=repair_payload if kind is DispatchAttemptKind.REPAIR else None,
            mode=mode,
        )
        if outcome.failure == "unsupported_format" and mode is not _ResponseMode.PLAIN:
            await self._downgrade_mode(mode)
            return DispatchAgain(kind, retry_index, repair_index, repair_payload=repair_payload)
        if outcome.failure == "authentication":
            raise ModelAuthenticationError("vision model request failed (authentication)")
        if outcome.failure == "permission":
            raise ModelPermissionError("vision model request failed (permission)")
        if outcome.failure == "invalid_request":
            raise ModelInvalidRequestError("vision model request failed (invalid request)")
        if outcome.failure == "invalid_response":
            content = ""
        elif outcome.failure == "unavailable":
            if retry_index < self._config.max_retries:
                delay = min(0.1 * (2**retry_index), 1.0)
                if self._remaining_timeout() <= delay:
                    raise ModelUnavailableError("vision model deadline was exceeded")
                retry_kind = (
                    DispatchAttemptKind.REPAIR
                    if kind is DispatchAttemptKind.REPAIR
                    else DispatchAttemptKind.RETRY
                )
                return DispatchAgain(
                    retry_kind,
                    retry_index + 1,
                    repair_index,
                    delay,
                    repair_payload,
                )
            raise ModelUnavailableError("vision model request failed (unavailable)")
        else:
            content = outcome.content or ""

        allow_markdown = not request.structured_required and mode is _ResponseMode.PLAIN
        parsed: VisionResult | None = None
        with suppress(TypeError, ValueError):
            parsed = _parse_result(content, request, allow_markdown=allow_markdown)
        if parsed is not None:
            return parsed
        if repair_index == 0:
            return DispatchAgain(
                DispatchAttemptKind.REPAIR,
                retry_index,
                1,
                repair_payload=content[:10_000],
            )
        raise ModelInvalidResponseError("vision model returned invalid structured content")

    async def analyze_many(self, requests: list[VisionRequest]) -> tuple[VisionResult, ...]:
        return await self.dispatcher.dispatch(requests, self._attempt)

    async def analyze(self, request: VisionRequest) -> VisionResult:
        return (await self.analyze_many([request]))[0]

    async def aclose(self) -> None:
        await self.dispatcher.aclose()
