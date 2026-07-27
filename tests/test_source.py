from __future__ import annotations

import asyncio
import io
import threading
import warnings
from pathlib import Path
from typing import Any, cast

import pytest

import opendocs.source as source_module
from opendocs import InvalidSourceError, LimitExceededError, OpenDocsWarning
from opendocs.source import materialize_source

MAX_INPUT_BYTES = 100_000_000


async def _wait_for_open_docs_warning(
    captured: list[warnings.WarningMessage],
) -> OpenDocsWarning:
    for _ in range(100):
        for item in captured:
            if isinstance(item.message, OpenDocsWarning):
                return item.message
        await asyncio.sleep(0.01)
    raise AssertionError("expected OpenDocsWarning was not captured")


@pytest.mark.asyncio
async def test_path_stays_caller_owned(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    async with materialize_source(path) as resolved:
        assert resolved.path == path.resolve()
        assert resolved.original_name == "notes.txt"
        assert resolved.owned is False

    assert path.exists()


@pytest.mark.asyncio
async def test_symlink_path_preserves_caller_visible_basename(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hello", encoding="utf-8")
    alias = tmp_path / "alias.md"
    alias.symlink_to(target)

    async with materialize_source(alias) as resolved:
        assert resolved.path == target.resolve()
        assert resolved.original_name == "alias.md"
        assert resolved.owned is False

    assert alias.exists()
    assert target.exists()


@pytest.mark.asyncio
async def test_bytes_are_removed_after_success() -> None:
    async with materialize_source(b"hello") as resolved:
        temporary_path = resolved.path
        assert temporary_path.read_bytes() == b"hello"
        assert resolved.original_name is None
        assert resolved.owned is True

    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_owned_file_is_removed_after_failure() -> None:
    temporary_path: Path | None = None

    with pytest.raises(RuntimeError, match="parser failed"):
        async with materialize_source(b"hello") as resolved:
            temporary_path = resolved.path
            raise RuntimeError("parser failed")

    assert temporary_path is not None
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_primary_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_path: Path | None = None

    async def fail_cleanup(path: Path) -> None:
        raise PermissionError(f"cleanup blocked for {path.name}")

    monkeypatch.setattr(source_module, "_cleanup_owned_path", fail_cleanup)

    with pytest.raises(RuntimeError, match="parser failed") as exc_info:
        async with materialize_source(b"hello") as resolved:
            temporary_path = resolved.path
            raise RuntimeError("parser failed")

    assert temporary_path is not None
    assert temporary_path.exists()
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("cleanup blocked" in note for note in notes)
    temporary_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_binary_stream_is_consumed_but_not_closed() -> None:
    stream = io.BytesIO(b"x# title")
    stream.name = "/tmp/outline.md"  # type: ignore[attr-defined]
    stream.read(1)

    async with materialize_source(stream) as resolved:
        assert resolved.path.read_bytes() == b"# title"
        assert resolved.original_name == "outline.md"
        assert resolved.owned is True

    assert stream.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "http://example.com/a.pdf",
        "https://example.com/a.pdf",
        "s3://bucket/a.pdf",
        "oss://bucket/a.pdf",
    ],
)
async def test_remote_urls_are_rejected(source: str) -> None:
    with pytest.raises(InvalidSourceError, match="local paths"):
        async with materialize_source(source):
            raise AssertionError("remote URL unexpectedly materialized")


@pytest.mark.asyncio
async def test_missing_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="does not exist"):
        async with materialize_source(tmp_path / "missing.txt"):
            raise AssertionError("missing path unexpectedly materialized")


@pytest.mark.asyncio
async def test_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError, match="regular file"):
        async with materialize_source(tmp_path):
            raise AssertionError("directory unexpectedly materialized")


@pytest.mark.asyncio
async def test_text_stream_is_rejected() -> None:
    with pytest.raises(InvalidSourceError, match="must return bytes"):
        async with materialize_source(cast(Any, io.StringIO("hello"))):
            raise AssertionError("text stream unexpectedly materialized")


@pytest.mark.asyncio
async def test_invalid_object_is_rejected() -> None:
    with pytest.raises(InvalidSourceError, match="source must be a local path, bytes"):
        async with materialize_source(cast(Any, object())):
            raise AssertionError("invalid object unexpectedly materialized")


@pytest.mark.asyncio
async def test_path_size_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "oversized.bin"
    with path.open("wb") as handle:
        handle.seek(MAX_INPUT_BYTES)
        handle.write(b"x")

    with pytest.raises(LimitExceededError, match="100000000 bytes"):
        async with materialize_source(path):
            raise AssertionError("oversized path unexpectedly materialized")


@pytest.mark.asyncio
async def test_bytes_size_limit_is_enforced() -> None:
    oversized = b"x" * (MAX_INPUT_BYTES + 1)

    with pytest.raises(LimitExceededError, match="100000000 bytes"):
        async with materialize_source(oversized):
            raise AssertionError("oversized bytes unexpectedly materialized")


@pytest.mark.asyncio
async def test_stream_size_limit_is_enforced() -> None:
    oversized = b"x" * (MAX_INPUT_BYTES + 1)
    stream = io.BytesIO(oversized)

    with pytest.raises(LimitExceededError, match="100000000 bytes"):
        async with materialize_source(stream):
            raise AssertionError("oversized stream unexpectedly materialized")

    assert stream.closed is False


@pytest.mark.asyncio
async def test_owned_file_is_removed_after_cancellation() -> None:
    entered = asyncio.Event()
    temporary_path: Path | None = None

    async def hold_source() -> None:
        nonlocal temporary_path
        async with materialize_source(b"hello") as resolved:
            temporary_path = resolved.path
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_source())
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
async def test_post_yield_cancellation_returns_promptly_and_cleans_eventually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release_cleanup = asyncio.Event()
    temporary_path: Path | None = None

    cleanup_impl = source_module._cleanup_owned_path

    async def blocked_cleanup(path: Path) -> None:
        cleanup_started.set()
        await release_cleanup.wait()
        await cleanup_impl(path)
        cleanup_finished.set()

    monkeypatch.setattr(source_module, "_cleanup_owned_path", blocked_cleanup)

    async def hold_source() -> None:
        nonlocal temporary_path
        async with materialize_source(b"hello") as resolved:
            temporary_path = resolved.path
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_source())
    await entered.wait()
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)
    finally:
        release_cleanup.set()

    assert temporary_path is not None
    await asyncio.wait_for(cleanup_finished.wait(), timeout=1)
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_post_yield_cancellation_warns_when_background_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release_cleanup = asyncio.Event()
    temporary_path: Path | None = None

    async def fail_cleanup(path: Path) -> None:
        await release_cleanup.wait()
        raise PermissionError(f"cleanup blocked for {path.name}")

    monkeypatch.setattr(source_module, "_cleanup_owned_path", fail_cleanup)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", OpenDocsWarning)

        async def hold_source() -> None:
            nonlocal temporary_path
            async with materialize_source(b"hello") as resolved:
                temporary_path = resolved.path
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold_source())
        await entered.wait()
        task.cancel()

        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.2)
        finally:
            release_cleanup.set()

        warning = await _wait_for_open_docs_warning(captured)

    assert temporary_path is not None
    assert warning.code == "source_cleanup_failed"
    assert str(temporary_path) in str(warning)
    assert "cleanup blocked" in str(warning)
    temporary_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cancellation_during_write_returns_promptly_and_cleans_eventually(
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

    async def materialize() -> None:
        async with materialize_source(b"hello"):
            raise AssertionError("cancelled write unexpectedly reached yield")

    task = asyncio.create_task(materialize())
    assert await asyncio.to_thread(started.wait, 1), "background write did not start"
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)
    finally:
        release.set()

    assert temporary_path is not None
    for _ in range(100):
        if not temporary_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_cancellation_during_write_warns_when_background_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release_write = threading.Event()
    release_cleanup = asyncio.Event()
    temporary_path: Path | None = None

    def delayed_write(path: Path, data: bytes) -> None:
        nonlocal temporary_path
        temporary_path = path
        started.set()
        assert release_write.wait(timeout=5), "test did not release the background write"
        path.write_bytes(data)

    async def fail_cleanup(path: Path) -> None:
        await release_cleanup.wait()
        raise PermissionError(f"cleanup blocked for {path.name}")

    monkeypatch.setattr(source_module, "_write_temporary", delayed_write)
    monkeypatch.setattr(source_module, "_cleanup_owned_path", fail_cleanup)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", OpenDocsWarning)

        async def materialize() -> None:
            async with materialize_source(b"hello"):
                raise AssertionError("cancelled write unexpectedly reached yield")

        task = asyncio.create_task(materialize())
        assert await asyncio.to_thread(started.wait, 1), "background write did not start"
        task.cancel()

        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.2)
        finally:
            release_write.set()
            release_cleanup.set()

        warning = await _wait_for_open_docs_warning(captured)

    assert temporary_path is not None
    assert warning.code == "source_cleanup_failed"
    assert str(temporary_path) in str(warning)
    assert "cleanup blocked" in str(warning)
    temporary_path.unlink(missing_ok=True)
