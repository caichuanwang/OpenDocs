from __future__ import annotations

import hashlib
import json
import os
import tomllib
import warnings as warnings_module
from pathlib import Path
from typing import ClassVar, cast

import pytest  # pyright: ignore[reportMissingImports]

import opendocs.api as api_module
from opendocs import VisionConfig, aparse
from opendocs.errors import OpenDocsWarning
from opendocs.vision.base import VisionRequest, VisionResult, VisionTextElement
from tests.m2_acceptance import (
    EXTRACTOR_VERSION,
    M2Asset,
    M2Checklist,
    M2Entry,
    ReplayRequest,
    ReplayWarning,
    _m2_entries,
    _sha256,
    _verified_assets,
    assert_live_structure,
    assert_replay_output,
    load_checklist,
    load_replay,
    markdown_anchor_hashes,
    observed_markdown_anchors,
    validate_m2_options,
    write_candidate,
)


def _entries() -> tuple[M2Entry, M2Entry]:
    return (
        M2Entry("contract.docx", "1" * 64),
        M2Entry("deck.pptx", "2" * 64),
    )


def _derived(entries: tuple[M2Entry, M2Entry]) -> dict[str, object]:
    docx, pptx = entries
    return {
        "assets": {docx.name: docx.sha256, pptx.name: pptx.sha256},
        "docx": {
            "anchors": ["a" * 64, "b" * 64],
            "headings": [1, 2],
            "tables": [{"columns": 2, "rows": 3, "column_spans": 0, "row_spans": 0}],
            "images": [4],
            "warnings": ["docx_nested_table_flattened"],
            "call_budget": 2,
        },
        "pptx": {
            "slides": [
                {"anchors": ["c" * 64], "tables": 1, "charts": 0, "images": 1},
                {"anchors": ["d" * 64], "tables": 0, "charts": 1, "images": 0},
            ],
            "warnings": ["pptx_blank_slide"],
            "call_budget": 3,
        },
    }


def _approved_checklist(checklist_dir: Path, entries: tuple[M2Entry, M2Entry]) -> None:
    write_candidate(checklist_dir, entries, _derived(entries))
    path = checklist_dir / "checklist.toml"
    payload = path.read_text(encoding="utf-8").replace("approved = false", "approved = true")
    path.write_text(payload, encoding="utf-8")


def _replay_payload(entries: tuple[M2Entry, M2Entry]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "assets": {
            entry.name: {
                "asset_sha256": entry.sha256,
                "markdown_sha256": hashlib.sha256(entry.name.encode()).hexdigest(),
                "warnings": [{"code": "native", "message": "native output"}],
                "requests": [{"source_index": 0, "kind": "prose", "content_sha256": "e" * 64}],
            }
            for entry in entries
        },
    }


def test_m2_manifest_selects_only_two_portable_office_entries(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        """schema_version = 1
[[files]]
name = "contract.docx"
sha256 = "1111111111111111111111111111111111111111111111111111111111111111"
milestone = "M2"
[[files]]
name = "deck.pptx"
sha256 = "2222222222222222222222222222222222222222222222222222222222222222"
milestone = "M2"
""",
        encoding="utf-8",
    )

    assert _m2_entries(manifest) == _entries()

    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('name = "deck.pptx"', 'name = "x.pdf"'),
        encoding="utf-8",
    )
    with pytest.raises(pytest.UsageError, match="DOCX and one PPTX"):
        _m2_entries(manifest)


def test_m2_manifest_rejects_duplicate_and_unsafe_scope(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.toml"
    manifest.write_text(
        """schema_version = 1
[[files]]
name = "../contract.docx"
sha256 = "1111111111111111111111111111111111111111111111111111111111111111"
milestone = "M2"
[[files]]
name = "../contract.docx"
sha256 = "1111111111111111111111111111111111111111111111111111111111111111"
milestone = "M2"
""",
        encoding="utf-8",
    )

    with pytest.raises(pytest.UsageError, match="invalid entry"):
        _m2_entries(manifest)


def test_m2_asset_verification_is_atomic(tmp_path: Path) -> None:
    entries = _entries()
    (tmp_path / entries[0].name).write_bytes(b"good")
    (tmp_path / entries[1].name).write_bytes(b"bad")
    checked = (M2Entry(entries[0].name, _sha256(tmp_path / entries[0].name)), entries[1])

    with pytest.raises(pytest.UsageError, match=r"deck\.pptx: hash mismatch"):
        _verified_assets(tmp_path, checked)


def test_checklist_requires_human_approval_and_rejects_extra_metadata(tmp_path: Path) -> None:
    entries = _entries()
    checklist_dir = tmp_path / "checklist"
    _approved_checklist(checklist_dir, entries)

    checklist = load_checklist(checklist_dir, entries)

    assert checklist.extractor_version == EXTRACTOR_VERSION
    path = checklist_dir / "checklist.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + '\ngenerated_by = "candidate"\n', encoding="utf-8"
    )
    with pytest.raises(pytest.UsageError, match="invalid schema"):
        load_checklist(checklist_dir, entries)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("approved = true", "must be approved"),
        ('"a" * 64', "anchor"),
        ('extractor_version = "wrong"', "extractor version"),
    ],
)
def test_checklist_rejects_unapproved_or_invalid_bound_evidence(
    tmp_path: Path, replacement: str, message: str
) -> None:
    entries = _entries()
    checklist_dir = tmp_path / "checklist"
    write_candidate(checklist_dir, entries, _derived(entries))
    path = checklist_dir / "checklist.toml"
    source = path.read_text(encoding="utf-8")
    if replacement == '"a" * 64':
        source = source.replace("a" * 64, "A" * 64)
    else:
        source = source.replace(
            replacement, replacement if replacement != "approved = true" else "approved = false"
        )
        if replacement == 'extractor_version = "wrong"':
            source = source.replace(f'extractor_version = "{EXTRACTOR_VERSION}"', replacement)
    path.write_text(source, encoding="utf-8")

    with pytest.raises(pytest.UsageError, match=message):
        load_checklist(checklist_dir, entries)


def test_candidate_never_approves_or_overwrites(tmp_path: Path) -> None:
    entries = _entries()
    checklist_dir = tmp_path / "checklist"

    write_candidate(checklist_dir, entries, _derived(entries))

    payload = tomllib.loads((checklist_dir / "checklist.toml").read_text(encoding="utf-8"))
    assert payload["approved"] is False
    with pytest.raises(FileExistsError):
        write_candidate(checklist_dir, entries, _derived(entries))


def test_replay_requires_exact_order_count_hash_and_repeat_output(tmp_path: Path) -> None:
    entries = _entries()
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    payload = _replay_payload(entries)
    (replay_dir / "replay.json").write_text(json.dumps(payload), encoding="utf-8")

    replay = load_replay(replay_dir, entries)
    expected = replay[entries[0].name]
    markdown = entries[0].name
    warnings = (ReplayWarning("native", "native output"),)
    requests = (ReplayRequest(0, "prose", "e" * 64),)
    assert_replay_output(expected, markdown, warnings, requests)

    with pytest.raises(AssertionError, match="request sequence"):
        assert_replay_output(expected, markdown, warnings, requests + requests)
    with pytest.raises(AssertionError, match="markdown hash"):
        assert_replay_output(expected, "drift", warnings, requests)
    with pytest.raises(AssertionError, match="repeat run"):
        assert_replay_output(expected, markdown, warnings, requests, repeated_markdown="drift")


def test_replay_rejects_wrong_asset_binding_and_extra_file(tmp_path: Path) -> None:
    entries = _entries()
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    payload = _replay_payload(entries)
    assets = cast(dict[str, object], payload["assets"])
    asset = cast(dict[str, object], assets[entries[0].name])
    asset["asset_sha256"] = "0" * 64
    (replay_dir / "replay.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(pytest.UsageError, match="asset hash"):
        load_replay(replay_dir, entries)
    (replay_dir / "extra").write_text("x", encoding="utf-8")
    with pytest.raises(pytest.UsageError, match=r"only replay\.json"):
        load_replay(replay_dir, entries)


def test_live_structure_requires_ordered_anchors_and_call_budget() -> None:
    expected = ("a" * 64, "b" * 64)

    assert_live_structure(expected, expected, call_count=2, call_budget=2)

    with pytest.raises(AssertionError, match="anchor sequence"):
        assert_live_structure(expected, tuple(reversed(expected)), call_count=1, call_budget=2)
    with pytest.raises(AssertionError, match="call budget"):
        assert_live_structure(expected, expected, call_count=3, call_budget=2)


def test_m2_option_semantics_require_inputs_only_when_opted_in() -> None:
    assert validate_m2_options(None, None, None, False) is False
    with pytest.raises(pytest.UsageError, match="require --corpus-dir"):
        validate_m2_options(None, "checklist", None, False)
    with pytest.raises(pytest.UsageError, match="require --m2-checklist-dir"):
        validate_m2_options("corpus", None, "replay", False)
    assert validate_m2_options("corpus", "checklist", "replay", True) is True


class _DeterministicReplayVisionClient:
    instances: ClassVar[list[_DeterministicReplayVisionClient]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.requests: list[ReplayRequest] = []
        type(self).instances.append(self)

    async def analyze(self, request: VisionRequest) -> VisionResult:
        content_sha256 = _sha256(request.image_path)
        self.requests.append(
            ReplayRequest(request.source_index, request.kind.value, content_sha256)
        )
        return VisionResult(
            (
                VisionTextElement(
                    text=f"M2 replay image {content_sha256}",
                    source_index=request.source_index,
                ),
            )
        )

    async def aclose(self) -> None:
        return None


def _captured_warnings(
    captured: list[warnings_module.WarningMessage],
) -> tuple[ReplayWarning, ...]:
    return tuple(
        ReplayWarning(item.message.code, str(item.message))
        for item in captured
        if isinstance(item.message, OpenDocsWarning)
    )


async def _run_replay_parse(
    asset: M2Asset,
) -> tuple[str, tuple[ReplayWarning, ...], tuple[ReplayRequest, ...]]:
    before = len(_DeterministicReplayVisionClient.instances)
    with warnings_module.catch_warnings(record=True) as captured:
        warnings_module.simplefilter("always", OpenDocsWarning)
        markdown = await aparse(asset.path, vision=VisionConfig(model="replay/local"))
    assert len(_DeterministicReplayVisionClient.instances) == before + 1
    client = _DeterministicReplayVisionClient.instances[-1]
    return markdown, _captured_warnings(captured), tuple(client.requests)


def _expected_structure(
    asset: M2Asset,
    checklist: M2Checklist,
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    if asset.path.suffix.lower() == ".docx":
        return checklist.docx.anchors, checklist.docx.warnings, checklist.docx.call_budget
    anchors = tuple(anchor for slide in checklist.pptx.slides for anchor in slide.anchors)
    return anchors, checklist.pptx.warnings, checklist.pptx.call_budget


@pytest.fixture(scope="module")
def m2_acceptance_context(
    corpus_dir: Path | None,
    pytestconfig: pytest.Config,
) -> tuple[tuple[M2Asset, M2Asset], M2Checklist]:
    corpus_value = pytestconfig.getoption("corpus_dir")
    checklist_value = pytestconfig.getoption("m2_checklist_dir")
    replay_value = pytestconfig.getoption("m2_replay_dir")
    live = bool(pytestconfig.getoption("m2_live"))
    if not validate_m2_options(corpus_value, checklist_value, replay_value, live):
        pytest.skip("pass M2 acceptance options to run the private M2 gate")
    assert corpus_dir is not None
    assert checklist_value is not None
    entries = _m2_entries()
    assets = _verified_assets(corpus_dir, entries)
    checklist_dir = Path(checklist_value).expanduser().resolve()
    return assets, load_checklist(checklist_dir, entries)


def test_markdown_anchor_hashes_preserve_rendered_line_order() -> None:
    markdown = "Alpha\n\n# Beta\nAlpha\n"
    hashes = markdown_anchor_hashes(markdown)

    assert len(hashes) == 3
    assert hashes[0] == hashes[2]
    assert observed_markdown_anchors(markdown, (hashes[0], hashes[1], hashes[2])) == hashes


@pytest.mark.asyncio
async def test_m2_replay_pipeline(
    m2_acceptance_context: tuple[tuple[M2Asset, M2Asset], M2Checklist],
    pytestconfig: pytest.Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_value = pytestconfig.getoption("m2_replay_dir")
    if replay_value is None:
        pytest.skip("pass --m2-replay-dir to run the deterministic M2 replay gate")

    assets, checklist = m2_acceptance_context
    replay = load_replay(
        Path(replay_value).expanduser().resolve(),
        (assets[0].entry, assets[1].entry),
    )
    _DeterministicReplayVisionClient.instances.clear()
    monkeypatch.setattr(
        api_module,
        "LiteLLMVisionClient",
        _DeterministicReplayVisionClient,
    )

    for asset in assets:
        expected = replay[asset.entry.name]
        markdown, emitted_warnings, requests = await _run_replay_parse(asset)
        repeated_markdown, repeated_warnings, repeated_requests = await _run_replay_parse(asset)
        assert_replay_output(
            expected,
            markdown,
            emitted_warnings,
            requests,
            repeated_markdown=repeated_markdown,
        )
        assert_replay_output(
            expected,
            repeated_markdown,
            repeated_warnings,
            repeated_requests,
        )
        anchors, warning_codes, call_budget = _expected_structure(asset, checklist)
        assert tuple(warning.code for warning in emitted_warnings) == warning_codes
        assert_live_structure(
            anchors,
            observed_markdown_anchors(markdown, anchors),
            call_count=len(requests),
            call_budget=call_budget,
        )


@pytest.mark.asyncio
async def test_m2_live_pipeline(
    m2_acceptance_context: tuple[tuple[M2Asset, M2Asset], M2Checklist],
    pytestconfig: pytest.Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not pytestconfig.getoption("m2_live"):
        pytest.skip("pass --m2-live to enable the live M2 acceptance gate")
    model = os.environ.get("OPENDOCS_VISION_MODEL")
    if not model:
        raise pytest.UsageError("OPENDOCS_VISION_MODEL is required for the live M2 gate")

    assets, checklist = m2_acceptance_context
    actual_analyze = api_module.LiteLLMVisionClient.analyze
    requests: list[ReplayRequest] = []

    async def tracked_analyze(
        client: api_module.LiteLLMVisionClient,
        request: VisionRequest,
    ) -> VisionResult:
        requests.append(
            ReplayRequest(request.source_index, request.kind.value, _sha256(request.image_path))
        )
        return await actual_analyze(client, request)

    monkeypatch.setattr(api_module.LiteLLMVisionClient, "analyze", tracked_analyze)
    config = VisionConfig(
        model=model,
        api_key=os.environ.get("OPENDOCS_VISION_API_KEY"),
        api_base=os.environ.get("OPENDOCS_VISION_API_BASE"),
    )
    for asset in assets:
        requests.clear()
        with warnings_module.catch_warnings(record=True) as captured:
            warnings_module.simplefilter("always", OpenDocsWarning)
            markdown = await aparse(asset.path, vision=config)
        emitted_warnings = _captured_warnings(captured)
        anchors, warning_codes, call_budget = _expected_structure(asset, checklist)
        assert tuple(warning.code for warning in emitted_warnings) == warning_codes
        assert_live_structure(
            anchors,
            observed_markdown_anchors(markdown, anchors),
            call_count=len(requests),
            call_budget=call_budget,
        )
