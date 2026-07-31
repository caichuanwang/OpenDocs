from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1
SUPPORTED_POLICY_VERSION = "0.1"
SUPPORTED_SPLITS = frozenset({"tuning", "holdout"})
SUPPORTED_FORMATS = frozenset({"pdf_page", "image", "docx", "pptx"})
EXPECTED_METRICS = frozenset(
    {
        "native_text_accuracy",
        "scanned_text_accuracy",
        "native_table_cell_accuracy",
        "visual_table_cell_accuracy",
        "visual_content_recall",
        "visual_content_precision",
        "native_visual_call_rate",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SchemaError(ValueError):
    """Raised when benchmark policy or manifest data violates the public schema."""


@dataclass(frozen=True, slots=True)
class MetricThreshold:
    name: str
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class BenchmarkPolicy:
    schema_version: int
    policy_version: str
    categories: tuple[str, ...]
    pdf_image_items_per_split: int
    minimum_per_category: int
    office_documents_per_format: int
    unresolved_handling: str
    metrics: tuple[MetricThreshold, ...]
    digest: str

    def threshold(self, metric_name: str) -> MetricThreshold:
        for metric in self.metrics:
            if metric.name == metric_name:
                return metric
        raise SchemaError(f"policy does not define required metric {metric_name!r}")


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_id: str
    sha256: str
    path: str


@dataclass(frozen=True, slots=True)
class AnnotationReference:
    ref: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ContaminationState:
    inspected: bool
    used_for_threshold_calibration: bool
    used_for_prompt_or_code_tuning: bool

    @property
    def contaminated(self) -> bool:
        return (
            self.inspected
            or self.used_for_threshold_calibration
            or self.used_for_prompt_or_code_tuning
        )


@dataclass(frozen=True, slots=True)
class BenchmarkItem:
    item_id: str
    format: str
    split: str
    category: str | None
    source: SourceDocument
    content_sha256: str
    page_number: int | None
    annotation: AnnotationReference
    contamination: ContaminationState
    real_document: bool

    def reclassify_as_tuning(
        self,
        *,
        inspected: bool = False,
        threshold_calibration: bool = False,
        prompt_or_code_tuning: bool = False,
    ) -> BenchmarkItem:
        return replace(
            self,
            split="tuning",
            contamination=ContaminationState(
                inspected=self.contamination.inspected or inspected,
                used_for_threshold_calibration=(
                    self.contamination.used_for_threshold_calibration or threshold_calibration
                ),
                used_for_prompt_or_code_tuning=(
                    self.contamination.used_for_prompt_or_code_tuning or prompt_or_code_tuning
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    schema_version: int
    policy_version: str
    items: tuple[BenchmarkItem, ...]
    digest: str

    def for_split(self, split: str) -> tuple[BenchmarkItem, ...]:
        if split not in SUPPORTED_SPLITS:
            raise SchemaError(f"unsupported split {split!r}")
        return tuple(item for item in self.items if item.split == split)


@dataclass(frozen=True, slots=True)
class RunIdentity:
    candidate_commit: str
    package_version: str
    policy_digest: str
    manifest_digest: str
    evaluator_version: str
    model_identity: str
    environment_identity: str
    replay_identity: str | None = None


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_policy(path: Path) -> BenchmarkPolicy:
    return parse_policy(_load_json(path))


def load_manifest(path: Path, policy: BenchmarkPolicy) -> BenchmarkManifest:
    return parse_manifest(_load_json(path), policy)


def parse_policy(value: object) -> BenchmarkPolicy:
    data = _object(value, "policy")
    _fields(
        data,
        required={
            "schema_version",
            "policy_version",
            "categories",
            "split_requirements",
            "quality",
        },
        where="policy",
    )
    schema_version = _integer(data["schema_version"], "policy.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema version {schema_version}")
    policy_version = _string(data["policy_version"], "policy.policy_version")
    if policy_version != SUPPORTED_POLICY_VERSION:
        raise SchemaError(f"unsupported policy version {policy_version!r}")

    categories_value = data["categories"]
    if not isinstance(categories_value, list):
        raise SchemaError("policy.categories must be an array")
    categories = tuple(
        _string(category, f"policy.categories[{index}]")
        for index, category in enumerate(categories_value)
    )
    if not categories or len(categories) != len(set(categories)):
        raise SchemaError("policy.categories must contain unique values")

    requirements = _object(data["split_requirements"], "policy.split_requirements")
    _fields(
        requirements,
        required={
            "pdf_image_items_per_split",
            "minimum_per_category",
            "office_documents_per_format",
        },
        where="policy.split_requirements",
    )
    items_per_split = _positive_integer(
        requirements["pdf_image_items_per_split"],
        "policy.split_requirements.pdf_image_items_per_split",
    )
    minimum_per_category = _positive_integer(
        requirements["minimum_per_category"],
        "policy.split_requirements.minimum_per_category",
    )
    office_documents = _positive_integer(
        requirements["office_documents_per_format"],
        "policy.split_requirements.office_documents_per_format",
    )
    if minimum_per_category * len(categories) > items_per_split:
        raise SchemaError("category minimums exceed the split item count")

    quality = _object(data["quality"], "policy.quality")
    _fields(
        quality,
        required={"unresolved_handling", "metrics"},
        where="policy.quality",
    )
    unresolved_handling = _string(
        quality["unresolved_handling"],
        "policy.quality.unresolved_handling",
    )
    if unresolved_handling not in {"fail", "disclose"}:
        raise SchemaError("policy.quality.unresolved_handling must be 'fail' or 'disclose'")
    metrics_value = _object(quality["metrics"], "policy.quality.metrics")
    if set(metrics_value) != EXPECTED_METRICS:
        missing = sorted(EXPECTED_METRICS - set(metrics_value))
        unknown = sorted(set(metrics_value) - EXPECTED_METRICS)
        raise SchemaError(f"policy metrics mismatch; missing={missing}, unknown={unknown}")
    metrics = tuple(_parse_threshold(name, metrics_value[name]) for name in sorted(metrics_value))

    return BenchmarkPolicy(
        schema_version=schema_version,
        policy_version=policy_version,
        categories=categories,
        pdf_image_items_per_split=items_per_split,
        minimum_per_category=minimum_per_category,
        office_documents_per_format=office_documents,
        unresolved_handling=unresolved_handling,
        metrics=metrics,
        digest=canonical_digest(data),
    )


def parse_manifest(value: object, policy: BenchmarkPolicy) -> BenchmarkManifest:
    data = _object(value, "manifest")
    _fields(
        data,
        required={"schema_version", "policy_version", "items"},
        where="manifest",
    )
    schema_version = _integer(data["schema_version"], "manifest.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema version {schema_version}")
    policy_version = _string(data["policy_version"], "manifest.policy_version")
    if policy_version != policy.policy_version:
        raise SchemaError(
            f"manifest policy version {policy_version!r} does not match {policy.policy_version!r}"
        )
    items_value = data["items"]
    if not isinstance(items_value, list):
        raise SchemaError("manifest.items must be an array")
    items = tuple(_parse_item(item, policy, index) for index, item in enumerate(items_value))
    _validate_manifest_items(items, policy)
    return BenchmarkManifest(
        schema_version=schema_version,
        policy_version=policy_version,
        items=items,
        digest=canonical_digest(data),
    )


def _parse_threshold(name: str, value: object) -> MetricThreshold:
    data = _object(value, f"policy.quality.metrics.{name}")
    _fields(
        data,
        required=set(),
        optional={"minimum", "maximum"},
        where=f"policy.quality.metrics.{name}",
    )
    minimum = _optional_ratio(data.get("minimum"), f"{name}.minimum")
    maximum = _optional_ratio(data.get("maximum"), f"{name}.maximum")
    if (minimum is None) == (maximum is None):
        raise SchemaError(f"metric {name!r} must define exactly one threshold")
    return MetricThreshold(name=name, minimum=minimum, maximum=maximum)


def _parse_item(
    value: object,
    policy: BenchmarkPolicy,
    index: int,
) -> BenchmarkItem:
    where = f"manifest.items[{index}]"
    data = _object(value, where)
    _fields(
        data,
        required={
            "item_id",
            "format",
            "split",
            "category",
            "source",
            "content_sha256",
            "page_number",
            "annotation",
            "contamination",
            "real_document",
        },
        where=where,
    )
    item_id = _nonblank_string(data["item_id"], f"{where}.item_id")
    format_name = _string(data["format"], f"{where}.format")
    if format_name not in SUPPORTED_FORMATS:
        raise SchemaError(f"{where}.format is unsupported")
    split = _string(data["split"], f"{where}.split")
    if split not in SUPPORTED_SPLITS:
        raise SchemaError(f"{where}.split is unsupported")
    category = _optional_string(data["category"], f"{where}.category")
    page_number = _optional_positive_integer(data["page_number"], f"{where}.page_number")
    real_document = _boolean(data["real_document"], f"{where}.real_document")

    if format_name in {"pdf_page", "image"}:
        if category not in policy.categories:
            raise SchemaError(f"{where}.category is unsupported")
        if page_number is None:
            raise SchemaError(f"{where}.page_number is required for PDF/image items")
        if real_document:
            raise SchemaError(f"{where}.real_document is reserved for Office items")
    else:
        if category is not None or page_number is not None:
            raise SchemaError(f"{where} Office items cannot define a category or page number")
        if not real_document:
            raise SchemaError(f"{where} {format_name} must be a real document")

    source_data = _object(data["source"], f"{where}.source")
    _fields(
        source_data,
        required={"source_id", "sha256", "path"},
        where=f"{where}.source",
    )
    source = SourceDocument(
        source_id=_nonblank_string(source_data["source_id"], f"{where}.source.source_id"),
        sha256=_sha256(source_data["sha256"], f"{where}.source.sha256"),
        path=_nonblank_string(source_data["path"], f"{where}.source.path"),
    )
    annotation_data = _object(data["annotation"], f"{where}.annotation")
    _fields(
        annotation_data,
        required={"ref", "sha256"},
        where=f"{where}.annotation",
    )
    annotation = AnnotationReference(
        ref=_nonblank_string(annotation_data["ref"], f"{where}.annotation.ref"),
        sha256=_sha256(annotation_data["sha256"], f"{where}.annotation.sha256"),
    )
    contamination_data = _object(data["contamination"], f"{where}.contamination")
    _fields(
        contamination_data,
        required={
            "inspected",
            "used_for_threshold_calibration",
            "used_for_prompt_or_code_tuning",
        },
        where=f"{where}.contamination",
    )
    contamination = ContaminationState(
        inspected=_boolean(contamination_data["inspected"], f"{where}.contamination.inspected"),
        used_for_threshold_calibration=_boolean(
            contamination_data["used_for_threshold_calibration"],
            f"{where}.contamination.used_for_threshold_calibration",
        ),
        used_for_prompt_or_code_tuning=_boolean(
            contamination_data["used_for_prompt_or_code_tuning"],
            f"{where}.contamination.used_for_prompt_or_code_tuning",
        ),
    )
    if split == "holdout" and contamination.contaminated:
        raise SchemaError(f"{where} is a contaminated holdout item and must be replaced")

    return BenchmarkItem(
        item_id=item_id,
        format=format_name,
        split=split,
        category=category,
        source=source,
        content_sha256=_sha256(data["content_sha256"], f"{where}.content_sha256"),
        page_number=page_number,
        annotation=annotation,
        contamination=contamination,
        real_document=real_document,
    )


def _validate_manifest_items(
    items: tuple[BenchmarkItem, ...],
    policy: BenchmarkPolicy,
) -> None:
    item_ids: set[str] = set()
    content_splits: dict[str, str] = {}
    source_id_splits: dict[str, str] = {}
    source_hash_splits: dict[str, str] = {}

    for item in items:
        if item.item_id in item_ids:
            raise SchemaError(f"duplicate item id {item.item_id!r}")
        item_ids.add(item.item_id)
        _bind_to_split(
            content_splits,
            item.content_sha256,
            item.split,
            "content hash",
        )
        _bind_to_split(
            source_id_splits,
            item.source.source_id,
            item.split,
            "source id",
        )
        _bind_to_split(
            source_hash_splits,
            item.source.sha256,
            item.split,
            "source hash",
        )

    for split in sorted(SUPPORTED_SPLITS):
        split_items = tuple(item for item in items if item.split == split)
        page_items = tuple(item for item in split_items if item.format in {"pdf_page", "image"})
        if len(page_items) != policy.pdf_image_items_per_split:
            raise SchemaError(
                f"{split} must contain exactly {policy.pdf_image_items_per_split} PDF/image pages"
            )
        for category in policy.categories:
            category_count = sum(item.category == category for item in page_items)
            if category_count < policy.minimum_per_category:
                raise SchemaError(
                    f"{split} category {category!r} requires at least "
                    f"{policy.minimum_per_category} pages"
                )
        for format_name in ("docx", "pptx"):
            office_items = tuple(item for item in split_items if item.format == format_name)
            if len(office_items) != policy.office_documents_per_format or not all(
                item.real_document for item in office_items
            ):
                raise SchemaError(
                    f"{split} requires exactly {policy.office_documents_per_format} "
                    f"real {format_name} document"
                )


def _bind_to_split(
    identities: dict[str, str],
    identity: str,
    split: str,
    label: str,
) -> None:
    previous_split = identities.setdefault(identity, split)
    if previous_split != split:
        raise SchemaError(f"{label} crosses tuning and holdout splits")


def _load_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaError(f"cannot load benchmark JSON from {path.name!r}") from error


def _fields(
    data: dict[str, Any],
    *,
    required: set[str],
    where: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing or unknown:
        raise SchemaError(
            f"{where} fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{where} must be an object")
    return cast(dict[str, Any], value)


def _string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"{where} must be a string")
    return value


def _nonblank_string(value: object, where: str) -> str:
    result = _string(value, where)
    if not result.strip():
        raise SchemaError(f"{where} must not be blank")
    return result


def _optional_string(value: object, where: str) -> str | None:
    if value is None:
        return None
    return _nonblank_string(value, where)


def _integer(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{where} must be an integer")
    return value


def _positive_integer(value: object, where: str) -> int:
    result = _integer(value, where)
    if result <= 0:
        raise SchemaError(f"{where} must be greater than zero")
    return result


def _optional_positive_integer(value: object, where: str) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, where)


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError(f"{where} must be a boolean")
    return value


def _sha256(value: object, where: str) -> str:
    result = _string(value, where)
    if not _SHA256_PATTERN.fullmatch(result):
        raise SchemaError(f"{where} must be a lowercase SHA-256 digest")
    return result


def _optional_ratio(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError(f"{where} must be a number")
    result = float(value)
    if not 0 <= result <= 1:
        raise SchemaError(f"{where} must be between zero and one")
    return result
