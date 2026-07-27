from __future__ import annotations

import asyncio
import os
import tempfile
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeAlias, TypeGuard
from urllib.parse import urlparse

from opendocs.errors import InvalidSourceError, LimitExceededError, OpenDocsWarning

Source: TypeAlias = str | os.PathLike[str] | bytes | BinaryIO
_MAX_INPUT_BYTES = 100_000_000
_REMOTE_SCHEMES = frozenset({"http", "https", "s3", "oss"})
_SOURCE_CLEANUP_FAILED_WARNING_CODE = "source_cleanup_failed"


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    path: Path
    original_name: str | None
    owned: bool

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("path must be a Path")
        if self.original_name is not None and not isinstance(self.original_name, str):
            raise TypeError("original_name must be a str or None")
        if not isinstance(self.owned, bool):
            raise TypeError("owned must be a bool")


def _validated_path(source: str | os.PathLike[str]) -> Path:
    value = os.fspath(source)
    if isinstance(value, bytes):
        raise InvalidSourceError("byte paths are not supported; pass file bytes instead")
    if urlparse(value).scheme.lower() in _REMOTE_SCHEMES:
        raise InvalidSourceError("OpenDocs accepts local paths and does not download remote URLs")

    path = Path(value).expanduser()
    if not path.exists():
        raise InvalidSourceError(f"source path does not exist: {path}")
    if not path.is_file():
        raise InvalidSourceError(f"source path is not a regular file: {path}")

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.read(0)
    except OSError as error:
        raise InvalidSourceError(f"source path is not readable: {path}") from error

    if size > _MAX_INPUT_BYTES:
        raise LimitExceededError(f"source exceeds {_MAX_INPUT_BYTES} bytes")

    return path.resolve()


def _original_path_name(source: str | os.PathLike[str]) -> str:
    value = os.fspath(source)
    if isinstance(value, bytes):
        raise InvalidSourceError("byte paths are not supported; pass file bytes instead")
    return Path(value).expanduser().name


def _is_path_source(source: Source) -> TypeGuard[str | os.PathLike[str]]:
    return not isinstance(source, bytes) and isinstance(source, (str, os.PathLike))


def _is_binary_stream(source: object) -> TypeGuard[BinaryIO]:
    return not isinstance(source, (str, bytes, os.PathLike)) and hasattr(source, "read")


def _read_stream(stream: BinaryIO) -> bytes:
    try:
        value = stream.read(_MAX_INPUT_BYTES + 1)
    except (OSError, ValueError) as error:
        raise InvalidSourceError("binary file object could not be read") from error

    if not isinstance(value, bytes):
        raise InvalidSourceError("binary file object read() must return bytes")
    if len(value) > _MAX_INPUT_BYTES:
        raise LimitExceededError(f"source exceeds {_MAX_INPUT_BYTES} bytes")

    return value


def _create_temporary_path(data: bytes) -> Path:
    if len(data) > _MAX_INPUT_BYTES:
        raise LimitExceededError(f"source exceeds {_MAX_INPUT_BYTES} bytes")

    descriptor, raw_path = tempfile.mkstemp(prefix="opendocs-")
    path = Path(raw_path)
    try:
        os.close(descriptor)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _write_temporary(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)


def _cleanup_finished_write(task: asyncio.Task[None], path: Path) -> None:
    if not task.cancelled():
        task.exception()
    _schedule_background_cleanup(path)


async def _unlink_if_exists(path: Path) -> None:
    await asyncio.to_thread(path.unlink, missing_ok=True)


async def _cleanup_owned_path(path: Path) -> None:
    await _unlink_if_exists(path)


def _warn_cleanup_failure(path: Path, error: BaseException) -> None:
    warnings.warn(
        OpenDocsWarning(
            f"Owned source cleanup failed for {path}: {error}",
            code=_SOURCE_CLEANUP_FAILED_WARNING_CODE,
        ),
        stacklevel=2,
    )


def _consume_task_exception(task: asyncio.Task[object], path: Path) -> None:
    if not task.cancelled():
        exception = task.exception()
        if exception is not None:
            _warn_cleanup_failure(path, exception)


def _schedule_background_cleanup(path: Path) -> None:
    cleanup_task = asyncio.create_task(_cleanup_owned_path(path))
    cleanup_task.add_done_callback(lambda task: _consume_task_exception(task, path))


async def _write_owned(data: bytes) -> Path:
    path = _create_temporary_path(data)
    write_task = asyncio.create_task(asyncio.to_thread(_write_temporary, path, data))
    try:
        await asyncio.shield(write_task)
    except asyncio.CancelledError:
        write_task.add_done_callback(lambda completed: _cleanup_finished_write(completed, path))
        raise
    except BaseException:
        await _unlink_if_exists(path)
        raise
    return path


@asynccontextmanager
async def materialize_source(source: Source) -> AsyncIterator[ResolvedSource]:
    if isinstance(source, bytes):
        data = source
        original_name = None
    elif _is_path_source(source):
        original_name = _original_path_name(source)
        path = await asyncio.to_thread(_validated_path, source)
        yield ResolvedSource(path=path, original_name=original_name, owned=False)
        return
    elif _is_binary_stream(source):
        data = await asyncio.to_thread(_read_stream, source)
        stream_name = getattr(source, "name", None)
        original_name = Path(stream_name).name if isinstance(stream_name, str) else None
    else:
        raise InvalidSourceError("source must be a local path, bytes, or a binary file object")

    path = await _write_owned(data)
    try:
        yield ResolvedSource(path=path, original_name=original_name, owned=True)
    except asyncio.CancelledError:
        _schedule_background_cleanup(path)
        raise
    except BaseException as error:
        try:
            await _cleanup_owned_path(path)
        except asyncio.CancelledError:
            _schedule_background_cleanup(path)
        except BaseException as cleanup_error:
            error.add_note(f"Owned source cleanup failed for {path}: {cleanup_error}")
        raise
    else:
        try:
            await _cleanup_owned_path(path)
        except asyncio.CancelledError:
            _schedule_background_cleanup(path)
            raise
