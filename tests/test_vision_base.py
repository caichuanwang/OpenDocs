from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

import pytest

from opendocs.vision.base import (
    DispatchAgain,
    DispatchAttemptKind,
    VisionDispatcher,
    VisionRequest,
    VisionResult,
    VisionTextElement,
)


def _request(path: Path, source_index: int) -> VisionRequest:
    return VisionRequest(path, "prompt", source_index)


@pytest.mark.asyncio
async def test_dispatcher_admits_initial_requests_in_fifo_order_with_bounded_concurrency(
    tmp_path: Path,
) -> None:
    first_release = asyncio.Event()
    second_started = asyncio.Event()
    active = 0
    maximum_active = 0

    async def handler(request, kind, retry_index, repair_index, repair_payload):
        nonlocal active, maximum_active
        del kind, retry_index, repair_index, repair_payload
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            if request.source_index == 0:
                await first_release.wait()
            else:
                second_started.set()
            return VisionResult((VisionTextElement(str(request.source_index), 0),))
        finally:
            active -= 1

    dispatcher = VisionDispatcher(2)
    task = asyncio.create_task(
        dispatcher.dispatch([_request(tmp_path, 0), _request(tmp_path, 1)], handler)
    )
    await second_started.wait()
    first_release.set()
    results = await task
    await dispatcher.aclose()

    assert maximum_active == 2
    assert [item.source_index for item in dispatcher.trace] == [0, 1]
    assert [
        element.text
        for result in results
        if isinstance((element := result.elements[0]), VisionTextElement)
    ] == ["0", "1"]


@pytest.mark.asyncio
async def test_dispatcher_appends_retry_and_repair_to_fifo_tail(tmp_path: Path) -> None:
    attempts = defaultdict(int)

    async def handler(request, kind, retry_index, repair_index, repair_payload):
        del retry_index, repair_index, repair_payload
        attempts[request.source_index] += 1
        if request.source_index == 0 and kind is DispatchAttemptKind.INITIAL:
            return DispatchAgain(DispatchAttemptKind.RETRY, 1, 0)
        if request.source_index == 1 and kind is DispatchAttemptKind.INITIAL:
            return DispatchAgain(DispatchAttemptKind.REPAIR, 0, 1, repair_payload="bad")
        return VisionResult((VisionTextElement(str(request.source_index), 0),))

    dispatcher = VisionDispatcher(1)
    results = await dispatcher.dispatch([_request(tmp_path, 0), _request(tmp_path, 1)], handler)
    await dispatcher.aclose()

    assert [item.enqueue_sequence for item in dispatcher.trace] == [0, 1, 2, 3]
    assert [item.kind for item in dispatcher.trace] == [
        DispatchAttemptKind.INITIAL,
        DispatchAttemptKind.INITIAL,
        DispatchAttemptKind.RETRY,
        DispatchAttemptKind.REPAIR,
    ]
    assert [
        element.text
        for result in results
        if isinstance((element := result.elements[0]), VisionTextElement)
    ] == ["0", "1"]


@pytest.mark.asyncio
async def test_dispatcher_cancellation_does_not_close_shared_dispatcher(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request, kind, retry_index, repair_index, repair_payload):
        del request, kind, retry_index, repair_index, repair_payload
        started.set()
        await release.wait()
        return VisionResult(())

    dispatcher = VisionDispatcher(1)
    task = asyncio.create_task(dispatcher.dispatch([_request(tmp_path, 0)], handler))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    followup = asyncio.create_task(dispatcher.dispatch([_request(tmp_path, 1)], handler))
    await started.wait()
    release.set()
    assert await followup == (VisionResult(()),)
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_global_pool_bounds_concurrent_dispatches_and_keeps_fifo(
    tmp_path: Path,
) -> None:
    release = asyncio.Event()
    two_started = asyncio.Event()
    active = 0
    maximum = 0
    started: list[int] = []

    async def handler(request, kind, retry_index, repair_index, repair_payload):
        nonlocal active, maximum
        del kind, retry_index, repair_index, repair_payload
        active += 1
        maximum = max(maximum, active)
        started.append(request.source_index)
        if len(started) == 2:
            two_started.set()
        try:
            await release.wait()
            return VisionResult((VisionTextElement(str(request.source_index), 0),))
        finally:
            active -= 1

    dispatcher = VisionDispatcher(2)
    first = asyncio.create_task(dispatcher.dispatch([_request(tmp_path, 0)], handler))
    await asyncio.sleep(0)
    second = asyncio.create_task(
        dispatcher.dispatch([_request(tmp_path, 1), _request(tmp_path, 2)], handler)
    )
    await asyncio.wait_for(two_started.wait(), 1)
    assert started == [0, 1]
    assert maximum == 2
    release.set()
    await asyncio.gather(first, second)
    await dispatcher.aclose()
    assert [item.source_index for item in dispatcher.trace] == [0, 1, 2]


@pytest.mark.asyncio
async def test_cancel_one_dispatch_while_other_retries_and_repairs(tmp_path: Path) -> None:
    blocked = asyncio.Event()
    repair_payloads: list[str | None] = []

    async def handler(request, kind, retry_index, repair_index, repair_payload):
        if request.source_index == 0:
            blocked.set()
            await asyncio.Event().wait()
        repair_payloads.append(repair_payload)
        if kind is DispatchAttemptKind.INITIAL:
            return DispatchAgain(DispatchAttemptKind.REPAIR, 0, 1, repair_payload="broken")
        if kind is DispatchAttemptKind.REPAIR and retry_index == 0:
            return DispatchAgain(
                DispatchAttemptKind.REPAIR, 1, repair_index, repair_payload=repair_payload
            )
        return VisionResult((VisionTextElement("done", request.source_index),))

    dispatcher = VisionDispatcher(2)
    cancelled = asyncio.create_task(dispatcher.dispatch([_request(tmp_path, 0)], handler))
    survivor = asyncio.create_task(dispatcher.dispatch([_request(tmp_path, 1)], handler))
    await blocked.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    result = await asyncio.wait_for(survivor, 1)
    await dispatcher.aclose()
    assert result[0].elements[0] == VisionTextElement("done", 1)
    assert repair_payloads == [None, "broken", "broken"]


@pytest.mark.asyncio
async def test_reused_request_repair_payload_is_attempt_local(tmp_path: Path) -> None:
    request = _request(tmp_path, 0)
    observed: list[str | None] = []

    async def handler(request, kind, retry_index, repair_index, repair_payload):
        del request, retry_index, repair_index
        observed.append(repair_payload)
        if kind is DispatchAttemptKind.INITIAL:
            payload = "first" if observed.count(None) == 1 else "second"
            return DispatchAgain(DispatchAttemptKind.REPAIR, repair_index=1, repair_payload=payload)
        return VisionResult((VisionTextElement(repair_payload or "", 0),))

    dispatcher = VisionDispatcher(2)
    results = await asyncio.gather(
        dispatcher.dispatch([request], handler), dispatcher.dispatch([request], handler)
    )
    await dispatcher.aclose()
    assert {
        element.text
        for result in results
        if isinstance((element := result[0].elements[0]), VisionTextElement)
    } == {"first", "second"}
