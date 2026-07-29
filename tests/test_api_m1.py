from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest  # pyright: ignore[reportMissingImports]
from PIL import Image  # pyright: ignore[reportMissingImports]

import opendocs.api as api_module
from opendocs import (
    DocumentTimeoutError,
    NoUsableContentError,
    ParseOptions,
    VisionConfig,
    aparse,
    parse,
)
from opendocs._models import BBox, DocumentType, ParsedDocument, TextBlock
from opendocs.parsers.pdf.extract import measure_text_quality
from opendocs.parsers.pdf.models import PageFacts
from opendocs.parsers.pdf.render import PopplerRenderer
from opendocs.source import ParseWorkspace, ResolvedSource
from opendocs.vision.base import VisionRequest, VisionResult, VisionTextElement


class _FakeVisionClient:
    calls: ClassVar[list[VisionRequest]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.calls.append(request)
        return VisionResult((VisionTextElement("image content", 0),))

    async def aclose(self) -> None:
        return None


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(output, "PNG")
    return output.getvalue()


def _minimal_pdf(text: str = "hello") -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
    output.extend(trailer.encode())
    output.extend(f"startxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def test_image_api_path_bytes_stream_and_sync_async_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_module, "LiteLLMVisionClient", _FakeVisionClient)
    _FakeVisionClient.calls = []
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    config = VisionConfig(model="fake/local")
    sync_stream = io.BytesIO(data)
    sync_stream.name = "image.png"  # type: ignore[attr-defined]
    async_stream = io.BytesIO(data)
    async_stream.name = "image.png"  # type: ignore[attr-defined]

    results = [
        parse(path, vision=config),
        parse(data, vision=config),
        parse(sync_stream, vision=config),
        asyncio.run(aparse(path, vision=config)),
        asyncio.run(aparse(data, vision=config)),
        asyncio.run(aparse(async_stream, vision=config)),
    ]

    assert results == ["image content\n"] * 6
    assert len(_FakeVisionClient.calls) == 6


def test_native_pdf_api_path_bytes_stream_and_sync_async_match(tmp_path: Path) -> None:
    data = _minimal_pdf()
    path = tmp_path / "native.pdf"
    path.write_bytes(data)
    sync_stream = io.BytesIO(data)
    sync_stream.name = "native.pdf"  # type: ignore[attr-defined]
    async_stream = io.BytesIO(data)
    async_stream.name = "native.pdf"  # type: ignore[attr-defined]

    results = [
        parse(path),
        parse(data),
        parse(sync_stream),
        asyncio.run(aparse(path)),
        asyncio.run(aparse(data)),
        asyncio.run(aparse(async_stream)),
    ]

    assert len(set(results)) == 1
    assert "hello" in results[0]
    assert "<!-- page: 1 -->" in results[0]


def test_native_pdf_with_vision_config_skips_model_and_poppler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class QuietClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("init")

        async def analyze(self, _request: VisionRequest) -> VisionResult:
            raise AssertionError("native PDF must not use vision")

        async def aclose(self) -> None:
            events.append("close")

    async def forbidden_poppler(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("native PDF must not invoke Poppler")

    monkeypatch.setattr(api_module, "LiteLLMVisionClient", QuietClient)
    monkeypatch.setattr(
        "opendocs.parsers.pdf.render.PopplerRenderer._run_poppler",
        forbidden_poppler,
    )

    result = parse(_minimal_pdf(), vision=VisionConfig(model="fake/local"))

    assert "hello" in result
    assert events == ["init", "close"]


def test_blank_pdf_with_vision_config_skips_model_and_poppler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class QuietClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("init")

        async def analyze(self, _request: VisionRequest) -> VisionResult:
            raise AssertionError("blank PDF must not use vision")

        async def aclose(self) -> None:
            events.append("close")

    async def forbidden_poppler(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("blank PDF must not invoke Poppler")

    monkeypatch.setattr(api_module, "LiteLLMVisionClient", QuietClient)
    monkeypatch.setattr(
        "opendocs.parsers.pdf.render.PopplerRenderer._run_poppler",
        forbidden_poppler,
    )

    with pytest.raises(NoUsableContentError):
        parse(_minimal_pdf(""), vision=VisionConfig(model="fake/local"))

    assert events == ["init", "close"]


def test_text_with_vision_config_never_probes_or_calls_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class QuietClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("init")

        async def analyze(self, _request: VisionRequest) -> VisionResult:
            raise AssertionError("text must not use vision")

        async def aclose(self) -> None:
            events.append("close")

    monkeypatch.setattr(api_module, "LiteLLMVisionClient", QuietClient)

    assert parse(b"hello", vision=VisionConfig(model="fake/local")) == "hello\n"
    assert events == ["init", "close"]


@pytest.mark.asyncio
async def test_api_closes_runtime_when_vision_client_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def fake_workspace() -> AsyncIterator[ParseWorkspace]:
        events.append("workspace-enter")
        try:
            yield ParseWorkspace(tmp_path)
        finally:
            events.append("workspace-exit")

    class FakeRuntime:
        def __init__(self, workspace: ParseWorkspace) -> None:
            self.workspace = workspace
            events.append("runtime-init")

        async def aclose(self) -> None:
            events.append("runtime-close")

    class FailingVision:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("vision init failed")

    monkeypatch.setattr(api_module, "parse_workspace", fake_workspace)
    monkeypatch.setattr(api_module, "ParserRuntime", FakeRuntime)
    monkeypatch.setattr(api_module, "LiteLLMVisionClient", FailingVision)

    with pytest.raises(RuntimeError, match="vision init failed"):
        await api_module._parse_with_timeout(
            b"input",
            options=ParseOptions(),
            vision=VisionConfig(model="fake/local"),
        )

    assert events == ["workspace-enter", "runtime-init", "runtime-close", "workspace-exit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "timeout", "cancel"])
async def test_api_closes_client_and_runtime_before_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    events: list[str] = []
    entered = asyncio.Event()

    @asynccontextmanager
    async def fake_workspace() -> AsyncIterator[ParseWorkspace]:
        workspace = ParseWorkspace(tmp_path)
        events.append("workspace-enter")
        try:
            yield workspace
        finally:
            events.append("workspace-exit")

    @asynccontextmanager
    async def fake_source(
        _source: object,
        *,
        wait_for_cleanup_on_cancel: bool,
    ) -> AsyncIterator[ResolvedSource]:
        del wait_for_cleanup_on_cancel
        events.append("source-enter")
        try:
            yield ResolvedSource(tmp_path / "input.txt", "input.txt", False)
        finally:
            events.append("source-exit")

    class FakeRuntime:
        def __init__(self, workspace: ParseWorkspace) -> None:
            self.workspace = workspace
            events.append("runtime-init")

        async def aclose(self) -> None:
            events.append("runtime-close")

    class FakeVision:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("vision-init")

        async def aclose(self) -> None:
            events.append("vision-close")

    class FakeParser:
        async def parse(
            self,
            _source: ResolvedSource,
            *,
            options: ParseOptions,
        ) -> ParsedDocument:
            del options
            entered.set()
            if outcome == "error":
                raise RuntimeError("parse failed")
            if outcome in {"timeout", "cancel"}:
                await asyncio.Event().wait()
            return ParsedDocument(DocumentType.TEXT, (TextBlock("ok"),))

        async def aclose(self) -> None:
            events.append("parser-close")

    monkeypatch.setattr(api_module, "parse_workspace", fake_workspace)
    monkeypatch.setattr(api_module, "materialize_source", fake_source)
    monkeypatch.setattr(api_module, "ParserRuntime", FakeRuntime)
    monkeypatch.setattr(api_module, "LiteLLMVisionClient", FakeVision)
    monkeypatch.setattr(
        api_module,
        "_detect",
        lambda _source: asyncio.sleep(0, result=DocumentType.TEXT),
    )
    monkeypatch.setattr(
        api_module,
        "build_default_registry",
        lambda *_args, **_kwargs: SimpleNamespace(get=lambda _document_type: FakeParser()),
    )

    options = ParseOptions(timeout=0.02 if outcome == "timeout" else 1)
    task = asyncio.create_task(
        api_module._parse_with_timeout(
            b"input",
            options=options,
            vision=VisionConfig(model="fake/local"),
        )
    )
    if outcome == "cancel":
        await entered.wait()
        task.cancel()
    expected = {
        "success": None,
        "error": RuntimeError,
        "timeout": TimeoutError,
        "cancel": asyncio.CancelledError,
    }[outcome]
    if expected is None:
        await task
    else:
        with pytest.raises(expected):
            await task

    assert events.index("parser-close") < events.index("source-exit")
    assert events.index("source-exit") < events.index("vision-close")
    assert events.index("vision-close") < events.index("runtime-close")
    assert events.index("runtime-close") < events.index("workspace-exit")


class _EmptyStream:
    async def read(self, _size: int) -> bytes:
        return b""


class _LateProcess:
    def __init__(self, events: list[str]) -> None:
        self.returncode: int | None = None
        self.stdout = _EmptyStream()
        self.stderr = _EmptyStream()
        self._finished = asyncio.Event()
        self.events = events
        self.killed = 0

    async def wait(self) -> int:
        await self._finished.wait()
        self.events.append("process-reaped")
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.events.append("process-terminate")

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9
        self._finished.set()


def _late_page() -> PageFacts:
    box = BBox(0.0, 0.0, 100.0, 100.0)
    return PageFacts(
        1,
        box,
        box,
        0,
        100.0,
        100.0,
        (),
        (),
        (),
        (),
        measure_text_quality(""),
        1.0,
        0,
        False,
        False,
        False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
async def test_api_keeps_source_and_workspace_until_late_poppler_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
) -> None:
    events: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()
    process = _LateProcess(events)
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"%PDF-")

    async def factory(*_args: object, **_kwargs: object) -> _LateProcess:
        events.append("factory-start")
        started.set()
        await release.wait()
        events.append("factory-release")
        return process

    @asynccontextmanager
    async def fake_workspace() -> AsyncIterator[ParseWorkspace]:
        events.append("workspace-enter")
        try:
            yield ParseWorkspace(tmp_path)
        finally:
            events.append("workspace-exit")

    @asynccontextmanager
    async def fake_source(
        _source: object,
        *,
        wait_for_cleanup_on_cancel: bool,
    ) -> AsyncIterator[ResolvedSource]:
        del wait_for_cleanup_on_cancel
        events.append("source-enter")
        try:
            yield ResolvedSource(source_path, source_path.name, False)
        finally:
            events.append("source-exit")

    class FakeRuntime:
        def __init__(self, workspace: ParseWorkspace) -> None:
            self.workspace = workspace

        async def aclose(self) -> None:
            events.append("runtime-close")

    renderer = PopplerRenderer(
        ParseWorkspace(tmp_path),
        shutdown_grace=0.01,
        process_factory=factory,
    )

    class LateParser:
        async def parse(
            self,
            source: ResolvedSource,
            *,
            options: ParseOptions,
        ) -> ParsedDocument:
            async with renderer.render_page(
                source.path,
                _late_page(),
                deadline=asyncio.get_running_loop().time() + options.timeout,
            ):
                raise AssertionError("late process unexpectedly rendered")

        async def aclose(self) -> None:
            events.append("parser-close-start")
            await renderer.aclose()
            events.append("parser-close")

    parser = LateParser()
    monkeypatch.setattr(api_module, "parse_workspace", fake_workspace)
    monkeypatch.setattr(api_module, "materialize_source", fake_source)
    monkeypatch.setattr(api_module, "ParserRuntime", FakeRuntime)
    monkeypatch.setattr(
        api_module,
        "_detect",
        lambda _source: asyncio.sleep(0, result=DocumentType.PDF),
    )
    monkeypatch.setattr(
        api_module,
        "build_default_registry",
        lambda *_args, **_kwargs: SimpleNamespace(get=lambda _document_type: parser),
    )

    timeout = 0.02 if interruption == "timeout" else 1.0
    task = asyncio.create_task(
        aparse(
            source_path,
            options=ParseOptions(timeout=timeout),
        )
    )
    await started.wait()
    if interruption == "cancel":
        task.cancel()
    await asyncio.sleep(0.05)

    assert not task.done()
    assert "source-exit" not in events
    assert "workspace-exit" not in events
    release.set()

    expected = DocumentTimeoutError if interruption == "timeout" else asyncio.CancelledError
    with pytest.raises(expected):
        await asyncio.wait_for(task, timeout=0.5)

    assert process.killed == 1
    assert events.index("factory-release") < events.index("process-reaped")
    assert events.index("process-reaped") < events.index("parser-close")
    assert events.index("parser-close") < events.index("source-exit")
    assert events.index("source-exit") < events.index("runtime-close")
    assert events.index("runtime-close") < events.index("workspace-exit")
