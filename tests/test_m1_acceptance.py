from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest  # pyright: ignore[reportMissingImports]

import opendocs.api as api_module
from opendocs import VisionConfig, aparse
from opendocs._models import BBox
from opendocs.vision.base import (
    VisionElement,
    VisionRequest,
    VisionResult,
    VisionTableElement,
    VisionTextElement,
)

_MANIFEST = Path(__file__).with_name("corpus.example.toml")
_SUPPORTED_M1_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PAGE_COMMENT = re.compile(r"<!-- page: (\d+) -->")
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/\S+|[A-Za-z]:[\\/]\S+)")
_MAX_REPLAY_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class M1Entry:
    name: str
    sha256: str


@dataclass(frozen=True, slots=True)
class M1Asset:
    entry: M1Entry
    path: Path


def _m1_entries(manifest: Path = _MANIFEST) -> tuple[M1Entry, ...]:
    try:
        with manifest.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise pytest.UsageError("M1 public manifest is unavailable or malformed") from error
    if payload.get("schema_version") != 1 or not isinstance(payload.get("files"), list):
        raise pytest.UsageError("M1 public manifest has an unsupported schema")

    entries: list[M1Entry] = []
    for item in payload["files"]:
        if not isinstance(item, dict) or item.get("milestone") != "M1":
            continue
        name = item.get("name")
        sha256 = item.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or Path(name).suffix.lower() not in _SUPPORTED_M1_SUFFIXES
            or not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
        ):
            raise pytest.UsageError("M1 public manifest contains an invalid entry")
        entries.append(M1Entry(name, sha256))

    if len(entries) != 3 or len({entry.name for entry in entries}) != 3:
        raise pytest.UsageError("M1 public manifest must contain exactly three distinct entries")
    if len({entry.sha256 for entry in entries}) != 3:
        raise pytest.UsageError("M1 public manifest hashes must be distinct")
    if sum(entry.name.lower().endswith(".pdf") for entry in entries) != 2:
        raise pytest.UsageError("M1 public manifest must contain exactly two PDF entries")
    image_count = sum(Path(entry.name).suffix.lower() in _IMAGE_SUFFIXES for entry in entries)
    if image_count != 1:
        raise pytest.UsageError("M1 public manifest must contain exactly one image entry")
    return tuple(entries)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_assets(corpus_dir: Path, entries: tuple[M1Entry, ...]) -> tuple[M1Asset, ...]:
    try:
        root = corpus_dir.resolve(strict=True)
    except OSError as error:
        raise pytest.UsageError("M1 corpus directory is unavailable") from error
    if not root.is_dir():
        raise pytest.UsageError("M1 corpus location is not a directory")

    verified: list[M1Asset] = []
    failures: list[str] = []
    for entry in entries:
        candidate = root / entry.name
        try:
            resolved = candidate.resolve(strict=True)
            valid = resolved.is_relative_to(root) and resolved.is_file()
            digest = _sha256(resolved) if valid else ""
        except OSError:
            valid = False
            digest = ""
            resolved = candidate
        if not valid:
            failures.append(f"{entry.name}: unavailable or unsafe")
        elif digest != entry.sha256:
            failures.append(f"{entry.name}: hash mismatch")
        else:
            verified.append(M1Asset(entry, resolved))

    if failures:
        raise pytest.UsageError("M1 corpus validation failed: " + "; ".join(failures))
    return tuple(verified)


def _bbox(value: object) -> BBox | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(isinstance(number, bool) or not isinstance(number, int | float) for number in value)
    ):
        raise pytest.UsageError("M1 replay bbox must contain four numbers")
    numbers = cast(list[int | float], value)
    try:
        return BBox(*(float(number) for number in numbers)).require_normalized("M1 replay bbox")
    except (TypeError, ValueError) as error:
        raise pytest.UsageError("M1 replay bbox is outside normalized coordinates") from error


def _replay_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise pytest.UsageError("M1 replay text must be non-empty")
    if "\x00" in value or _ABSOLUTE_PATH.search(value):
        raise pytest.UsageError("M1 replay text must not contain filesystem paths")
    return value


def _replay_element(value: object) -> VisionElement:
    if not isinstance(value, dict):
        raise pytest.UsageError("M1 replay elements must be objects")
    element_type = value.get("type")
    source_index = value.get("source_index")
    if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
        raise pytest.UsageError("M1 replay source_index must be a non-negative integer")
    bbox = _bbox(value.get("bbox"))
    if element_type == "text" and set(value) <= {"type", "text", "source_index", "bbox"}:
        return VisionTextElement(_replay_text(value.get("text")), source_index, bbox)
    if element_type == "table" and set(value) <= {
        "type",
        "grid",
        "header_rows",
        "source_index",
        "bbox",
    }:
        raw_grid = value.get("grid")
        header_rows = value.get("header_rows")
        if not isinstance(raw_grid, list) or not raw_grid:
            raise pytest.UsageError("M1 replay table grid must be non-empty")
        grid: list[tuple[str | None, ...]] = []
        for raw_row in raw_grid:
            if not isinstance(raw_row, list) or any(
                cell is not None and not isinstance(cell, str) for cell in raw_row
            ):
                raise pytest.UsageError("M1 replay table rows must contain strings or null")
            cells = cast(list[str | None], raw_row)
            grid.append(tuple(_replay_text(cell) if cell is not None else None for cell in cells))
        if isinstance(header_rows, bool) or not isinstance(header_rows, int):
            raise pytest.UsageError("M1 replay header_rows must be an integer")
        try:
            return VisionTableElement(tuple(grid), header_rows, source_index, bbox)
        except (TypeError, ValueError) as error:
            raise pytest.UsageError("M1 replay table is invalid") from error
    raise pytest.UsageError("M1 replay element has an invalid tagged schema")


def _replay_results(
    replay_dir: Path,
    entries: tuple[M1Entry, ...],
) -> dict[str, VisionResult]:
    try:
        root = replay_dir.resolve(strict=True)
        if not root.is_dir() or {path.name for path in root.iterdir()} != {"results.json"}:
            raise pytest.UsageError("M1 replay directory must contain only results.json")
        results_path = (root / "results.json").resolve(strict=True)
        if not results_path.is_relative_to(root) or not results_path.is_file():
            raise pytest.UsageError("M1 replay fixture path is unsafe")
        if results_path.stat().st_size > _MAX_REPLAY_BYTES:
            raise pytest.UsageError("M1 replay fixture exceeds the size limit")
        payload = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise pytest.UsageError("M1 replay fixture is unavailable or malformed") from error
    expected_names = {entry.name for entry in entries}
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "results"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("results"), dict)
        or set(payload["results"]) != expected_names
    ):
        raise pytest.UsageError("M1 replay fixture does not match the public manifest")

    parsed: dict[str, VisionResult] = {}
    for name in sorted(expected_names):
        result = payload["results"][name]
        if not isinstance(result, dict) or set(result) != {"elements"}:
            raise pytest.UsageError("M1 replay result has an invalid schema")
        elements = result["elements"]
        if not isinstance(elements, list) or not elements:
            raise pytest.UsageError("M1 replay result must contain elements")
        parsed[name] = VisionResult(tuple(_replay_element(element) for element in elements))
    return parsed


def _assert_pdf_page_order(markdown: str) -> None:
    page_numbers = [int(value) for value in _PAGE_COMMENT.findall(markdown)]
    assert page_numbers == list(range(1, len(page_numbers) + 1))
    assert page_numbers


def test_m1_manifest_contract_selects_exactly_three_supported_entries() -> None:
    entries = _m1_entries()

    assert len(entries) == 3
    assert sum(Path(entry.name).suffix.lower() == ".pdf" for entry in entries) == 2
    assert sum(Path(entry.name).suffix.lower() in _IMAGE_SUFFIXES for entry in entries) == 1


def _write_replay_fixture(replay_dir: Path, entries: tuple[M1Entry, ...]) -> None:
    replay_dir.mkdir()
    element = {"type": "text", "text": "replay", "source_index": 0, "bbox": None}
    payload = {
        "schema_version": 1,
        "results": {entry.name: {"elements": [element]} for entry in entries},
    }
    (replay_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")


def test_m1_replay_protocol_is_strict_and_path_safe(tmp_path: Path) -> None:
    entries = (
        M1Entry("first.pdf", "1" * 64),
        M1Entry("second.pdf", "2" * 64),
        M1Entry("image.png", "3" * 64),
    )
    replay_dir = tmp_path / "replay"
    _write_replay_fixture(replay_dir, entries)

    results = _replay_results(replay_dir, entries)

    assert set(results) == {entry.name for entry in entries}
    assert all(result.elements[0] == VisionTextElement("replay", 0) for result in results.values())

    (replay_dir / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(pytest.UsageError, match=r"contain only results\.json"):
        _replay_results(replay_dir, entries)
    path_element = {"type": "text", "text": "/private/file", "source_index": 0, "bbox": None}
    with pytest.raises(pytest.UsageError, match="must not contain filesystem paths"):
        _replay_element(path_element)


def test_m1_asset_verification_is_atomic(tmp_path: Path) -> None:
    good = tmp_path / "first.pdf"
    bad = tmp_path / "second.pdf"
    image = tmp_path / "image.png"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")
    image.write_bytes(b"image")
    entries = (
        M1Entry(good.name, _sha256(good)),
        M1Entry(bad.name, "0" * 64),
        M1Entry(image.name, _sha256(image)),
    )

    with pytest.raises(pytest.UsageError, match=r"second\.pdf: hash mismatch"):
        _verified_assets(tmp_path, entries)


@pytest.fixture(scope="module")
def m1_assets(
    corpus_dir: Path | None,
    pytestconfig: pytest.Config,
) -> tuple[M1Asset, ...]:
    entries = _m1_entries()
    if corpus_dir is None:
        if pytestconfig.getoption("m1_replay_dir") is not None or pytestconfig.getoption("m1_live"):
            raise pytest.UsageError("M1 acceptance options require --corpus-dir")
        pytest.skip("pass --corpus-dir to run the private M1 acceptance gate")
    assert corpus_dir is not None
    return _verified_assets(corpus_dir, entries)


class _ReplayVisionClient:
    result: VisionResult

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.requests.append(request)
        return self.result

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_m1_replay_pipeline(
    m1_assets: tuple[M1Asset, ...],
    pytestconfig: pytest.Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_value = pytestconfig.getoption("m1_replay_dir")
    if replay_value is None:
        pytest.skip("pass --m1-replay-dir to run the deterministic M1 replay gate")
    replay_dir = Path(replay_value).expanduser().resolve()
    results = _replay_results(replay_dir, tuple(asset.entry for asset in m1_assets))
    monkeypatch.setattr(api_module, "LiteLLMVisionClient", _ReplayVisionClient)

    for asset in m1_assets:
        _ReplayVisionClient.result = results[asset.entry.name]
        markdown = await aparse(asset.path, vision=VisionConfig(model="replay/local"))
        assert markdown.strip()
        if asset.path.suffix.lower() == ".pdf":
            _assert_pdf_page_order(markdown)
        else:
            tables = [
                element
                for element in results[asset.entry.name].elements
                if isinstance(element, VisionTableElement)
            ]
            assert len(tables) == 1
            table = tables[0]
            assert table.header_rows >= 2
            assert len(table.grid[0]) == 20
            assert len(table.grid) - table.header_rows == 4
            assert "<table>" in markdown


@pytest.mark.asyncio
async def test_m1_live_pipeline(
    m1_assets: tuple[M1Asset, ...],
    pytestconfig: pytest.Config,
) -> None:
    if not pytestconfig.getoption("m1_live"):
        pytest.skip("pass --m1-live to enable the live M1 acceptance gate")
    model = os.environ.get("OPENDOCS_VISION_MODEL")
    if not model:
        pytest.skip("OPENDOCS_VISION_MODEL is required for the live M1 gate")
    config = VisionConfig(
        model=model,
        api_key=os.environ.get("OPENDOCS_VISION_API_KEY"),
        api_base=os.environ.get("OPENDOCS_VISION_API_BASE"),
    )
    for asset in m1_assets:
        markdown = await aparse(asset.path, vision=config)
        assert markdown.strip()
        if asset.path.suffix.lower() == ".pdf":
            _assert_pdf_page_order(markdown)
