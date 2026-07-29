from __future__ import annotations

import asyncio
import uuid
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox, CoordinateTransform
from opendocs.errors import (
    CorruptDocumentError,
    DocumentTimeoutError,
    LimitExceededError,
    RuntimeDependencyError,
)
from opendocs.parsers.pdf.models import PageFacts
from opendocs.source import ParseWorkspace

_DEFAULT_LONG_SIDE = 2_048
_MAX_RENDER_WIDTH = 20_000
_MAX_RENDER_HEIGHT = 20_000
_MAX_RENDER_PIXELS = 80_000_000
_MAX_CAPTURE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class RenderedPdfPage:
    image_path: Path
    transform: CoordinateTransform


ProcessFactory = Callable[..., Any]
NativeRunner = Callable[..., Awaitable[Any]]
TransformWire = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    int,
    int,
    int,
    tuple[int, int, int, int],
]


def _box_wire(box: BBox) -> tuple[float, float, float, float]:
    return float(box.left), float(box.top), float(box.right), float(box.bottom)


def _sanitize_render_native(
    raw_path: Path,
    clean_path: Path,
    media_box: tuple[float, float, float, float],
    crop_box: tuple[float, float, float, float],
    rotation: int,
    use_crop_box: bool,
) -> TransformWire:
    transform = PopplerRenderer._sanitize_geometry(
        raw_path,
        clean_path,
        BBox(*media_box),
        BBox(*crop_box),
        rotation,
        use_crop_box=use_crop_box,
    )
    return (
        _box_wire(transform.media_box),
        _box_wire(transform.crop_box),
        transform.rotation,
        transform.raster_width,
        transform.raster_height,
        transform.crop_pixel_box,
    )


def _transform_from_wire(value: TransformWire) -> CoordinateTransform:
    media_box, crop_box, rotation, width, height, crop_pixels = value
    return CoordinateTransform(
        BBox(*crop_box),
        width,
        height,
        media_box=BBox(*media_box),
        rotation=rotation,
        crop_pixel_box=crop_pixels,
    )


class PopplerRenderer:
    def __init__(
        self,
        workspace: ParseWorkspace,
        *,
        executable: str = "pdftoppm",
        long_side: int = _DEFAULT_LONG_SIDE,
        shutdown_grace: float = 0.2,
        process_factory: ProcessFactory = asyncio.create_subprocess_exec,
        native_runner: NativeRunner | None = None,
    ) -> None:
        if not isinstance(workspace, ParseWorkspace):
            raise TypeError("workspace must be a ParseWorkspace")
        if not workspace.path.is_dir():
            raise ValueError("workspace path must be an existing directory")
        if not isinstance(executable, str) or not executable:
            raise ValueError("executable must be a non-empty string")
        if isinstance(long_side, bool) or not isinstance(long_side, int):
            raise TypeError("long_side must be an int")
        if long_side <= 0 or long_side > _MAX_RENDER_WIDTH:
            raise ValueError("long_side is outside the supported range")
        if isinstance(shutdown_grace, bool) or not isinstance(shutdown_grace, int | float):
            raise TypeError("shutdown_grace must be a real number")
        if shutdown_grace <= 0:
            raise ValueError("shutdown_grace must be greater than zero")
        self._workspace = workspace
        self._executable = executable
        self._long_side = long_side
        self._shutdown_grace = float(shutdown_grace)
        self._process_factory = process_factory
        self._native_runner = native_runner
        self._start_tasks: set[asyncio.Task[Any]] = set()
        self._late_cleanup_tasks: set[asyncio.Task[None]] = set()
        self._render_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def render_page(
        self,
        pdf_path: Path,
        page: PageFacts,
        *,
        deadline: float,
        use_crop_box: bool = True,
    ) -> AsyncIterator[RenderedPdfPage]:
        if self._closed:
            raise RuntimeError("PDF renderer is closed")
        render_task = asyncio.current_task()
        if render_task is None:
            raise RuntimeError("PDF renderer requires an asyncio task")
        self._render_tasks.add(render_task)
        token = uuid.uuid4().hex
        prefix = self._workspace.output_path(f"pdf-render-{page.page_number}-{token}")
        raw_path = prefix.with_suffix(".png")
        clean_path = self._workspace.output_path(f"pdf-page-{page.page_number}-{token}.png")
        try:
            await self._run_poppler(pdf_path, page.page_number, prefix, deadline)
            matching = tuple(prefix.parent.glob(f"{prefix.name}*"))
            if matching != (raw_path,):
                raise RuntimeDependencyError("PDF renderer produced an unexpected output set")
            if self._native_runner is None:
                transform = self._sanitize_render(
                    raw_path,
                    clean_path,
                    page,
                    use_crop_box=use_crop_box,
                )
            else:
                wire = await self._native_runner(
                    _sanitize_render_native,
                    raw_path,
                    clean_path,
                    _box_wire(page.media_box),
                    _box_wire(page.crop_box),
                    page.rotation,
                    use_crop_box,
                )
                transform = _transform_from_wire(wire)
            yield RenderedPdfPage(clean_path, transform)
        finally:
            try:
                clean_path.unlink(missing_ok=True)
                for candidate in prefix.parent.glob(f"{prefix.name}*"):
                    with suppress(OSError):
                        candidate.unlink(missing_ok=True)
            finally:
                self._render_tasks.discard(render_task)

    async def _run_poppler(
        self,
        pdf_path: Path,
        page_number: int,
        prefix: Path,
        deadline: float,
    ) -> None:
        argv = [
            self._executable,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-singlefile",
            "-png",
            "-scale-to",
            str(self._long_side),
            str(pdf_path),
            str(prefix),
        ]
        process: Any | None = None
        start_task: asyncio.Task[Any] | None = None
        try:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                start_task = asyncio.create_task(
                    self._process_factory(
                        *argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                )
                self._track_start(start_task)
                started_process = await asyncio.shield(start_task)
                process = started_process
                stdout_task = asyncio.create_task(self._read_bounded(started_process.stdout))
                stderr_task = asyncio.create_task(self._read_bounded(started_process.stderr))
                try:
                    await started_process.wait()
                    await asyncio.gather(stdout_task, stderr_task)
                finally:
                    for task in (stdout_task, stderr_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        except FileNotFoundError:
            raise RuntimeDependencyError("PDF rendering requires the pdftoppm runtime") from None
        except asyncio.CancelledError:
            process = await self._finish_pending_start(start_task, process)
            if process is not None:
                await self._stop_after_interruption(process)
            raise
        except TimeoutError:
            process = await self._finish_pending_start(start_task, process)
            if process is not None:
                await self._stop_after_interruption(process)
            raise DocumentTimeoutError("PDF rendering exceeded the document deadline") from None
        except OSError:
            if process is not None:
                await self._stop_after_interruption(process)
            raise RuntimeDependencyError("PDF renderer could not be started") from None
        except Exception:
            process = await self._finish_pending_start(start_task, process)
            if process is not None:
                await self._stop_after_interruption(process)
            raise RuntimeDependencyError("PDF renderer process failed") from None
        if process is None or process.returncode != 0:
            raise RuntimeDependencyError("PDF renderer exited unsuccessfully")

    async def _finish_pending_start(
        self,
        start_task: asyncio.Task[Any] | None,
        process: Any | None,
    ) -> Any | None:
        if process is not None or start_task is None:
            return process
        try:
            return await asyncio.wait_for(
                asyncio.shield(start_task),
                timeout=self._shutdown_grace,
            )
        except TimeoutError:
            self._schedule_late_cleanup(start_task)
        except BaseException:
            # Awaiting a completed failed/cancelled task consumes its outcome.
            pass
        return None

    def _track_start(self, start_task: asyncio.Task[Any]) -> None:
        self._start_tasks.add(start_task)

        def finished(task: asyncio.Task[Any]) -> None:
            self._start_tasks.discard(task)

        start_task.add_done_callback(finished)

    def _schedule_late_cleanup(self, start_task: asyncio.Task[Any]) -> None:
        cleanup = asyncio.create_task(self._cleanup_late_start(start_task))
        self._late_cleanup_tasks.add(cleanup)

        def finished(task: asyncio.Task[None]) -> None:
            self._late_cleanup_tasks.discard(task)
            if not task.cancelled():
                task.exception()

        cleanup.add_done_callback(finished)

    async def _cleanup_late_start(self, start_task: asyncio.Task[Any]) -> None:
        try:
            process = await asyncio.shield(start_task)
        except asyncio.CancelledError:
            if not start_task.done():
                start_task.cancel()
            with suppress(BaseException):
                await start_task
            raise
        except BaseException:
            return
        await self._stop_after_interruption(process)

    def _ensure_close_task(self) -> asyncio.Task[None]:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        return self._close_task

    async def _close(self) -> None:
        current = asyncio.current_task()
        active = tuple(task for task in self._render_tasks if task is not current)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        while self._late_cleanup_tasks:
            pending = tuple(self._late_cleanup_tasks)
            await asyncio.gather(*pending)
        if self._start_tasks:
            await asyncio.gather(*tuple(self._start_tasks), return_exceptions=True)

    async def aclose(self) -> None:
        if asyncio.current_task() in self._render_tasks:
            raise RuntimeError("PDF renderer cannot close from an active render context")
        close_task = self._ensure_close_task()
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await asyncio.shield(close_task)
            raise

    @staticmethod
    async def _read_bounded(stream: Any | None) -> bytes:
        if stream is None:
            return b""
        retained = bytearray()
        while True:
            chunk = await stream.read(8 * 1024)
            if not chunk:
                break
            remaining = _MAX_CAPTURE_BYTES - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
        return bytes(retained)

    async def _wait(self, process: Any) -> bool:
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), self._shutdown_grace)
        except TimeoutError:
            return False
        return True

    async def _stop_after_interruption(self, process: Any) -> None:
        try:
            if process.returncode is not None:
                await process.wait()
                return
            with suppress(ProcessLookupError):
                process.terminate()
            if not await self._wait(process):
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    @staticmethod
    def _full_transform(
        media_box: BBox,
        rotation: int,
        width: int,
        height: int,
    ) -> CoordinateTransform:
        return CoordinateTransform(
            media_box,
            width,
            height,
            media_box=media_box,
            rotation=rotation,
        )

    @classmethod
    def _crop_pixel_box(
        cls,
        media_box: BBox,
        crop_box: BBox,
        rotation: int,
        width: int,
        height: int,
        *,
        use_crop_box: bool,
    ) -> tuple[BBox, tuple[int, int, int, int]]:
        selected = crop_box if use_crop_box else media_box
        full = cls._full_transform(media_box, rotation, width, height)
        normalized = full.points_to_page(selected)
        return selected, full.page_to_pixels(normalized)

    @classmethod
    def _sanitize_render(
        cls,
        raw_path: Path,
        clean_path: Path,
        page: PageFacts,
        *,
        use_crop_box: bool,
    ) -> CoordinateTransform:
        return cls._sanitize_geometry(
            raw_path,
            clean_path,
            page.media_box,
            page.crop_box,
            page.rotation,
            use_crop_box=use_crop_box,
        )

    @classmethod
    def _sanitize_geometry(
        cls,
        raw_path: Path,
        clean_path: Path,
        media_box: BBox,
        crop_box: BBox,
        rotation: int,
        *,
        use_crop_box: bool,
    ) -> CoordinateTransform:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(raw_path) as candidate:
                    if candidate.format != "PNG":
                        raise ValueError("unexpected render format")
                    width, height = candidate.size
                    candidate.verify()
                if (
                    width <= 0
                    or height <= 0
                    or width > _MAX_RENDER_WIDTH
                    or height > _MAX_RENDER_HEIGHT
                    or width * height > _MAX_RENDER_PIXELS
                ):
                    raise LimitExceededError("rendered PDF page exceeds the image safety budget")
                selected, crop_pixels = cls._crop_pixel_box(
                    media_box,
                    crop_box,
                    rotation,
                    width,
                    height,
                    use_crop_box=use_crop_box,
                )
                with Image.open(raw_path) as opened:
                    opened.load()
                    cropped = opened.crop(crop_pixels)
                    try:
                        clean = cropped.convert("RGB")
                    finally:
                        cropped.close()
                    clean.info.clear()
                    clean.save(clean_path, format="PNG", optimize=False)
                    clean.close()
            return CoordinateTransform(
                selected,
                width,
                height,
                media_box=media_box,
                rotation=rotation,
                crop_pixel_box=crop_pixels,
            )
        except LimitExceededError:
            raise
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise LimitExceededError("rendered PDF page exceeds the image safety budget") from None
        except (OSError, SyntaxError, TypeError, ValueError):
            raise CorruptDocumentError("PDF renderer produced invalid image output") from None
