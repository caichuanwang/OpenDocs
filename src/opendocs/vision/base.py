from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias, cast

from opendocs._models import BBox


class VisionRequestKind(StrEnum):
    PROSE = "prose"
    TABLE = "table"
    HYBRID_CROP = "hybrid_crop"
    FULL_PAGE = "full_page"


@dataclass(frozen=True, slots=True)
class VisionRequest:
    image_path: Path
    prompt: str
    source_index: int
    kind: VisionRequestKind = VisionRequestKind.PROSE
    coordinate_space: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.image_path, Path):
            raise TypeError("image_path must be a Path")
        if not isinstance(self.prompt, str):
            raise TypeError("prompt must be a str")
        if isinstance(self.source_index, bool) or not isinstance(self.source_index, int):
            raise TypeError("source_index must be an int")
        if self.source_index < 0:
            raise ValueError("source_index must be greater than or equal to zero")
        if not isinstance(self.kind, VisionRequestKind):
            raise TypeError("kind must be a VisionRequestKind")
        if self.coordinate_space is not None and not isinstance(self.coordinate_space, str):
            raise TypeError("coordinate_space must be a str or None")

    @property
    def structured_required(self) -> bool:
        return self.kind in {VisionRequestKind.TABLE, VisionRequestKind.HYBRID_CROP}


@dataclass(frozen=True, slots=True)
class VisionTextElement:
    text: str
    source_index: int
    bbox: BBox | None = None
    type: str = "text"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a str")
        if isinstance(self.source_index, bool) or not isinstance(self.source_index, int):
            raise TypeError("source_index must be an int")
        if self.source_index < 0:
            raise ValueError("source_index must be greater than or equal to zero")
        if self.bbox is not None:
            if not isinstance(self.bbox, BBox):
                raise TypeError("bbox must be a BBox or None")
            self.bbox.require_normalized()
        if self.type != "text":
            raise ValueError('type must be "text"')


@dataclass(frozen=True, slots=True)
class VisionTableElement:
    grid: tuple[tuple[str | None, ...], ...]
    header_rows: int
    source_index: int
    bbox: BBox | None = None
    type: str = "table"

    def __post_init__(self) -> None:
        if not isinstance(self.grid, tuple) or not self.grid:
            raise TypeError("grid must be a non-empty tuple")
        width: int | None = None
        for row in self.grid:
            if not isinstance(row, tuple):
                raise TypeError("table rows must be tuples")
            if width is None:
                width = len(row)
                if width == 0:
                    raise ValueError("grid must contain at least one column")
            elif len(row) != width:
                raise ValueError("grid must be rectangular")
            if any(cell is not None and not isinstance(cell, str) for cell in row):
                raise TypeError("table cells must be str or None")
        if isinstance(self.header_rows, bool) or not isinstance(self.header_rows, int):
            raise TypeError("header_rows must be an int")
        if not 0 <= self.header_rows <= len(self.grid):
            raise ValueError("header_rows must be between zero and the number of rows")
        if isinstance(self.source_index, bool) or not isinstance(self.source_index, int):
            raise TypeError("source_index must be an int")
        if self.source_index < 0:
            raise ValueError("source_index must be greater than or equal to zero")
        if self.bbox is not None:
            if not isinstance(self.bbox, BBox):
                raise TypeError("bbox must be a BBox or None")
            self.bbox.require_normalized()
        if self.type != "table":
            raise ValueError('type must be "table"')


VisionElement: TypeAlias = VisionTextElement | VisionTableElement


@dataclass(frozen=True, slots=True)
class VisionResult:
    elements: tuple[VisionElement, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.elements, tuple):
            raise TypeError("elements must be a tuple")
        if any(
            not isinstance(item, VisionTextElement | VisionTableElement) for item in self.elements
        ):
            raise TypeError("elements must contain vision elements")


class VisionClient(Protocol):
    async def analyze(self, request: VisionRequest) -> VisionResult: ...


class DispatchAttemptKind(StrEnum):
    INITIAL = "initial"
    RETRY = "retry"
    REPAIR = "repair"


@dataclass(frozen=True, slots=True)
class DispatchTrace:
    enqueue_sequence: int
    source_index: int
    kind: DispatchAttemptKind
    retry_index: int
    repair_index: int


_MAX_REPAIR_PAYLOAD_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class DispatchAgain:
    kind: DispatchAttemptKind
    retry_index: int = 0
    repair_index: int = 0
    delay: float = 0
    repair_payload: str | None = None

    def __post_init__(self) -> None:
        if self.repair_payload is not None:
            if not isinstance(self.repair_payload, str):
                raise TypeError("repair_payload must be a str or None")
            if len(self.repair_payload) > _MAX_REPAIR_PAYLOAD_CHARS:
                raise ValueError("repair_payload exceeds the bounded state limit")


DispatchOutcome: TypeAlias = VisionResult | DispatchAgain
AttemptHandler: TypeAlias = Callable[
    [VisionRequest, DispatchAttemptKind, int, int, str | None], Awaitable[DispatchOutcome]
]


@dataclass(slots=True)
class _QueuedAttempt:
    sequence: int
    request: VisionRequest
    kind: DispatchAttemptKind
    retry_index: int
    repair_index: int
    repair_payload: str | None
    result: asyncio.Future[VisionResult]
    handler: AttemptHandler


class VisionDispatcher:
    def __init__(self, concurrency: int) -> None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int):
            raise TypeError("concurrency must be an int")
        if concurrency <= 0:
            raise ValueError("concurrency must be greater than zero")
        self._concurrency = concurrency
        self._sequence = 0
        self._closed = False
        self._queue: asyncio.Queue[_QueuedAttempt] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._results: set[asyncio.Future[VisionResult]] = set()
        self._delayed: dict[asyncio.Future[VisionResult], set[asyncio.Task[None]]] = {}
        self._running: dict[asyncio.Future[VisionResult], asyncio.Future[DispatchOutcome]] = {}
        self._close_task: asyncio.Task[None] | None = None
        self.trace: list[DispatchTrace] = []

    def _ensure_workers(self) -> None:
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker()) for _ in range(self._concurrency)]

    def _enqueue(
        self,
        request: VisionRequest,
        result: asyncio.Future[VisionResult],
        handler: AttemptHandler,
        *,
        kind: DispatchAttemptKind,
        retry_index: int = 0,
        repair_index: int = 0,
        repair_payload: str | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("vision dispatcher is closed")
        if repair_payload is not None and len(repair_payload) > _MAX_REPAIR_PAYLOAD_CHARS:
            raise ValueError("repair_payload exceeds the bounded state limit")
        sequence = self._sequence
        self._sequence += 1
        self._queue.put_nowait(
            _QueuedAttempt(
                sequence,
                request,
                kind,
                retry_index,
                repair_index,
                repair_payload,
                result,
                handler,
            )
        )

    async def _enqueue_after_delay(self, attempt: _QueuedAttempt, outcome: DispatchAgain) -> None:
        if outcome.delay > 0:
            await asyncio.sleep(outcome.delay)
        if not attempt.result.done() and not self._closed:
            self._enqueue(
                attempt.request,
                attempt.result,
                attempt.handler,
                kind=outcome.kind,
                retry_index=outcome.retry_index,
                repair_index=outcome.repair_index,
                repair_payload=outcome.repair_payload,
            )

    def _schedule_again(self, attempt: _QueuedAttempt, outcome: DispatchAgain) -> None:
        task = asyncio.create_task(self._enqueue_after_delay(attempt, outcome))
        tasks = self._delayed.setdefault(attempt.result, set())
        tasks.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            tasks.discard(completed)
            if not tasks:
                self._delayed.pop(attempt.result, None)

        task.add_done_callback(finished)

    async def _worker(self) -> None:
        while True:
            attempt = await self._queue.get()
            try:
                if attempt.result.done():
                    continue
                self.trace.append(
                    DispatchTrace(
                        attempt.sequence,
                        attempt.request.source_index,
                        attempt.kind,
                        attempt.retry_index,
                        attempt.repair_index,
                    )
                )
                handler_task = asyncio.ensure_future(
                    attempt.handler(
                        attempt.request,
                        attempt.kind,
                        attempt.retry_index,
                        attempt.repair_index,
                        attempt.repair_payload,
                    )
                )
                self._running[attempt.result] = handler_task
                try:
                    outcome = await handler_task
                except asyncio.CancelledError:
                    if self._closed:
                        raise
                    continue
                finally:
                    self._running.pop(attempt.result, None)
                if attempt.result.done():
                    continue
                if isinstance(outcome, DispatchAgain):
                    self._schedule_again(attempt, outcome)
                else:
                    attempt.result.set_result(outcome)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not attempt.result.done():
                    attempt.result.set_exception(error)
            finally:
                self._queue.task_done()

    def _purge_queue(self, results: set[asyncio.Future[VisionResult]]) -> None:
        retained: list[_QueuedAttempt] = []
        while True:
            try:
                attempt = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            if attempt.result not in results:
                retained.append(attempt)
        for attempt in retained:
            self._queue.put_nowait(attempt)

    async def _cancel_results(self, results: Sequence[asyncio.Future[VisionResult]]) -> None:
        tasks: list[asyncio.Future[object]] = []
        cancelled = set(results)
        for result in results:
            result.cancel()
            running = self._running.get(result)
            if running is not None:
                running.cancel()
                tasks.append(cast(asyncio.Future[object], running))
            for task in self._delayed.pop(result, set()):
                task.cancel()
                tasks.append(cast(asyncio.Future[object], task))
            self._results.discard(result)
        self._purge_queue(cancelled)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def dispatch(
        self,
        requests: Sequence[VisionRequest],
        handler: AttemptHandler,
    ) -> tuple[VisionResult, ...]:
        if self._closed:
            raise RuntimeError("vision dispatcher is closed")
        if not requests:
            return ()
        self._ensure_workers()
        loop = asyncio.get_running_loop()
        futures = [loop.create_future() for _ in requests]
        self._results.update(futures)
        ordered = sorted(zip(requests, futures, strict=True), key=lambda item: item[0].source_index)
        for request, future in ordered:
            self._enqueue(request, future, handler, kind=DispatchAttemptKind.INITIAL)
        try:
            return tuple(await asyncio.gather(*futures))
        except asyncio.CancelledError:
            await self._cancel_results(futures)
            raise
        except Exception:
            await self._cancel_results(futures)
            raise
        finally:
            for future in futures:
                self._results.discard(future)

    async def _close(self) -> None:
        results = tuple(self._results)
        await self._cancel_results(results)
        workers = tuple(self._workers)
        self._workers.clear()
        for worker in workers:
            worker.cancel()
        delayed = tuple(task for tasks in self._delayed.values() for task in tasks)
        self._delayed.clear()
        for task in delayed:
            task.cancel()
        if workers or delayed:
            await asyncio.gather(*workers, *delayed, return_exceptions=True)

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise
