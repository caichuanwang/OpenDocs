import asyncio
import json
from pathlib import Path

import pytest

from benchmarks.document_parsing.evaluate_resources import (
    ResourceProbeConfig,
    measure_dispatchers,
)
from benchmarks.document_parsing.render_evidence import (
    EvidenceError,
    validate_safe_resource_record,
)
from opendocs._runtime import ParserRuntime
from opendocs.source import parse_workspace
from opendocs.vision.base import (
    DispatchAttemptKind,
    VisionDispatcher,
    VisionRequest,
    VisionRequestKind,
    VisionResult,
)
from tests.native_worker_helpers import sleep_and_echo


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 4])
async def test_resource_probe_respects_each_configured_visual_limit(limit: int) -> None:
    report = await measure_dispatchers(
        ResourceProbeConfig(
            per_parse_limits=(limit,),
            requests_per_parse=8,
            caller_limit=1,
            operation_delay=0.002,
            timeout=2,
            environment_identity="synthetic-test",
        )
    )

    assert report.passed is True
    assert report.peak_visual_by_parse == (limit,)


@pytest.mark.asyncio
async def test_simultaneous_documents_keep_independent_visual_limits() -> None:
    report = await measure_dispatchers(
        ResourceProbeConfig(
            per_parse_limits=(2, 2),
            requests_per_parse=6,
            caller_limit=2,
            operation_delay=0.01,
            timeout=2,
            environment_identity="synthetic-test",
        )
    )

    assert report.passed is True
    assert report.peak_documents == 2
    assert report.peak_visual_by_parse == (2, 2)
    assert report.peak_visual_global == 4


@pytest.mark.asyncio
async def test_caller_owned_semaphore_bounds_document_concurrency() -> None:
    report = await measure_dispatchers(
        ResourceProbeConfig(
            per_parse_limits=(1, 1, 1, 1),
            requests_per_parse=3,
            caller_limit=2,
            operation_delay=0.005,
            timeout=2,
            environment_identity="synthetic-test",
        )
    )

    assert report.passed is True
    assert report.peak_documents == 2


@pytest.mark.asyncio
async def test_dispatch_cancellation_stops_queued_work_and_awaits_active_cleanup() -> None:
    dispatcher = VisionDispatcher(concurrency=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    started = 0
    active = 0

    async def handler(
        request: VisionRequest,
        kind: DispatchAttemptKind,
        retry_index: int,
        repair_index: int,
        repair_payload: str | None,
    ) -> VisionResult:
        del request, kind, retry_index, repair_index, repair_payload
        nonlocal active, started
        started += 1
        active += 1
        entered.set()
        try:
            await release.wait()
            return VisionResult(())
        finally:
            active -= 1

    requests = tuple(
        VisionRequest(
            image_path=Path("synthetic-image.png"),
            source_index=index,
            kind=VisionRequestKind.FULL_PAGE,
            prompt="synthetic",
        )
        for index in range(4)
    )
    task = asyncio.create_task(dispatcher.dispatch(requests, handler))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.sleep(0)
    await dispatcher.aclose()

    assert started == 1
    assert active == 0


@pytest.mark.asyncio
async def test_native_timeout_reaps_worker_and_workspace() -> None:
    workspace_path = None
    runtime = None

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            async with parse_workspace() as workspace:
                workspace_path = workspace.path
                runtime = ParserRuntime(workspace, shutdown_grace=0.02)
                try:
                    await runtime.run_native(sleep_and_echo, 5.0, "late")
                finally:
                    await runtime.aclose()

    assert runtime is not None
    assert runtime.native_worker.is_alive is False
    assert workspace_path is not None
    assert not workspace_path.exists()


@pytest.mark.asyncio
async def test_resource_probe_times_out_as_a_failed_incomplete_observation() -> None:
    report = await measure_dispatchers(
        ResourceProbeConfig(
            per_parse_limits=(1,),
            requests_per_parse=2,
            caller_limit=1,
            operation_delay=1,
            timeout=0.01,
            environment_identity="synthetic-test",
        )
    )

    assert report.completed is False
    assert report.timed_out is True
    assert report.dispatchers_closed is True
    assert report.passed is False


@pytest.mark.asyncio
async def test_safe_resource_record_contains_measurements_but_no_private_content() -> None:
    report = await measure_dispatchers(
        ResourceProbeConfig(
            per_parse_limits=(1,),
            requests_per_parse=1,
            caller_limit=1,
            operation_delay=0,
            timeout=1,
            environment_identity="macos-python-3.13",
            candidate_commit="c" * 40,
            package_version="0.1.0",
            policy_digest="d" * 64,
            manifest_digest="e" * 64,
        )
    )

    record = report.to_safe_dict()
    serialized = json.dumps(record, sort_keys=True)

    validate_safe_resource_record(record)
    assert "c" * 40 in serialized
    assert "0.1.0" in serialized
    assert "macos-python-3.13" in serialized
    assert "elapsed_seconds" in serialized
    assert "source" not in serialized
    assert "markdown" not in serialized
    assert "prompt" not in serialized
    assert "provider_payload" not in serialized

    record["raw_markdown"] = "private"
    with pytest.raises(EvidenceError, match="raw_markdown"):
        validate_safe_resource_record(record)
