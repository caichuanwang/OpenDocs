from __future__ import annotations

import asyncio
import io
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SpreadsheetImage
from PIL import Image

import opendocs.api as api_module
import opendocs.source as source_module
from opendocs import (
    DocumentTimeoutError,
    LimitExceededError,
    OpenDocsWarning,
    ParseOptions,
    RuntimeDependencyError,
    VisionConfig,
    aparse,
    parse,
)
from opendocs._runtime import ParserRuntime
from opendocs.source import Source
from tests.native_worker_helpers import hard_exit, sleep_and_echo
from tests.xlsx_fixtures import rewrite_xlsx, write_public_contract_xlsx


class NamedBytesIO(io.BytesIO):
    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _source_factory(kind: str, path: Path, content: bytes) -> Callable[[], Source]:
    if kind == "path":
        return lambda: path
    if kind == "bytes":
        return lambda: content
    if kind == "named_stream":
        return lambda: NamedBytesIO(content, str(path))
    if kind == "unnamed_stream":
        return lambda: io.BytesIO(content)
    raise AssertionError(f"unknown source kind: {kind}")


def _warning_codes(captured: list[warnings.WarningMessage]) -> tuple[str, ...]:
    return tuple(
        item.message.code for item in captured if isinstance(item.message, OpenDocsWarning)
    )


def _invoke(
    api_kind: str,
    source: Source,
    *,
    options: ParseOptions | None = None,
    vision: VisionConfig | None = None,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", OpenDocsWarning)
        result = (
            parse(source, options=options, vision=vision)
            if api_kind == "parse"
            else asyncio.run(aparse(source, options=options, vision=vision))
        )
    public_warnings = tuple(item for item in captured if isinstance(item.message, OpenDocsWarning))
    return (
        result,
        _warning_codes(captured),
        tuple(str(item.message) for item in public_warnings),
        tuple(item.filename for item in public_warnings),
    )


def test_xlsx_public_api_has_eight_equivalent_input_and_api_combinations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contract.xlsx"
    write_public_contract_xlsx(path)
    content = path.read_bytes()
    outcomes: list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
    streams: list[Source] = []
    for source_kind in ("path", "bytes", "named_stream", "unnamed_stream"):
        source_factory = _source_factory(source_kind, path, content)
        for api_kind in ("parse", "aparse"):
            source = source_factory()
            streams.append(source)
            outcomes.append(_invoke(api_kind, source))

    assert len(outcomes) == 8
    assert [outcome[:3] for outcome in outcomes] == [outcomes[0][:3]] * 8
    result, warning_codes, _messages, filenames = outcomes[0]
    assert warning_codes == (
        "xlsx_formula_cache_missing",
        "xlsx_unsupported_number_format",
    )
    assert filenames == (__file__, __file__)
    assert "# Ledger (Visible)" in result
    assert "$1,234.50" in result
    assert "2026-08-14" in result
    assert "=B2*2" in result
    assert "# Hidden (Hidden)" in result
    assert "# Very Hidden (Very Hidden)" in result
    assert "# Empty (Visible)" in result
    assert all(not source.closed for source in streams if hasattr(source, "closed"))


@pytest.mark.asyncio
async def test_xlsx_async_warning_emission_points_to_the_public_api_caller(
    tmp_path: Path,
) -> None:
    path = tmp_path / "warning-location.xlsx"
    write_public_contract_xlsx(path)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", OpenDocsWarning)
        await aparse(path)

    public_warnings = tuple(item for item in captured if isinstance(item.message, OpenDocsWarning))
    assert tuple(item.filename for item in public_warnings) == (__file__, __file__)


def test_xlsx_public_output_is_repeatable_and_max_pages_does_not_limit_sheets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repeatable.xlsx"
    write_public_contract_xlsx(path)

    outcomes = [_invoke("parse", path) for _ in range(3)]
    assert outcomes == [outcomes[0]] * 3

    limited = _invoke("parse", path, options=ParseOptions(max_pages=1))
    assert limited == outcomes[0]
    assert limited[0].count("<!-- xlsx-sheet:") == 4


def test_xlsx_public_output_limit_uses_complete_blocks_and_emits_warning(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated.xlsx"
    write_public_contract_xlsx(path)
    complete = _invoke("parse", path)
    first_region = complete[0].index("<!-- xlsx-region:")

    truncated = _invoke(
        "parse",
        path,
        options=ParseOptions(max_output_chars=first_region),
    )

    assert truncated[0] == complete[0][:first_region].rstrip() + "\n"
    assert truncated[1] == (*complete[1], "output_truncated")
    assert truncated[3] == (__file__, __file__, __file__)


def test_xlsx_public_limit_failure_type_is_equal_across_all_eight_combinations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversized.xlsx"
    write_public_contract_xlsx(path)
    with ZipFile(path) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")
    sheet_xml = sheet_xml.replace(b'<dimension ref="A1:E4"/>', b'<dimension ref="A1:XFD1048576"/>')
    rewrite_xlsx(path, {"xl/worksheets/sheet1.xml": sheet_xml})
    content = path.read_bytes()
    failures: list[tuple[type[BaseException], object]] = []

    for source_kind in ("path", "bytes", "named_stream", "unnamed_stream"):
        source_factory = _source_factory(source_kind, path, content)
        for api_kind in ("parse", "aparse"):
            with pytest.raises(LimitExceededError) as exc_info:
                source = source_factory()
                if api_kind == "parse":
                    parse(source)
                else:
                    asyncio.run(aparse(source))
            failures.append((type(exc_info.value), exc_info.value.code))

    assert failures == [failures[0]] * 8


def test_xlsx_normal_and_parser_failure_cleanup_owned_source_and_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "lifecycle.xlsx"
    write_public_contract_xlsx(path)
    content = path.read_bytes()
    original_run_native = ParserRuntime.run_native
    observed: list[tuple[Path, Path]] = []

    async def recording_run_native(
        self: ParserRuntime,
        function: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        assert isinstance(args[0], Path)
        assert isinstance(args[1], Path)
        observed.append((args[0], args[1]))
        return await original_run_native(self, function, *args, **kwargs)

    monkeypatch.setattr(ParserRuntime, "run_native", recording_run_native)
    _invoke("parse", content)
    source_path, workspace_path = observed[-1]
    assert not source_path.exists()
    assert not workspace_path.exists()

    async def parser_failure(
        self: ParserRuntime,
        function: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        del self, function, kwargs
        assert isinstance(args[0], Path)
        assert isinstance(args[1], Path)
        observed.append((args[0], args[1]))
        raise RuntimeDependencyError("synthetic XLSX parser failure")

    monkeypatch.setattr(ParserRuntime, "run_native", parser_failure)
    with pytest.raises(RuntimeDependencyError, match="synthetic XLSX parser failure"):
        parse(content)
    source_path, workspace_path = observed[-1]
    assert not source_path.exists()
    assert not workspace_path.exists()


@pytest.mark.asyncio
async def test_xlsx_external_cancellation_reaps_native_worker_and_cleans_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cancel.xlsx"
    write_public_contract_xlsx(path)
    original_run_native = ParserRuntime.run_native
    entered = asyncio.Event()
    runtime: ParserRuntime | None = None
    source_path: Path | None = None
    workspace_path: Path | None = None

    async def blocked_run_native(
        self: ParserRuntime,
        function: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal runtime, source_path, workspace_path
        del function, kwargs
        runtime = self
        source_path = cast(Path, args[0])
        workspace_path = cast(Path, args[1])
        worker_task = asyncio.create_task(original_run_native(self, sleep_and_echo, 5.0, "done"))
        for _ in range(100):
            if self.native_worker.pid is not None:
                break
            await asyncio.sleep(0.01)
        entered.set()
        return await worker_task

    monkeypatch.setattr(ParserRuntime, "run_native", blocked_run_native)
    task = asyncio.create_task(aparse(path.read_bytes()))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime is not None and runtime.native_worker.is_alive is False
    assert source_path is not None and not source_path.exists()
    assert workspace_path is not None and not workspace_path.exists()


@pytest.mark.parametrize("api_kind", ["parse", "aparse"])
def test_xlsx_document_timeout_reaps_native_worker_and_cleans_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_kind: str,
) -> None:
    path = tmp_path / f"timeout-{api_kind}.xlsx"
    write_public_contract_xlsx(path)
    original_run_native = ParserRuntime.run_native
    runtime: ParserRuntime | None = None
    source_path: Path | None = None
    workspace_path: Path | None = None

    async def blocked_run_native(
        self: ParserRuntime,
        function: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal runtime, source_path, workspace_path
        del function, kwargs
        runtime = self
        source_path = cast(Path, args[0])
        workspace_path = cast(Path, args[1])
        return await original_run_native(self, sleep_and_echo, 5.0, "done")

    monkeypatch.setattr(ParserRuntime, "run_native", blocked_run_native)
    with pytest.raises(DocumentTimeoutError):
        if api_kind == "parse":
            parse(path.read_bytes(), options=ParseOptions(timeout=0.05))
        else:
            asyncio.run(aparse(path.read_bytes(), options=ParseOptions(timeout=0.05)))

    assert runtime is not None and runtime.native_worker.is_alive is False
    assert source_path is not None and not source_path.exists()
    assert workspace_path is not None and not workspace_path.exists()


def test_xlsx_native_hard_termination_cleans_owned_source_and_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "crash.xlsx"
    write_public_contract_xlsx(path)
    original_run_native = ParserRuntime.run_native
    runtime: ParserRuntime | None = None
    source_path: Path | None = None
    workspace_path: Path | None = None

    async def crashing_run_native(
        self: ParserRuntime,
        function: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal runtime, source_path, workspace_path
        del function, kwargs
        runtime = self
        source_path = cast(Path, args[0])
        workspace_path = cast(Path, args[1])
        return await original_run_native(self, hard_exit, 17)

    monkeypatch.setattr(ParserRuntime, "run_native", crashing_run_native)
    with pytest.raises(RuntimeDependencyError, match="exited without returning"):
        parse(path.read_bytes())

    assert runtime is not None and runtime.native_worker.is_alive is False
    assert source_path is not None and not source_path.exists()
    assert workspace_path is not None and not workspace_path.exists()


def test_xlsx_cleanup_failure_preserves_primary_error_as_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cleanup.xlsx"
    write_public_contract_xlsx(path)
    workspace_path: Path | None = None

    async def fail_cleanup(path: Path) -> None:
        nonlocal workspace_path
        workspace_path = path
        raise PermissionError(f"cleanup blocked for {path.name}")

    async def parser_failure(
        self: ParserRuntime,
        function: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal workspace_path
        del self, function, kwargs
        workspace_path = cast(Path, args[1])
        raise RuntimeDependencyError("primary XLSX failure")

    monkeypatch.setattr(ParserRuntime, "run_native", parser_failure)
    monkeypatch.setattr(source_module, "_cleanup_workspace", fail_cleanup)
    with pytest.raises(RuntimeDependencyError, match="primary XLSX failure") as exc_info:
        parse(path)
    assert any(
        "Parse workspace cleanup failed" in note
        for note in getattr(exc_info.value, "__notes__", ())
    )
    assert workspace_path is not None
    source_module._remove_workspace(workspace_path)


def test_xlsx_per_object_vision_timeout_keeps_native_result_and_cleans_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_image = tmp_path / "checker.png"
    image = Image.new("RGB", (64, 64), "white")
    try:
        for row in range(64):
            for column in range(64):
                if (row // 8 + column // 8) % 2:
                    image.putpixel((column, row), (0, 0, 180))
        image.save(source_image, "PNG")
    finally:
        image.close()

    path = tmp_path / "visual-timeout.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "native value"
    sheet.add_image(SpreadsheetImage(source_image), "C3")
    workbook.save(path)
    workbook.close()

    workspace_path = tmp_path / "vision-workspace"
    requests: list[object] = []

    def create_workspace() -> Path:
        workspace_path.mkdir()
        return workspace_path

    class SlowVisionClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def analyze(self, request: object) -> object:
            requests.append(request)
            await asyncio.sleep(1)
            raise AssertionError("per-object timeout should cancel vision")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(source_module, "_create_workspace", create_workspace)
    monkeypatch.setattr(api_module, "LiteLLMVisionClient", SlowVisionClient)
    result, codes, _messages, _filenames = _invoke(
        "aparse",
        path,
        options=ParseOptions(timeout=2),
        vision=VisionConfig("fake/model", timeout=0.01),
    )

    assert "native value" in result
    assert codes == ("xlsx_vision_timeout",)
    assert requests
    assert not workspace_path.exists()
