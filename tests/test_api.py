from __future__ import annotations

import asyncio
import inspect
import io
import threading
import time
import warnings
from pathlib import Path
from typing import Any, cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest  # pyright: ignore[reportMissingImports]

import opendocs
import opendocs.source as source_module
from opendocs import (
    CorruptDocumentError,
    DocumentTimeoutError,
    OpenDocsWarning,
    ParseOptions,
    SyncInAsyncContextError,
    VisionConfig,
    aparse,
    parse,
)
from opendocs._models import DocumentType
from opendocs.source import ResolvedSource


async def _wait_for_open_docs_warning(
    captured: list[warnings.WarningMessage],
) -> OpenDocsWarning:
    for _ in range(100):
        for item in captured:
            if isinstance(item.message, OpenDocsWarning):
                return item.message
        await asyncio.sleep(0.01)
    raise AssertionError("expected OpenDocsWarning was not captured")


def _office_bytes(member: str) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("notes.txt", b"*literal*\n", "\\*literal\\*\n"),
        ("notes.md", b"# Heading\n", "# Heading\n"),
    ],
)
def test_parse_and_aparse_match_for_paths(
    tmp_path: Path,
    name: str,
    data: bytes,
    expected: str,
) -> None:
    path = tmp_path / name
    path.write_bytes(data)

    assert parse(path) == expected
    assert parse(str(path)) == expected
    assert asyncio.run(aparse(path)) == expected
    assert path.exists()


def test_parse_and_aparse_match_for_unnamed_text_bytes_and_streams() -> None:
    data = b"*literal*\n"
    expected = "\\*literal\\*\n"

    assert parse(data) == expected
    assert asyncio.run(aparse(data)) == expected

    sync_stream = io.BytesIO(data)
    sync_stream.name = "/tmp/notes.txt"  # type: ignore[attr-defined]
    assert parse(sync_stream) == expected
    assert sync_stream.closed is False

    async_stream = io.BytesIO(data)
    async_stream.name = "/tmp/notes.txt"  # type: ignore[attr-defined]
    assert asyncio.run(aparse(async_stream)) == expected
    assert async_stream.closed is False


@pytest.mark.asyncio
async def test_parse_rejects_a_running_event_loop() -> None:
    with pytest.raises(SyncInAsyncContextError, match="await aparse"):
        parse(b"hello")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "document_type"),
    [
        (_office_bytes("word/document.xml"), "docx"),
        (_office_bytes("ppt/presentation.xml"), "pptx"),
    ],
)
async def test_detected_malformed_office_packages_raise_typed_corruption(
    content: bytes,
    document_type: str,
) -> None:
    del document_type
    with pytest.raises(CorruptDocumentError, match="relationships"):
        await aparse(content)


@pytest.mark.asyncio
async def test_output_truncation_emits_a_capturable_warning() -> None:
    with pytest.warns(OpenDocsWarning, match="block 2") as captured:
        result = await aparse(
            b"alpha\n\na much longer second block",
            options=ParseOptions(max_output_chars=10),
        )

    assert result == "alpha\n"
    assert isinstance(captured[0].message, OpenDocsWarning)
    assert captured[0].message.code == "output_truncated"
    assert captured[0].filename == __file__


def test_sync_output_truncation_warning_points_to_the_parse_caller() -> None:
    with pytest.warns(OpenDocsWarning, match="block 2") as captured:
        result = parse(
            b"alpha\n\na much longer second block",
            options=ParseOptions(max_output_chars=10),
        )

    assert result == "alpha\n"
    assert isinstance(captured[0].message, OpenDocsWarning)
    assert captured[0].message.code == "output_truncated"
    assert captured[0].filename == __file__


@pytest.mark.asyncio
async def test_timeout_during_detection_returns_promptly_when_source_cleanup_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release_cleanup = asyncio.Event()
    temporary_path: Path | None = None
    cleanup_impl = source_module._cleanup_owned_path
    real_unlink = Path.unlink

    async def slow_detect(source: ResolvedSource) -> DocumentType:
        nonlocal temporary_path
        temporary_path = source.path
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("timeout should cancel detection")

    async def blocked_cleanup(path: Path) -> None:
        cleanup_started.set()
        await release_cleanup.wait()
        await cleanup_impl(path)
        cleanup_finished.set()

    def guarded_unlink(self: Path, *, missing_ok: bool = False) -> None:
        for frame in inspect.stack():
            if frame.function == "_cleanup_cancelled_owned_source":
                raise AssertionError("api.py must not unlink owned temp files directly")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr("opendocs.api._detect", slow_detect)
    monkeypatch.setattr("opendocs.source._cleanup_owned_path", blocked_cleanup)
    monkeypatch.setattr(Path, "unlink", guarded_unlink)

    parse_task = asyncio.create_task(aparse(b"hello", options=ParseOptions(timeout=0.05)))
    await entered.wait()
    started_at = asyncio.get_running_loop().time()

    try:
        with pytest.raises(DocumentTimeoutError):
            await asyncio.wait_for(parse_task, timeout=0.3)
    finally:
        release_cleanup.set()

    assert asyncio.get_running_loop().time() - started_at < 0.3
    assert temporary_path is not None
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    await asyncio.wait_for(cleanup_finished.wait(), timeout=1)
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_timeout_during_temp_write_returns_promptly_and_cleans_eventually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    temporary_path: Path | None = None

    def delayed_write(path: Path, data: bytes) -> None:
        nonlocal temporary_path
        temporary_path = path
        started.set()
        assert release.wait(timeout=5), "test did not release the background write"
        path.write_bytes(data)

    monkeypatch.setattr("opendocs.source._write_temporary", delayed_write)
    parse_task = asyncio.create_task(aparse(b"hello", options=ParseOptions(timeout=0.05)))
    assert await asyncio.to_thread(started.wait, 1), "background write did not start"
    started_at = asyncio.get_running_loop().time()

    try:
        with pytest.raises(DocumentTimeoutError):
            await asyncio.wait_for(parse_task, timeout=0.3)
    finally:
        release.set()

    assert asyncio.get_running_loop().time() - started_at < 0.3
    assert temporary_path is not None
    for _ in range(100):
        if not temporary_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not temporary_path.exists()


def test_sync_timeout_during_detection_cleans_temp_before_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    temporary_path = tmp_path / "owned-source"
    cleanup_impl = source_module._cleanup_owned_path

    async def immediate_write(
        data: bytes,
        *,
        wait_for_cleanup_on_cancel: bool,
    ) -> Path:
        del wait_for_cleanup_on_cancel
        temporary_path.write_bytes(data)
        return temporary_path

    async def blocked_detect(source: ResolvedSource) -> DocumentType:
        assert source.path == temporary_path
        await asyncio.Event().wait()
        raise AssertionError("timeout should cancel detection")

    async def delayed_cleanup(path: Path) -> None:
        await asyncio.sleep(0.1)
        await cleanup_impl(path)

    monkeypatch.setattr(source_module, "_write_owned", immediate_write)
    monkeypatch.setattr("opendocs.api._detect", blocked_detect)
    monkeypatch.setattr(source_module, "_cleanup_owned_path", delayed_cleanup)

    try:
        with pytest.raises(DocumentTimeoutError):
            parse(b"hello", options=ParseOptions(timeout=0.01))

        assert not temporary_path.exists()
    finally:
        temporary_path.unlink(missing_ok=True)


def test_sync_timeout_during_temp_write_cleans_temp_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_path: Path | None = None

    def delayed_write(path: Path, data: bytes) -> None:
        nonlocal temporary_path
        temporary_path = path
        time.sleep(0.1)
        path.write_bytes(data)

    monkeypatch.setattr(source_module, "_write_temporary", delayed_write)

    try:
        with pytest.raises(DocumentTimeoutError):
            parse(b"hello", options=ParseOptions(timeout=0.01))

        assert temporary_path is not None
        assert not temporary_path.exists()
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def test_sync_failure_with_cancelled_cleanup_cleans_temp_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_path: Path | None = None
    cleanup_impl = source_module._cleanup_owned_path

    async def failed_detect(source: ResolvedSource) -> DocumentType:
        nonlocal temporary_path
        temporary_path = source.path
        raise RuntimeError("detection failed")

    async def delayed_cleanup(path: Path) -> None:
        await asyncio.sleep(0.1)
        await cleanup_impl(path)

    monkeypatch.setattr("opendocs.api._detect", failed_detect)
    monkeypatch.setattr(source_module, "_cleanup_owned_path", delayed_cleanup)

    try:
        with pytest.raises(RuntimeError, match="detection failed"):
            parse(b"hello", options=ParseOptions(timeout=0.01))

        assert temporary_path is not None
        assert not temporary_path.exists()
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"options": cast(Any, object())}, "options must be ParseOptions or None"),
        ({"vision": cast(Any, object())}, "vision must be VisionConfig or None"),
    ],
)
def test_parse_rejects_invalid_runtime_argument_types(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        cast(Any, parse)(b"hello", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"options": cast(Any, object())}, "options must be ParseOptions or None"),
        ({"vision": cast(Any, object())}, "vision must be VisionConfig or None"),
    ],
)
async def test_aparse_rejects_invalid_runtime_argument_types(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        await cast(Any, aparse)(b"hello", **kwargs)


def test_parse_accepts_but_does_not_use_vision_in_m0() -> None:
    assert parse(b"hello", vision=VisionConfig(model="example/model")) == "hello\n"


@pytest.mark.asyncio
async def test_timeout_cleanup_failure_warns_without_replacing_document_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    temporary_path: Path | None = None

    async def blocked_detect(source: ResolvedSource) -> DocumentType:
        nonlocal temporary_path
        temporary_path = source.path
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("blocked detect should not resume")

    async def fail_cleanup(path: Path) -> None:
        raise PermissionError(f"cleanup blocked for {path.name}")

    monkeypatch.setattr("opendocs.api._detect", blocked_detect)
    monkeypatch.setattr(source_module, "_cleanup_owned_path", fail_cleanup)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", OpenDocsWarning)
        with pytest.raises(DocumentTimeoutError):
            await aparse(b"hello", options=ParseOptions(timeout=0.05))

        warning = await _wait_for_open_docs_warning(captured)

    assert temporary_path is not None
    assert warning.code == "source_cleanup_failed"
    assert str(temporary_path) in str(warning)
    assert "cleanup blocked" in str(warning)
    temporary_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_aparse_propagates_external_cancellation_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    temporary_path: Path | None = None

    async def blocked_detect(source: ResolvedSource) -> DocumentType:
        nonlocal temporary_path
        temporary_path = source.path
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("blocked detect should not resume")

    monkeypatch.setattr("opendocs.api._detect", blocked_detect)
    task = asyncio.create_task(aparse(b"hello"))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert temporary_path is not None
    for _ in range(100):
        if not temporary_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_aparse_propagates_external_cancellation_when_source_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    temporary_path: Path | None = None

    async def blocked_detect(source: ResolvedSource) -> DocumentType:
        nonlocal temporary_path
        temporary_path = source.path
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("blocked detect should not resume")

    async def fail_cleanup(path: Path) -> None:
        raise PermissionError(f"cleanup blocked for {path.name}")

    monkeypatch.setattr("opendocs.api._detect", blocked_detect)
    monkeypatch.setattr(source_module, "_cleanup_owned_path", fail_cleanup)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", OpenDocsWarning)
        task = asyncio.create_task(aparse(b"hello"))
        await entered.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        warning = await _wait_for_open_docs_warning(captured)

    assert temporary_path is not None
    assert warning.code == "source_cleanup_failed"
    assert str(temporary_path) in str(warning)
    assert "cleanup blocked" in str(warning)
    temporary_path.unlink(missing_ok=True)


def test_public_all_exposes_only_the_documented_surface() -> None:
    assert opendocs.__all__ == [
        "CorruptDocumentError",
        "DocumentTimeoutError",
        "DocumentTypeMismatchError",
        "InvalidSourceError",
        "LimitExceededError",
        "ModelAuthenticationError",
        "ModelInvalidRequestError",
        "ModelInvalidResponseError",
        "ModelPermissionError",
        "ModelUnavailableError",
        "NoUsableContentError",
        "OpenDocsError",
        "OpenDocsErrorCode",
        "OpenDocsWarning",
        "ParseOptions",
        "RuntimeDependencyError",
        "SyncInAsyncContextError",
        "UnsupportedDocumentError",
        "VisionConfig",
        "VisionRequiredError",
        "__version__",
        "aparse",
        "parse",
    ]
    assert "DocumentType" not in opendocs.__all__
    assert "ResolvedSource" not in opendocs.__all__
