from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any, cast

import pytest

import opendocs._runtime as runtime_module
from opendocs._native_protocol import MAX_FRAME_BYTES, MAX_INLINE_BYTES, encode_message
from opendocs._runtime import NativeWorker, ParserRuntime
from opendocs.errors import (
    CorruptDocumentError,
    LimitExceededError,
    RuntimeDependencyError,
)
from opendocs.source import parse_workspace
from tests.native_worker_helpers import (
    dependency_versions,
    echo,
    make_bytes,
    noisy_echo,
    raise_corrupt,
    raise_limit,
    raise_unknown,
    sleep_and_echo,
    unsupported_result,
)


@pytest.mark.asyncio
async def test_native_worker_loads_dependencies_in_internal_subprocess() -> None:
    worker = NativeWorker()
    try:
        assert await worker.run(dependency_versions) == ("12.3.0", "0.11.10")
        assert worker.pid is not None
        assert worker.pid != os.getpid()
    finally:
        await worker.aclose()
    assert worker.is_alive is False


@pytest.mark.parametrize(
    ("mode", "script"),
    [
        (
            "-c",
            "import asyncio\nfrom opendocs._runtime import NativeWorker\n"
            "from tests.native_worker_helpers import echo\n"
            "async def main():\n"
            " w=NativeWorker(); print(await w.run(echo, 'ok')); await w.aclose()\n"
            "asyncio.run(main())",
        ),
        (
            "-",
            "import asyncio\nfrom opendocs._runtime import NativeWorker\n"
            "from tests.native_worker_helpers import echo\n"
            "async def main():\n"
            " w=NativeWorker(); print(await w.run(echo, 'ok')); await w.aclose()\n"
            "asyncio.run(main())\n",
        ),
    ],
)
def test_internal_worker_starts_from_python_c_or_stdin(mode: str, script: str) -> None:
    if mode == "-c":
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        completed = subprocess.run(
            [sys.executable, "-"],
            input=script,
            check=False,
            capture_output=True,
            text=True,
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function",
    [lambda value: value, cast(Any, str), cast(Any, print)],
)
async def test_native_worker_rejects_non_importable_or_non_function_callables(
    function: Any,
) -> None:
    worker = NativeWorker()
    with pytest.raises(TypeError, match="native callable"):
        await worker.run(function, "value")
    await worker.aclose()


@pytest.mark.asyncio
async def test_queued_call_cancellation_reaps_running_worker() -> None:
    worker = NativeWorker(shutdown_grace=0.03)
    first = asyncio.create_task(worker.run(sleep_and_echo, 5.0, "first"))
    for _ in range(100):
        if worker.pid is not None:
            break
        await asyncio.sleep(0.01)
    second = asyncio.create_task(worker.run(echo, "second"))
    await asyncio.sleep(0.02)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    with pytest.raises(RuntimeDependencyError, match="exited"):
        await first
    assert worker.is_alive is False
    with pytest.raises(RuntimeError, match="closed"):
        await worker.run(echo, "third")


@pytest.mark.asyncio
async def test_close_waits_for_pending_start_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_create = runtime_module.asyncio.create_subprocess_exec
    entered = asyncio.Event()
    release = asyncio.Event()
    started: list[asyncio.subprocess.Process] = []

    async def delayed_create(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        entered.set()
        await release.wait()
        process = await real_create(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(runtime_module.asyncio, "create_subprocess_exec", delayed_create)
    worker = NativeWorker(shutdown_grace=0.03)
    run_task = asyncio.create_task(worker.run(echo, "must-not-run"))
    await entered.wait()
    close_task = asyncio.create_task(worker.aclose())
    await asyncio.sleep(0.02)
    assert close_task.done() is False

    release.set()
    await close_task

    assert len(started) == 1
    assert started[0].returncode is not None
    assert worker.is_alive is False
    with pytest.raises(RuntimeError, match="closed"):
        await run_task


@pytest.mark.asyncio
async def test_concurrent_aclose_shares_shutdown_and_reaps_once() -> None:
    worker = NativeWorker(shutdown_grace=0.03)
    task = asyncio.create_task(worker.run(sleep_and_echo, 5.0, "value"))
    for _ in range(100):
        if worker.pid is not None:
            break
        await asyncio.sleep(0.01)
    await asyncio.gather(*(worker.aclose() for _ in range(10)))
    assert worker.is_alive is False
    with pytest.raises(RuntimeDependencyError, match="exited"):
        await task


@pytest.mark.asyncio
async def test_oversized_inline_argument_is_rejected_quickly_without_starting_worker() -> None:
    worker = NativeWorker(shutdown_grace=0.03)
    data = b"x" * 100_000_000
    gaps: list[float] = []
    last = asyncio.get_running_loop().time()

    async def ticker() -> None:
        nonlocal last
        while True:
            await asyncio.sleep(0.001)
            now = asyncio.get_running_loop().time()
            gaps.append(now - last)
            last = now

    ticker_task = asyncio.create_task(ticker())
    started = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeDependencyError, match="inline values exceed"):
        await worker.run(echo, data)
    elapsed = asyncio.get_running_loop().time() - started
    await asyncio.sleep(0.01)
    ticker_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ticker_task
    assert elapsed < 0.1
    assert max(gaps, default=0) < 0.05
    assert worker.pid is None
    assert worker.is_alive is False


@pytest.mark.asyncio
async def test_inline_result_at_limit_uses_framed_protocol() -> None:
    worker = NativeWorker()
    try:
        assert await worker.run(make_bytes, MAX_INLINE_BYTES // 2) == b"x" * (MAX_INLINE_BYTES // 2)
    finally:
        await worker.aclose()


@pytest.mark.asyncio
async def test_oversized_child_result_maps_to_runtime_dependency_error() -> None:
    worker = NativeWorker()
    try:
        with pytest.raises(RuntimeDependencyError, match="inline values exceed"):
            await worker.run(make_bytes, MAX_INLINE_BYTES + 1)
    finally:
        await worker.aclose()


def test_frame_limit_is_stricter_than_large_artifact_inputs() -> None:
    assert MAX_FRAME_BYTES <= 16 * 1024 * 1024
    with pytest.raises(ValueError, match="inline values exceed"):
        encode_message({"version": 1, "value": b"x" * (MAX_INLINE_BYTES + 1)})


@pytest.mark.asyncio
async def test_worker_stdout_noise_cannot_corrupt_consecutive_protocol_calls() -> None:
    worker = NativeWorker()
    try:
        assert await worker.run(noisy_echo, "first") == "first"
        assert await worker.run(noisy_echo, "second") == "second"
    finally:
        await worker.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function", "error_type", "code"),
    [
        (raise_corrupt, CorruptDocumentError, "corrupt_document"),
        (raise_limit, LimitExceededError, "limit_exceeded"),
    ],
)
async def test_child_stable_errors_are_rebuilt(function: Any, error_type: Any, code: str) -> None:
    worker = NativeWorker()
    try:
        with pytest.raises(error_type, match="child failure") as exc_info:
            await worker.run(function, "child failure")
        assert exc_info.value.code.value == code
    finally:
        await worker.aclose()


@pytest.mark.asyncio
async def test_unknown_child_error_maps_to_stable_runtime_dependency_error() -> None:
    worker = NativeWorker()
    try:
        with pytest.raises(RuntimeDependencyError, match=r"LookupError.*unknown"):
            await worker.run(raise_unknown, "unknown")
    finally:
        await worker.aclose()


@pytest.mark.asyncio
async def test_unserializable_child_result_maps_to_runtime_dependency_error() -> None:
    worker = NativeWorker()
    try:
        with pytest.raises(RuntimeDependencyError, match="message is not serializable"):
            await worker.run(unsupported_result)
    finally:
        await worker.aclose()


@pytest.mark.asyncio
async def test_parser_runtime_cancel_closes_native_worker() -> None:
    async with parse_workspace() as workspace:
        runtime = ParserRuntime(workspace, shutdown_grace=0.03)
        task = asyncio.create_task(runtime.run_native(sleep_and_echo, 5.0, "value"))
        for _ in range(100):
            if runtime.native_worker.pid is not None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.native_worker.is_alive is False
        await runtime.aclose()
