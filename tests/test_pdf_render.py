from __future__ import annotations

import asyncio
from pathlib import Path

import pytest  # pyright: ignore[reportMissingImports]
from PIL import Image, PngImagePlugin  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox
from opendocs._runtime import ParserRuntime
from opendocs.errors import CorruptDocumentError, DocumentTimeoutError, RuntimeDependencyError
from opendocs.parsers.pdf.extract import measure_text_quality
from opendocs.parsers.pdf.models import PageFacts
from opendocs.parsers.pdf.render import PopplerRenderer
from opendocs.source import ParseWorkspace


class EmptyStream:
    async def read(self, _size: int) -> bytes:
        return b""


class FakeProcess:
    def __init__(self, *, returncode: int = 0, block: bool = False) -> None:
        self.returncode: int | None = None if block else returncode
        self._event = asyncio.Event()
        if not block:
            self._event.set()
        self.stdout = EmptyStream()
        self.stderr = EmptyStream()
        self.terminated = 0
        self.killed = 0
        self.waits = 0

    async def wait(self) -> int:
        self.waits += 1
        await self._event.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9
        self._event.set()


def _page(page_number: int = 2) -> PageFacts:
    box = BBox(-10.0, -20.0, 90.0, 80.0)
    return PageFacts(
        page_number,
        box,
        BBox(-5.0, -10.0, 85.0, 70.0),
        90,
        80.0,
        90.0,
        (),
        (),
        (),
        (),
        measure_text_quality(""),
        0.0,
        0,
        False,
        False,
        True,
    )


@pytest.mark.asyncio
async def test_exact_selected_page_argv_sanitizes_and_cleans_outputs(tmp_path: Path) -> None:
    pdf_path = tmp_path / "private-source.pdf"
    pdf_path.write_bytes(b"%PDF-")
    argv: list[str] = []

    async def factory(*args: str, **kwargs: object) -> FakeProcess:
        del kwargs
        argv.extend(args)
        prefix = Path(args[-1])
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("Comment", "private metadata")
        Image.new("RGBA", (200, 100), (255, 255, 255, 128)).save(
            prefix.with_suffix(".png"),
            "PNG",
            pnginfo=pnginfo,
        )
        return FakeProcess()

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        executable="fake-pdftoppm",
        long_side=1024,
        process_factory=factory,
    )
    deadline = asyncio.get_running_loop().time() + 2

    async with renderer.render_page(pdf_path, _page(), deadline=deadline) as rendered:
        assert argv[:10] == [
            "fake-pdftoppm",
            "-f",
            "2",
            "-l",
            "2",
            "-singlefile",
            "-png",
            "-scale-to",
            "1024",
            str(pdf_path),
        ]
        assert Path(argv[-1]).parent == tmp_path
        with Image.open(rendered.image_path) as image:
            image.load()
            assert image.mode == "RGB"
            assert image.info == {}
        assert rendered.transform.rotation == 90
        assert rendered.transform.crop_pixel_box != (0, 0, 200, 100)
        clean_path = rendered.image_path

    assert not clean_path.exists()
    assert not tuple(tmp_path.glob("pdf-render-*"))


@pytest.mark.asyncio
async def test_sanitizer_can_run_through_native_worker_wire_boundary(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")

    async def factory(*args: str, **_kwargs: object) -> FakeProcess:
        Image.new("RGB", (200, 100), "white").save(Path(args[-1]).with_suffix(".png"), "PNG")
        return FakeProcess()

    runtime = ParserRuntime(ParseWorkspace(tmp_path))
    renderer = PopplerRenderer(
        runtime.workspace,
        process_factory=factory,
        native_runner=runtime.run_native,
    )
    try:
        async with renderer.render_page(
            source,
            _page(),
            deadline=asyncio.get_running_loop().time() + 2,
        ) as rendered:
            assert rendered.image_path.exists()
            assert rendered.transform.rotation == 90
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_missing_nonzero_and_unexpected_output_are_sanitized(tmp_path: Path) -> None:
    deadline = asyncio.get_running_loop().time() + 2
    source = tmp_path / "secret-name.pdf"
    source.write_bytes(b"%PDF-")

    async def missing(*_args: str, **_kwargs: object) -> object:
        raise FileNotFoundError("/secret/bin/pdftoppm")

    renderer = PopplerRenderer(ParseWorkspace(tmp_path), process_factory=missing)
    with pytest.raises(RuntimeDependencyError, match="pdftoppm") as raised:
        async with renderer.render_page(source, _page(), deadline=deadline):
            pass
    assert "secret" not in str(raised.value)

    async def nonzero(*_args: str, **_kwargs: object) -> FakeProcess:
        return FakeProcess(returncode=2)

    renderer = PopplerRenderer(ParseWorkspace(tmp_path), process_factory=nonzero)
    with pytest.raises(RuntimeDependencyError, match="unsuccessfully") as raised:
        async with renderer.render_page(source, _page(), deadline=deadline):
            pass
    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_invalid_or_extra_output_is_rejected_and_cleaned(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    deadline = asyncio.get_running_loop().time() + 2

    async def invalid(*args: str, **_kwargs: object) -> FakeProcess:
        Path(args[-1]).with_suffix(".png").write_bytes(b"not a PNG")
        return FakeProcess()

    renderer = PopplerRenderer(ParseWorkspace(tmp_path), process_factory=invalid)
    with pytest.raises(CorruptDocumentError, match="invalid image output"):
        async with renderer.render_page(source, _page(), deadline=deadline):
            pass
    assert not tuple(tmp_path.glob("pdf-render-*"))

    async def extra(*args: str, **_kwargs: object) -> FakeProcess:
        prefix = Path(args[-1])
        Image.new("RGB", (20, 20), "white").save(prefix.with_suffix(".png"), "PNG")
        prefix.with_suffix(".txt").write_text("unexpected")
        return FakeProcess()

    renderer = PopplerRenderer(ParseWorkspace(tmp_path), process_factory=extra)
    with pytest.raises(RuntimeDependencyError, match="unexpected output set"):
        async with renderer.render_page(source, _page(), deadline=deadline):
            pass
    assert not tuple(tmp_path.glob("pdf-render-*"))


@pytest.mark.asyncio
async def test_timeout_terminates_kills_waits_and_cleans(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    process = FakeProcess(block=True)

    async def factory(*_args: str, **_kwargs: object) -> FakeProcess:
        return process

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        shutdown_grace=0.01,
        process_factory=factory,
    )
    with pytest.raises(DocumentTimeoutError):
        async with renderer.render_page(
            source,
            _page(),
            deadline=asyncio.get_running_loop().time() + 0.01,
        ):
            pass

    assert process.terminated == 1
    assert process.killed == 1
    assert process.waits >= 2
    assert not tuple(tmp_path.glob("pdf-render-*"))


@pytest.mark.asyncio
async def test_cancellation_reaps_process(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    process = FakeProcess(block=True)

    async def factory(*_args: str, **_kwargs: object) -> FakeProcess:
        return process

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        shutdown_grace=0.01,
        process_factory=factory,
    )

    async def run() -> None:
        async with renderer.render_page(
            source,
            _page(),
            deadline=asyncio.get_running_loop().time() + 10,
        ):
            raise AssertionError("blocked process unexpectedly rendered")

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated == 1
    assert process.killed == 1
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_timeout_returns_while_process_start_never_finishes(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    process = FakeProcess(block=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def factory(*_args: str, **_kwargs: object) -> FakeProcess:
        started.set()
        await release.wait()
        return process

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        shutdown_grace=0.01,
        process_factory=factory,
    )
    loop = asyncio.get_running_loop()
    began = loop.time()
    with pytest.raises(DocumentTimeoutError):
        async with renderer.render_page(
            source,
            _page(),
            deadline=loop.time() + 0.01,
        ):
            pass

    assert started.is_set()
    assert loop.time() - began < 0.2
    cleanup_tasks = tuple(renderer._late_cleanup_tasks)
    assert len(cleanup_tasks) == 1
    close_task = asyncio.create_task(renderer.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()
    release.set()
    await close_task
    assert process.returncode is not None
    assert process.waits >= 2
    assert not renderer._late_cleanup_tasks
    assert not renderer._start_tasks


@pytest.mark.asyncio
async def test_late_process_after_cancellation_is_reaped_in_background(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    process = FakeProcess(block=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def factory(*_args: str, **_kwargs: object) -> FakeProcess:
        started.set()
        await release.wait()
        return process

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        shutdown_grace=0.01,
        process_factory=factory,
    )

    async def run() -> None:
        async with renderer.render_page(
            source,
            _page(),
            deadline=asyncio.get_running_loop().time() + 10,
        ):
            raise AssertionError("blocked process unexpectedly rendered")

    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)

    assert process.terminated == 0
    cleanup_tasks = tuple(renderer._late_cleanup_tasks)
    assert len(cleanup_tasks) == 1
    release.set()
    await asyncio.wait_for(renderer.aclose(), timeout=0.2)
    await renderer.aclose()

    assert process.terminated == 1
    assert process.killed == 1
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_aclose_is_idempotent_and_rejects_new_render(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    renderer = PopplerRenderer(ParseWorkspace(tmp_path))

    await asyncio.gather(renderer.aclose(), renderer.aclose())

    with pytest.raises(RuntimeError, match="closed"):
        async with renderer.render_page(
            source,
            _page(),
            deadline=asyncio.get_running_loop().time() + 1,
        ):
            pass


@pytest.mark.asyncio
async def test_cancellation_during_process_start_waits_then_reaps(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-")
    process = FakeProcess(block=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def factory(*_args: str, **_kwargs: object) -> FakeProcess:
        started.set()
        await release.wait()
        return process

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        shutdown_grace=0.01,
        process_factory=factory,
    )

    async def run() -> None:
        async with renderer.render_page(
            source,
            _page(),
            deadline=asyncio.get_running_loop().time() + 10,
        ):
            raise AssertionError("blocked process unexpectedly rendered")

    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated == 1
    assert process.killed == 1
    assert process.returncode is not None
