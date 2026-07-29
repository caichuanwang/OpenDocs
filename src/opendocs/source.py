from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
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


@dataclass(frozen=True, slots=True)
class ParseWorkspace:
    path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("path must be a Path")

    def output_path(self, name: str) -> Path:
        if not isinstance(name, str):
            raise TypeError("output name must be a str")
        candidate = Path(name)
        windows_stem = name.split(".", 1)[0].rstrip(" ").upper()
        windows_reserved = windows_stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            len(windows_stem) == 4
            and windows_stem[:3] in {"COM", "LPT"}
            and windows_stem[3] in "123456789¹²³"
        )
        forbidden = '<>:"/\\|?*'
        if (
            not name
            or name[-1] in {" ", "."}
            or any(
                ord(character) < 32 or ord(character) == 127 or character in forbidden
                for character in name
            )
            or candidate.is_absolute()
            or candidate.name != name
            or name in {".", ".."}
            or windows_reserved
        ):
            raise ValueError("output name must be a portable non-empty basename")
        return self.path / name


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


async def _cleanup_after_cancellation(path: Path, *, wait: bool) -> None:
    if not wait:
        _schedule_background_cleanup(path)
        return

    try:
        await _cleanup_owned_path(path)
    except asyncio.CancelledError as error:
        _warn_cleanup_failure(path, error)
    except OSError as error:
        _warn_cleanup_failure(path, error)


async def _write_owned(data: bytes, *, wait_for_cleanup_on_cancel: bool) -> Path:
    path = _create_temporary_path(data)
    write_task = asyncio.create_task(asyncio.to_thread(_write_temporary, path, data))
    try:
        await asyncio.shield(write_task)
    except asyncio.CancelledError:
        if wait_for_cleanup_on_cancel:
            with suppress(asyncio.CancelledError, OSError):
                await write_task
            await _cleanup_after_cancellation(path, wait=True)
        else:
            write_task.add_done_callback(lambda completed: _cleanup_finished_write(completed, path))
        raise
    except OSError:
        await _unlink_if_exists(path)
        raise
    return path


def _create_workspace() -> Path:
    return Path(tempfile.mkdtemp(prefix="opendocs-workspace-"))


def _remove_workspace(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError as error:
        raise RuntimeError(f"failed to remove parse workspace: {path}") from error


async def _cleanup_workspace(path: Path) -> None:
    cleanup_task = asyncio.create_task(asyncio.to_thread(_remove_workspace, path))
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        with suppress(asyncio.CancelledError, RuntimeError, OSError):
            await cleanup_task
        raise


async def _cleanup_workspace_after_error(path: Path, primary_error: BaseException) -> None:
    try:
        await _cleanup_workspace(path)
    except asyncio.CancelledError as cleanup_error:
        primary_error.add_note(f"Parse workspace cleanup failed for {path}: {cleanup_error}")
    except (RuntimeError, OSError) as cleanup_error:
        primary_error.add_note(f"Parse workspace cleanup failed for {path}: {cleanup_error}")


@asynccontextmanager
async def parse_workspace() -> AsyncIterator[ParseWorkspace]:
    path = _create_workspace()
    try:
        yield ParseWorkspace(path)
    finally:
        primary_error = sys.exception()
        if primary_error is None:
            await _cleanup_workspace(path)
        else:
            await _cleanup_workspace_after_error(path, primary_error)


@asynccontextmanager
async def materialize_source(
    source: Source,
    *,
    wait_for_cleanup_on_cancel: bool = False,
) -> AsyncIterator[ResolvedSource]:
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

    path = await _write_owned(
        data,
        wait_for_cleanup_on_cancel=wait_for_cleanup_on_cancel,
    )
    try:
        yield ResolvedSource(path=path, original_name=original_name, owned=True)
    finally:
        primary_error = sys.exception()
        if isinstance(primary_error, asyncio.CancelledError):
            await _cleanup_after_cancellation(
                path,
                wait=wait_for_cleanup_on_cancel,
            )
        else:
            try:
                await _cleanup_owned_path(path)
            except asyncio.CancelledError:
                await _cleanup_after_cancellation(
                    path,
                    wait=wait_for_cleanup_on_cancel,
                )
                if primary_error is None:
                    raise
            except OSError as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f"Owned source cleanup failed for {path}: {cleanup_error}")
