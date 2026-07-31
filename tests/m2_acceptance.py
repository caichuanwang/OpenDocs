from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest  # pyright: ignore[reportMissingImports]

_MANIFEST = Path(__file__).with_name("corpus.example.toml")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PATH_LIKE = re.compile(r"(?:^|\s)(?:/\S+|[A-Za-z]:[\\/]\S+)")
_REQUEST_KIND = re.compile(r"[a-z_]+")

EXTRACTOR_VERSION = "m2-office-acceptance-v1"


@dataclass(frozen=True, slots=True)
class M2Entry:
    name: str
    sha256: str


@dataclass(frozen=True, slots=True)
class M2Asset:
    entry: M2Entry
    path: Path


@dataclass(frozen=True, slots=True)
class DocxTableEvidence:
    columns: int
    rows: int
    column_spans: int
    row_spans: int


@dataclass(frozen=True, slots=True)
class DocxChecklist:
    anchors: tuple[str, ...]
    headings: tuple[int, ...]
    tables: tuple[DocxTableEvidence, ...]
    images: tuple[int, ...]
    warnings: tuple[str, ...]
    call_budget: int


@dataclass(frozen=True, slots=True)
class PptxSlideEvidence:
    anchors: tuple[str, ...]
    tables: int
    charts: int
    images: int


@dataclass(frozen=True, slots=True)
class PptxChecklist:
    slides: tuple[PptxSlideEvidence, ...]
    warnings: tuple[str, ...]
    call_budget: int


@dataclass(frozen=True, slots=True)
class M2Checklist:
    extractor_version: str
    docx: DocxChecklist
    pptx: PptxChecklist


@dataclass(frozen=True, slots=True)
class ReplayWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    source_index: int
    kind: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ReplayAsset:
    asset_sha256: str
    markdown_sha256: str
    warnings: tuple[ReplayWarning, ...]
    requests: tuple[ReplayRequest, ...]


def _m2_entries(manifest: Path = _MANIFEST) -> tuple[M2Entry, M2Entry]:
    try:
        with manifest.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise pytest.UsageError("M2 public manifest is unavailable or malformed") from error
    if payload.get("schema_version") != 1 or not isinstance(payload.get("files"), list):
        raise pytest.UsageError("M2 public manifest has an unsupported schema")

    entries: list[M2Entry] = []
    for item in payload["files"]:
        if not isinstance(item, dict) or item.get("milestone") != "M2":
            continue
        name = item.get("name")
        sha256 = item.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
        ):
            raise pytest.UsageError("M2 public manifest contains an invalid entry")
        entries.append(M2Entry(name, sha256))

    if len(entries) != 2 or len({entry.name for entry in entries}) != 2:
        raise pytest.UsageError(
            "M2 public manifest must contain exactly one DOCX and one PPTX entry"
        )
    if len({entry.sha256 for entry in entries}) != 2:
        raise pytest.UsageError("M2 public manifest hashes must be distinct")
    suffixes = {Path(entry.name).suffix.lower() for entry in entries}
    if suffixes != {".docx", ".pptx"}:
        raise pytest.UsageError(
            "M2 public manifest must contain exactly one DOCX and one PPTX entry"
        )

    ordered = sorted(entries, key=lambda entry: Path(entry.name).suffix.lower())
    return ordered[0], ordered[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def markdown_anchor_hashes(markdown: str) -> tuple[str, ...]:
    """Hash non-empty rendered Markdown lines for local source-bound anchors."""
    if not isinstance(markdown, str):
        raise TypeError("markdown must be a str")
    return tuple(
        hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
        for line in markdown.splitlines()
        if line.strip()
    )


def observed_markdown_anchors(
    markdown: str,
    expected_anchors: tuple[str, ...],
) -> tuple[str, ...]:
    expected = frozenset(expected_anchors)
    return tuple(digest for digest in markdown_anchor_hashes(markdown) if digest in expected)


def _verified_assets(corpus_dir: Path, entries: tuple[M2Entry, M2Entry]) -> tuple[M2Asset, M2Asset]:
    try:
        root = corpus_dir.resolve(strict=True)
    except OSError as error:
        raise pytest.UsageError("M2 corpus directory is unavailable") from error
    if not root.is_dir():
        raise pytest.UsageError("M2 corpus location is not a directory")

    verified: list[M2Asset] = []
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
            verified.append(M2Asset(entry, resolved))

    if failures:
        raise pytest.UsageError("M2 corpus validation failed: " + "; ".join(failures))
    return verified[0], verified[1]


def _expect_exact_keys(name: str, payload: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise pytest.UsageError(f"M2 {name} has an invalid schema")
    return cast(dict[str, object], payload)


def _require_hash(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise pytest.UsageError(f"M2 {name} must be a 64-character lowercase SHA-256 digest")
    return value


def _require_nonnegative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise pytest.UsageError(f"M2 {name} must be a non-negative integer")
    return value


def _require_safe_text(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or _PATH_LIKE.search(value)
        or "\x00" in value
    ):
        raise pytest.UsageError(f"M2 {name} must be non-empty safe text")
    return value


def _require_code(name: str, value: object) -> str:
    code = _require_safe_text(name, value)
    if not re.fullmatch(r"[a-z0-9_]+", code):
        raise pytest.UsageError(f"M2 {name} must be a stable warning code")
    return code


def _require_anchor_list(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise pytest.UsageError(f"M2 {name} must be a non-empty list")
    return tuple(_require_hash(f"{name} anchor", item) for item in value)


def _require_int_list(name: str, value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise pytest.UsageError(f"M2 {name} must be a list")
    return tuple(_require_nonnegative_int(f"{name} item", item) for item in value)


def _require_warning_list(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise pytest.UsageError(f"M2 {name} must be a list")
    return tuple(_require_code(f"{name} warning", item) for item in value)


def _parse_docx_table(name: str, payload: object) -> DocxTableEvidence:
    table = _expect_exact_keys(name, payload, {"columns", "rows", "column_spans", "row_spans"})
    return DocxTableEvidence(
        columns=_require_nonnegative_int(f"{name} columns", table["columns"]),
        rows=_require_nonnegative_int(f"{name} rows", table["rows"]),
        column_spans=_require_nonnegative_int(f"{name} column_spans", table["column_spans"]),
        row_spans=_require_nonnegative_int(f"{name} row_spans", table["row_spans"]),
    )


def _parse_docx(payload: object) -> DocxChecklist:
    section = _expect_exact_keys(
        "checklist docx",
        payload,
        {"anchors", "headings", "tables", "images", "warnings", "call_budget"},
    )
    tables = section["tables"]
    if not isinstance(tables, list):
        raise pytest.UsageError("M2 checklist docx tables must be a list")
    return DocxChecklist(
        anchors=_require_anchor_list("docx", section["anchors"]),
        headings=_require_int_list("docx headings", section["headings"]),
        tables=tuple(_parse_docx_table("docx table", item) for item in tables),
        images=_require_int_list("docx images", section["images"]),
        warnings=_require_warning_list("docx", section["warnings"]),
        call_budget=_require_nonnegative_int("docx call_budget", section["call_budget"]),
    )


def _parse_pptx_slide(payload: object) -> PptxSlideEvidence:
    slide = _expect_exact_keys(
        "checklist pptx slide",
        payload,
        {"anchors", "tables", "charts", "images"},
    )
    return PptxSlideEvidence(
        anchors=_require_anchor_list("pptx slide", slide["anchors"]),
        tables=_require_nonnegative_int("pptx slide tables", slide["tables"]),
        charts=_require_nonnegative_int("pptx slide charts", slide["charts"]),
        images=_require_nonnegative_int("pptx slide images", slide["images"]),
    )


def _parse_pptx(payload: object) -> PptxChecklist:
    section = _expect_exact_keys("checklist pptx", payload, {"slides", "warnings", "call_budget"})
    slides = section["slides"]
    if not isinstance(slides, list) or not slides:
        raise pytest.UsageError("M2 checklist pptx slides must be a non-empty list")
    return PptxChecklist(
        slides=tuple(_parse_pptx_slide(item) for item in slides),
        warnings=_require_warning_list("pptx", section["warnings"]),
        call_budget=_require_nonnegative_int("pptx call_budget", section["call_budget"]),
    )


def load_checklist(checklist_dir: Path, entries: tuple[M2Entry, M2Entry]) -> M2Checklist:
    try:
        root = checklist_dir.resolve(strict=True)
    except OSError as error:
        raise pytest.UsageError("M2 checklist directory is unavailable") from error
    if not root.is_dir() or {path.name for path in root.iterdir()} != {"checklist.toml"}:
        raise pytest.UsageError("M2 checklist directory must contain only checklist.toml")
    path = (root / "checklist.toml").resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise pytest.UsageError("M2 checklist path is unsafe")

    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise pytest.UsageError("M2 checklist is unavailable or malformed") from error

    root_payload = _expect_exact_keys(
        "checklist",
        payload,
        {"schema_version", "approved", "extractor_version", "assets", "docx", "pptx"},
    )
    if root_payload["schema_version"] != 1:
        raise pytest.UsageError("M2 checklist schema version is unsupported")
    if root_payload["extractor_version"] != EXTRACTOR_VERSION:
        raise pytest.UsageError("M2 checklist extractor version does not match")

    assets = _expect_exact_keys(
        "checklist assets",
        root_payload["assets"],
        {entry.name for entry in entries},
    )
    for entry in entries:
        digest = _require_hash(f"checklist asset {entry.name}", assets[entry.name])
        if digest != entry.sha256:
            raise pytest.UsageError(f"M2 checklist asset hash mismatch for {entry.name}")

    checklist = M2Checklist(
        extractor_version=EXTRACTOR_VERSION,
        docx=_parse_docx(root_payload["docx"]),
        pptx=_parse_pptx(root_payload["pptx"]),
    )
    if root_payload["approved"] is not True:
        raise pytest.UsageError("M2 checklist must be approved by a maintainer")
    return checklist


def _render_string_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(json.dumps(value, ensure_ascii=False) for value in values) + "]"


def _render_int_list(values: tuple[int, ...]) -> str:
    return "[" + ", ".join(str(value) for value in values) + "]"


def _candidate_sections(
    entries: tuple[M2Entry, M2Entry],
    derived: dict[str, object],
) -> tuple[DocxChecklist, PptxChecklist]:
    payload = _expect_exact_keys("candidate", derived, {"assets", "docx", "pptx"})
    assets = _expect_exact_keys(
        "candidate assets",
        payload["assets"],
        {entry.name for entry in entries},
    )
    for entry in entries:
        digest = _require_hash(f"candidate asset {entry.name}", assets[entry.name])
        if digest != entry.sha256:
            raise pytest.UsageError(f"M2 candidate asset hash mismatch for {entry.name}")
    return _parse_docx(payload["docx"]), _parse_pptx(payload["pptx"])


def write_candidate(
    checklist_dir: Path,
    entries: tuple[M2Entry, M2Entry],
    derived: dict[str, object],
) -> Path:
    docx, pptx = _candidate_sections(entries, derived)
    path = checklist_dir / "checklist.toml"
    if path.exists():
        raise FileExistsError(path)
    checklist_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "schema_version = 1",
        "approved = false",
        f"extractor_version = {json.dumps(EXTRACTOR_VERSION, ensure_ascii=False)}",
        "",
        "[assets]",
    ]
    for entry in entries:
        lines.append(f"{json.dumps(entry.name, ensure_ascii=False)} = {json.dumps(entry.sha256)}")

    lines.extend(
        [
            "",
            "[docx]",
            f"anchors = {_render_string_list(docx.anchors)}",
            f"headings = {_render_int_list(docx.headings)}",
            f"images = {_render_int_list(docx.images)}",
            f"warnings = {_render_string_list(docx.warnings)}",
            f"call_budget = {docx.call_budget}",
        ]
    )
    for table in docx.tables:
        lines.extend(
            [
                "",
                "[[docx.tables]]",
                f"columns = {table.columns}",
                f"rows = {table.rows}",
                f"column_spans = {table.column_spans}",
                f"row_spans = {table.row_spans}",
            ]
        )

    lines.extend(
        [
            "",
            "[pptx]",
            f"warnings = {_render_string_list(pptx.warnings)}",
            f"call_budget = {pptx.call_budget}",
        ]
    )
    for slide in pptx.slides:
        lines.extend(
            [
                "",
                "[[pptx.slides]]",
                f"anchors = {_render_string_list(slide.anchors)}",
                f"tables = {slide.tables}",
                f"charts = {slide.charts}",
                f"images = {slide.images}",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _parse_replay_warning(payload: object) -> ReplayWarning:
    warning = _expect_exact_keys("replay warning", payload, {"code", "message"})
    return ReplayWarning(
        code=_require_code("replay warning", warning["code"]),
        message=_require_safe_text("replay warning message", warning["message"]),
    )


def _parse_replay_request(payload: object) -> ReplayRequest:
    request = _expect_exact_keys(
        "replay request",
        payload,
        {"source_index", "kind", "content_sha256"},
    )
    kind = _require_safe_text("replay request kind", request["kind"])
    if _REQUEST_KIND.fullmatch(kind) is None:
        raise pytest.UsageError("M2 replay request kind must be a stable lowercase token")
    return ReplayRequest(
        source_index=_require_nonnegative_int(
            "replay request source_index",
            request["source_index"],
        ),
        kind=kind,
        content_sha256=_require_hash("replay request content_sha256", request["content_sha256"]),
    )


def load_replay(replay_dir: Path, entries: tuple[M2Entry, M2Entry]) -> dict[str, ReplayAsset]:
    try:
        root = replay_dir.resolve(strict=True)
    except OSError as error:
        raise pytest.UsageError("M2 replay directory is unavailable") from error
    if not root.is_dir() or {path.name for path in root.iterdir()} != {"replay.json"}:
        raise pytest.UsageError("M2 replay directory must contain only replay.json")
    path = (root / "replay.json").resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise pytest.UsageError("M2 replay path is unsafe")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise pytest.UsageError("M2 replay fixture is unavailable or malformed") from error

    root_payload = _expect_exact_keys("replay", payload, {"schema_version", "assets"})
    if root_payload["schema_version"] != 1:
        raise pytest.UsageError("M2 replay schema version is unsupported")
    assets = _expect_exact_keys(
        "replay assets",
        root_payload["assets"],
        {entry.name for entry in entries},
    )

    parsed: dict[str, ReplayAsset] = {}
    for entry in entries:
        asset = _expect_exact_keys(
            f"replay asset {entry.name}",
            assets[entry.name],
            {"asset_sha256", "markdown_sha256", "warnings", "requests"},
        )
        asset_sha = _require_hash(f"replay asset {entry.name}", asset["asset_sha256"])
        if asset_sha != entry.sha256:
            raise pytest.UsageError(f"M2 replay asset hash mismatch for {entry.name}")
        warnings = asset["warnings"]
        requests = asset["requests"]
        if not isinstance(warnings, list) or not isinstance(requests, list):
            raise pytest.UsageError("M2 replay warnings and requests must be lists")
        parsed[entry.name] = ReplayAsset(
            asset_sha256=asset_sha,
            markdown_sha256=_require_hash(
                f"replay markdown {entry.name}",
                asset["markdown_sha256"],
            ),
            warnings=tuple(_parse_replay_warning(item) for item in warnings),
            requests=tuple(_parse_replay_request(item) for item in requests),
        )
    return parsed


def assert_replay_output(
    expected: ReplayAsset,
    markdown: str,
    warnings: tuple[ReplayWarning, ...],
    requests: tuple[ReplayRequest, ...],
    *,
    repeated_markdown: str | None = None,
) -> None:
    if tuple(warnings) != expected.warnings:
        raise AssertionError("warning sequence mismatch")
    if tuple(requests) != expected.requests:
        raise AssertionError("request sequence mismatch")
    if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != expected.markdown_sha256:
        raise AssertionError("markdown hash mismatch")
    if (
        repeated_markdown is not None
        and hashlib.sha256(repeated_markdown.encode("utf-8")).hexdigest()
        != expected.markdown_sha256
    ):
        raise AssertionError("repeat run output mismatch")


def assert_live_structure(
    expected_anchors: tuple[str, ...],
    observed_anchors: tuple[str, ...],
    *,
    call_count: int,
    call_budget: int,
) -> None:
    if tuple(observed_anchors) != tuple(expected_anchors):
        raise AssertionError("anchor sequence mismatch")
    if call_count > call_budget:
        raise AssertionError("call budget exceeded")


def validate_m2_options(
    corpus_dir: str | Path | None,
    checklist_dir: str | Path | None,
    replay_dir: str | Path | None,
    live: bool,
) -> bool:
    selected = checklist_dir is not None or replay_dir is not None or live
    if not selected:
        return False
    if corpus_dir is None:
        raise pytest.UsageError("M2 acceptance options require --corpus-dir")
    if checklist_dir is None:
        raise pytest.UsageError("M2 acceptance options require --m2-checklist-dir")
    if replay_dir is None and not live:
        raise pytest.UsageError("M2 replay gate requires --m2-replay-dir")
    return True
