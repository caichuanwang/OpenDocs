from __future__ import annotations

import asyncio
import importlib
import inspect
import struct
import sys
from collections.abc import Callable
from contextlib import suppress
from typing import TypeVar, cast

from opendocs._native_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    decode_payload,
    encode_message,
)
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTimeoutError,
    DocumentTypeMismatchError,
    InvalidSourceError,
    LimitExceededError,
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelInvalidResponseError,
    ModelPermissionError,
    ModelUnavailableError,
    NoUsableContentError,
    OpenDocsError,
    OpenDocsErrorCode,
    RuntimeDependencyError,
    UnsupportedDocumentError,
    VisionRequiredError,
)
from opendocs.source import ParseWorkspace

_ResultT = TypeVar("_ResultT")
_HEADER = struct.Struct("!Q")
_ERROR_TYPES: dict[OpenDocsErrorCode, Callable[[str], OpenDocsError]] = {
    OpenDocsErrorCode.INVALID_SOURCE: InvalidSourceError,
    OpenDocsErrorCode.UNSUPPORTED_DOCUMENT: UnsupportedDocumentError,
    OpenDocsErrorCode.DOCUMENT_TYPE_MISMATCH: DocumentTypeMismatchError,
    OpenDocsErrorCode.CORRUPT_DOCUMENT: CorruptDocumentError,
    OpenDocsErrorCode.LIMIT_EXCEEDED: LimitExceededError,
    OpenDocsErrorCode.TIMEOUT: DocumentTimeoutError,
    OpenDocsErrorCode.VISION_REQUIRED: VisionRequiredError,
    OpenDocsErrorCode.MODEL_AUTHENTICATION: ModelAuthenticationError,
    OpenDocsErrorCode.MODEL_PERMISSION: ModelPermissionError,
    OpenDocsErrorCode.MODEL_INVALID_REQUEST: ModelInvalidRequestError,
    OpenDocsErrorCode.MODEL_UNAVAILABLE: ModelUnavailableError,
    OpenDocsErrorCode.MODEL_INVALID_RESPONSE: ModelInvalidResponseError,
    OpenDocsErrorCode.RUNTIME_DEPENDENCY: RuntimeDependencyError,
    OpenDocsErrorCode.NO_USABLE_CONTENT: NoUsableContentError,
}


def _callable_identity(function: Callable[..., object]) -> tuple[str, str]:
    if not inspect.isfunction(function):
        raise TypeError("native callable must be an importable top-level Python function")
    module_name = function.__module__
    qualname = function.__qualname__
    if module_name == "__main__" or "<locals>" in qualname or "<lambda>" in qualname:
        raise TypeError("native callable must not be a lambda, local function, or __main__ target")
    try:
        value: object = importlib.import_module(module_name)
        for component in qualname.split("."):
            value = getattr(value, component)
    except (AttributeError, ImportError) as error:
        raise TypeError("native callable must be importable by module and qualname") from error
    if value is not function:
        raise TypeError("native callable identity does not resolve to the supplied function")
    return module_name, qualname


def _rebuild_error(response: dict[str, object]) -> OpenDocsError:
    message = response.get("message")
    if not isinstance(message, str):
        message = "native worker returned an invalid error"
    if response.get("kind") != "opendocs":
        child_class = response.get("class")
        label = child_class if isinstance(child_class, str) else "unknown exception"
        return RuntimeDependencyError(f"native worker {label}: {message}")
    code_value = response.get("code")
    retryable = response.get("retryable")
    try:
        code = OpenDocsErrorCode(code_value)
    except (TypeError, ValueError):
        return RuntimeDependencyError(f"native worker returned unknown error code: {code_value}")
    error_type = _ERROR_TYPES.get(code)
    if error_type is None or not isinstance(retryable, bool):
        return RuntimeDependencyError(
            f"native worker returned unsupported error code: {code.value}"
        )
    error = error_type(message)
    if error.retryable != retryable:
        return RuntimeDependencyError("native worker returned inconsistent error metadata")
    return error


class NativeWorker:
    def __init__(self, *, shutdown_grace: float = 0.5) -> None:
        if isinstance(shutdown_grace, bool) or not isinstance(shutdown_grace, int | float):
            raise TypeError("shutdown_grace must be a real number")
        if shutdown_grace <= 0:
            raise ValueError("shutdown_grace must be greater than zero")
        self._shutdown_grace = float(shutdown_grace)
        self._process: asyncio.subprocess.Process | None = None
        self._start_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _start(self) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "opendocs._native_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._process = process
        if self._closed:
            await self._stop_process(process, send_stop=False)
            raise RuntimeError("native worker is closed")
        return process

    def _ensure_start_task(self) -> asyncio.Task[asyncio.subprocess.Process]:
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start())
        return self._start_task

    async def _send_frame(self, frame: bytes) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeDependencyError("native worker input stream is unavailable")
        try:
            process.stdin.write(frame)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionError) as error:
            raise RuntimeDependencyError(f"native worker request failed: {error}") from error

    async def _receive(self) -> dict[str, object]:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeDependencyError("native worker output stream is unavailable")
        try:
            header = await process.stdout.readexactly(_HEADER.size)
            (size,) = _HEADER.unpack(header)
            if size > MAX_FRAME_BYTES:
                raise RuntimeDependencyError(
                    f"native worker response exceeds {MAX_FRAME_BYTES} bytes"
                )
            payload = await process.stdout.readexactly(size)
            return cast(dict[str, object], decode_payload(payload))
        except asyncio.IncompleteReadError as error:
            raise RuntimeDependencyError(
                "native worker exited without returning a result"
            ) from error
        except ValueError as error:
            raise RuntimeDependencyError(f"native worker response failed: {error}") from error

    async def run(
        self,
        function: Callable[..., _ResultT],
        /,
        *args: object,
        **kwargs: object,
    ) -> _ResultT:
        module_name, qualname = _callable_identity(function)
        try:
            frame = encode_message(
                {
                    "version": PROTOCOL_VERSION,
                    "operation": "call",
                    "module": module_name,
                    "qualname": qualname,
                    "args": args,
                    "kwargs": kwargs,
                }
            )
        except ValueError as error:
            raise RuntimeDependencyError(f"native worker request failed: {error}") from error
        try:
            async with self._lock:
                if self._closed:
                    raise RuntimeError("native worker is closed")
                if self._process is None:
                    await asyncio.shield(self._ensure_start_task())
                if self._closed:
                    raise RuntimeError("native worker is closed")
                await self._send_frame(frame)
                response = await self._receive()
                operation = response.get("operation")
                if operation == "error":
                    raise _rebuild_error(response)
                if operation != "result" or "value" not in response:
                    raise RuntimeDependencyError("native worker returned an invalid response")
                return cast(_ResultT, response["value"])
        except asyncio.CancelledError:
            await self._close_after_cancellation()
            raise

    def _ensure_close_task(self) -> asyncio.Task[None]:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop())
        return self._close_task

    async def _close_after_cancellation(self) -> None:
        close_task = self._ensure_close_task()
        with suppress(BaseException):
            await asyncio.shield(close_task)

    async def _wait_process(self, process: asyncio.subprocess.Process, timeout: float) -> bool:
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout)
        except TimeoutError:
            return False
        return True

    async def _stop_process(self, process: asyncio.subprocess.Process, *, send_stop: bool) -> None:
        try:
            if process.returncode is None and send_stop and process.stdin is not None:
                with suppress(BrokenPipeError, ConnectionError, ValueError):
                    await self._send_frame(
                        encode_message({"version": PROTOCOL_VERSION, "operation": "stop"})
                    )
            if process.returncode is None and not await self._wait_process(
                process, self._shutdown_grace
            ):
                with suppress(ProcessLookupError):
                    process.terminate()
                if not await self._wait_process(process, self._shutdown_grace):
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        finally:
            if process.stdin is not None:
                process.stdin.close()
                with suppress(BrokenPipeError, ConnectionError):
                    await process.stdin.wait_closed()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            if self._process is process:
                self._process = None

    async def _stop(self) -> None:
        start_task = self._start_task
        if start_task is not None and not start_task.done():
            with suppress(BaseException):
                await asyncio.shield(start_task)
        process = self._process
        if process is not None:
            await self._stop_process(process, send_stop=True)

    async def aclose(self) -> None:
        close_task = self._ensure_close_task()
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await asyncio.shield(close_task)
            raise


class ParserRuntime:
    def __init__(self, workspace: ParseWorkspace, *, shutdown_grace: float = 0.5) -> None:
        if not isinstance(workspace, ParseWorkspace):
            raise TypeError("workspace must be a ParseWorkspace")
        if not workspace.path.is_dir():
            raise ValueError("workspace path must be an existing directory")
        self.workspace = workspace
        self.native_worker = NativeWorker(shutdown_grace=shutdown_grace)
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def run_native(
        self,
        function: Callable[..., _ResultT],
        /,
        *args: object,
        **kwargs: object,
    ) -> _ResultT:
        if self._closed:
            raise RuntimeError("parser runtime is closed")
        try:
            return await self.native_worker.run(function, *args, **kwargs)
        except asyncio.CancelledError:
            close_task = self._ensure_close_task()
            with suppress(BaseException):
                await asyncio.shield(close_task)
            raise

    def _ensure_close_task(self) -> asyncio.Task[None]:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self.native_worker.aclose())
        return self._close_task

    async def aclose(self) -> None:
        close_task = self._ensure_close_task()
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await asyncio.shield(close_task)
            raise
