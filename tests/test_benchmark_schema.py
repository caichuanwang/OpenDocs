import json
import tomllib
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from benchmarks.document_parsing.schema import (
    BenchmarkItem,
    SchemaError,
    load_manifest,
    load_policy,
    parse_manifest,
    parse_policy,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "benchmarks/document_parsing/policy-v0.1.json"
EXAMPLE_MANIFEST_PATH = ROOT / "benchmarks/document_parsing/manifest.example.json"

CATEGORIES = (
    "native_text",
    "scanned_text",
    "native_table",
    "visual_table",
    "visual_content",
    "mixed",
)


def _sha(value: int) -> str:
    return f"{value:064x}"


def _item(
    item_id: str,
    *,
    format_name: str,
    split: str,
    source_number: int,
    category: str | None = None,
    page_number: int | None = None,
    real_document: bool = False,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "format": format_name,
        "split": split,
        "category": category,
        "source": {
            "source_id": f"source-{source_number}",
            "sha256": _sha(10_000 + source_number),
            "path": f"documents/source-{source_number}.bin",
        },
        "content_sha256": _sha(20_000 + source_number),
        "page_number": page_number,
        "annotation": {
            "ref": f"annotations/{item_id}.json",
            "sha256": _sha(30_000 + source_number),
        },
        "contamination": {
            "inspected": False,
            "used_for_threshold_calibration": False,
            "used_for_prompt_or_code_tuning": False,
        },
        "real_document": real_document,
    }


def _valid_manifest() -> dict[str, object]:
    items: list[dict[str, object]] = []
    source_number = 1
    for split in ("tuning", "holdout"):
        for category in CATEGORIES:
            for category_index in range(5):
                items.append(
                    _item(
                        f"{split}-{category}-{category_index}",
                        format_name="pdf_page" if category_index % 2 == 0 else "image",
                        split=split,
                        source_number=source_number,
                        category=category,
                        page_number=category_index + 1,
                    )
                )
                source_number += 1
        for format_name in ("docx", "pptx"):
            items.append(
                _item(
                    f"{split}-{format_name}",
                    format_name=format_name,
                    split=split,
                    source_number=source_number,
                    real_document=True,
                )
            )
            source_number += 1
    return {
        "schema_version": 1,
        "policy_version": "0.1",
        "items": items,
    }


def _items(manifest: dict[str, object]) -> list[dict[str, object]]:
    items = manifest["items"]
    assert isinstance(items, list)
    return cast(list[dict[str, object]], items)


def test_checked_in_policy_and_complete_manifest_are_valid(tmp_path: Path) -> None:
    policy = load_policy(POLICY_PATH)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_valid_manifest()), encoding="utf-8")

    manifest = load_manifest(manifest_path, policy)

    assert len(manifest.items) == 64
    assert manifest.policy_version == policy.policy_version
    assert manifest.digest


@pytest.mark.parametrize(
    ("target", "mutation"),
    [
        ("policy", lambda value: value.update({"unknown": True})),
        ("policy", lambda value: value.pop("quality")),
        ("manifest", lambda value: value.update({"unknown": True})),
        ("manifest", lambda value: value.pop("items")),
    ],
)
def test_strict_parsing_rejects_unknown_and_missing_fields(
    target: str,
    mutation: Callable[[dict[str, object]], object],
) -> None:
    policy_data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    manifest_data = _valid_manifest()
    value = policy_data if target == "policy" else manifest_data
    mutation(value)

    with pytest.raises(SchemaError):
        if target == "policy":
            parse_policy(policy_data)
        else:
            parse_manifest(manifest_data, load_policy(POLICY_PATH))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: _items(manifest)[0].update({"content_sha256": "bad"}),
        lambda manifest: _items(manifest)[1].update({"item_id": _items(manifest)[0]["item_id"]}),
        lambda manifest: _items(manifest)[0].update({"category": "unknown"}),
        lambda manifest: _items(manifest)[0].update({"split": "validation"}),
        lambda manifest: manifest.update({"policy_version": "9.9"}),
    ],
)
def test_manifest_rejects_invalid_identity_and_enum_values(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    manifest = _valid_manifest()
    mutate(manifest)

    with pytest.raises(SchemaError):
        parse_manifest(manifest, load_policy(POLICY_PATH))


def test_manifest_rejects_content_reuse_across_splits() -> None:
    manifest = _valid_manifest()
    tuning = _items(manifest)[0]
    holdout = next(item for item in _items(manifest) if item["split"] == "holdout")
    holdout["content_sha256"] = tuning["content_sha256"]

    with pytest.raises(SchemaError, match="content"):
        parse_manifest(manifest, load_policy(POLICY_PATH))


@pytest.mark.parametrize("identity_field", ["source_id", "sha256"])
def test_manifest_rejects_source_document_reuse_across_splits(identity_field: str) -> None:
    manifest = _valid_manifest()
    tuning = cast(dict[str, object], _items(manifest)[0]["source"])
    holdout = cast(
        dict[str, object],
        next(item for item in _items(manifest) if item["split"] == "holdout")["source"],
    )
    holdout[identity_field] = tuning[identity_field]

    with pytest.raises(SchemaError, match="source"):
        parse_manifest(manifest, load_policy(POLICY_PATH))


def test_manifest_requires_exact_page_and_category_counts() -> None:
    policy = load_policy(POLICY_PATH)
    too_few = _valid_manifest()
    _items(too_few).pop(0)
    with pytest.raises(SchemaError, match="30"):
        parse_manifest(too_few, policy)

    category_short = _valid_manifest()
    tuning_items = [
        item
        for item in _items(category_short)
        if item["split"] == "tuning" and item["category"] == CATEGORIES[0]
    ]
    tuning_items[0]["category"] = CATEGORIES[1]
    with pytest.raises(SchemaError, match=CATEGORIES[0]):
        parse_manifest(category_short, policy)


@pytest.mark.parametrize("format_name", ["docx", "pptx"])
def test_manifest_requires_one_real_office_document_per_format_and_split(
    format_name: str,
) -> None:
    manifest = _valid_manifest()
    office = next(
        item
        for item in _items(manifest)
        if item["split"] == "holdout" and item["format"] == format_name
    )
    office["real_document"] = False

    with pytest.raises(SchemaError, match=format_name):
        parse_manifest(manifest, load_policy(POLICY_PATH))


@pytest.mark.parametrize(
    "field",
    [
        "inspected",
        "used_for_threshold_calibration",
        "used_for_prompt_or_code_tuning",
    ],
)
def test_holdout_rejects_any_contamination(field: str) -> None:
    manifest = _valid_manifest()
    item = next(item for item in _items(manifest) if item["split"] == "holdout")
    contamination = cast(dict[str, object], item["contamination"])
    contamination[field] = True

    with pytest.raises(SchemaError, match="holdout"):
        parse_manifest(manifest, load_policy(POLICY_PATH))


def test_inspected_holdout_item_reclassifies_to_tuning() -> None:
    policy = load_policy(POLICY_PATH)
    manifest = parse_manifest(_valid_manifest(), policy)
    holdout = next(item for item in manifest.items if item.split == "holdout")

    moved = holdout.reclassify_as_tuning(inspected=True)

    assert isinstance(moved, BenchmarkItem)
    assert moved.split == "tuning"
    assert moved.contamination.inspected is True


def test_example_manifest_contains_only_explicit_placeholders() -> None:
    example = json.loads(EXAMPLE_MANIFEST_PATH.read_text(encoding="utf-8"))
    serialized = json.dumps(example)

    assert example["items"]
    assert all(item["source"]["source_id"].startswith("replace-") for item in example["items"])
    assert "/Users/" not in serialized
    assert "api_key" not in serialized
    assert "provider_payload" not in serialized


def test_private_benchmark_inputs_are_ignored_and_not_packaged() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required_ignored_paths = {
        "benchmarks/document_parsing/private/",
        "benchmarks/document_parsing/runs/",
        "benchmarks/document_parsing/manifest.local.json",
    }
    build_targets = pyproject["tool"]["hatch"]["build"]["targets"]

    assert required_ignored_paths <= set(gitignore.splitlines())
    assert build_targets["wheel"]["packages"] == ["src/opendocs"]
    assert "/benchmarks/document_parsing/private" in build_targets["sdist"]["exclude"]
    assert "/benchmarks/document_parsing/runs" in build_targets["sdist"]["exclude"]


def test_manifest_parsing_does_not_mutate_caller_data() -> None:
    source = _valid_manifest()
    original = deepcopy(source)

    parse_manifest(source, load_policy(POLICY_PATH))

    assert source == original
