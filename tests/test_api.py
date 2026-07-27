from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path
from typing import Any, cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import opendocs
from opendocs import (
    DocumentTimeoutError,
    OpenDocsWarning,
    ParseOptions,
    SyncInAsyncContextError,
    UnsupportedDocumentError,
    VisionConfig,
    aparse,
    parse,
)
from opendocs._models import DocumentType
from opendocs.source import ResolvedSource


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
        (b"%PDF-1.7\n", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "image"),
        (_office_bytes("word/document.xml"), "docx"),
        (_office_bytes("ppt/presentation.xml"), "pptx"),
    ],
)
async def test_detected_future_formats_are_typed_unsupported(
    content: bytes,
    document_type: str,
) -> None:
    with pytest.raises(UnsupportedDocumentError, match=document_type):
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


@pytest.mark.asyncio
async def test_timeout_is_translated_and_temp_file_is_cleaned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_path: Path | None = None

    async def slow_detect(source: ResolvedSource) -> DocumentType:
        nonlocal materialized_path
        materialized_path = source.path
        await asyncio.sleep(1)
        return DocumentType.TEXT

    monkeypatch.setattr("opendocs.api._detect", slow_detect)

    with pytest.raises(DocumentTimeoutError):
        await aparse(b"hello", options=ParseOptions(timeout=0.01))

    assert materialized_path is not None
    assert not materialized_path.exists()


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


def test_public_all_exposes_only_the_documented_surface() -> None:
    assert opendocs.__all__ == [
        "CorruptDocumentError",
        "DocumentTimeoutError",
        "DocumentTypeMismatchError",
        "InvalidSourceError",
        "LimitExceededError",
        "NoUsableContentError",
        "OpenDocsError",
        "OpenDocsErrorCode",
        "OpenDocsWarning",
        "ParseOptions",
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
