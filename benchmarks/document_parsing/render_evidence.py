from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast


class EvidenceError(ValueError):
    """Raised when a record is incomplete, unsafe, or cannot prove a release gate."""


_QUALITY_FIELDS = {
    "schema_version",
    "evaluator_version",
    "candidate_commit",
    "package_version",
    "policy_version",
    "policy_digest",
    "manifest_digest",
    "split",
    "split_counts",
    "model_identity",
    "environment_identity",
    "replay_identity",
    "metrics",
    "passed",
}
_METRIC_FIELDS = {
    "name",
    "value",
    "minimum",
    "maximum",
    "unresolved_count",
    "categories",
    "passed",
}
_CATEGORY_FIELDS = {
    "category",
    "value",
    "unresolved_count",
    "passed",
}
_RESOURCE_FIELDS = {
    "schema_version",
    "evaluator_version",
    "candidate_commit",
    "package_version",
    "policy_digest",
    "manifest_digest",
    "environment_identity",
    "per_parse_limits",
    "requests_per_parse",
    "caller_limit",
    "timeout_seconds",
    "elapsed_seconds",
    "peak_documents",
    "peak_visual_by_parse",
    "peak_visual_global",
    "peak_rss_raw",
    "completed",
    "timed_out",
    "dispatchers_closed",
    "passed",
}


def validate_safe_quality_record(value: object) -> dict[str, object]:
    record = _object(value, "quality record")
    _exact_fields(record, _QUALITY_FIELDS, "quality record")
    if record["schema_version"] != 1:
        raise EvidenceError("quality record schema version is unsupported")
    if record["split"] not in {"tuning", "holdout"}:
        raise EvidenceError("quality record split is unsupported")
    for field in (
        "evaluator_version",
        "candidate_commit",
        "package_version",
        "policy_version",
        "policy_digest",
        "manifest_digest",
        "model_identity",
        "environment_identity",
    ):
        if not isinstance(record[field], str) or not record[field]:
            raise EvidenceError(f"quality record {field} is invalid")
    if record["replay_identity"] is not None and not isinstance(record["replay_identity"], str):
        raise EvidenceError("quality record replay_identity is invalid")
    if not isinstance(record["passed"], bool):
        raise EvidenceError("quality record passed field is invalid")

    counts = _object(record["split_counts"], "quality record split_counts")
    if not counts or any(
        not isinstance(category, str)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        for category, count in counts.items()
    ):
        raise EvidenceError("quality record split_counts is invalid")

    metrics = record["metrics"]
    if not isinstance(metrics, list) or not metrics:
        raise EvidenceError("quality record metrics must be a non-empty array")
    for index, metric_value in enumerate(metrics):
        metric = _object(metric_value, f"metric {index}")
        _exact_fields(metric, _METRIC_FIELDS, f"metric {index}")
        _validate_ratio(metric["value"], f"metric {index} value")
        _validate_ratio(metric["minimum"], f"metric {index} minimum")
        _validate_ratio(metric["maximum"], f"metric {index} maximum")
        if not isinstance(metric["name"], str) or not metric["name"]:
            raise EvidenceError(f"metric {index} name is invalid")
        if (
            isinstance(metric["unresolved_count"], bool)
            or not isinstance(metric["unresolved_count"], int)
            or metric["unresolved_count"] < 0
            or not isinstance(metric["passed"], bool)
        ):
            raise EvidenceError(f"metric {index} aggregate is invalid")
        categories = metric["categories"]
        if not isinstance(categories, list) or not categories:
            raise EvidenceError(f"metric {index} categories must be non-empty")
        for category_index, category_value in enumerate(categories):
            category = _object(
                category_value,
                f"metric {index} category {category_index}",
            )
            _exact_fields(
                category,
                _CATEGORY_FIELDS,
                f"metric {index} category {category_index}",
            )
            _validate_ratio(
                category["value"],
                f"metric {index} category {category_index} value",
            )
            if not isinstance(category["category"], str) or not category["category"]:
                raise EvidenceError(f"metric {index} category name is invalid")
            if (
                isinstance(category["unresolved_count"], bool)
                or not isinstance(category["unresolved_count"], int)
                or category["unresolved_count"] < 0
                or not isinstance(category["passed"], bool)
            ):
                raise EvidenceError(f"metric {index} category aggregate is invalid")
    return record


def validate_safe_resource_record(value: object) -> dict[str, object]:
    record = _object(value, "resource record")
    _exact_fields(record, _RESOURCE_FIELDS, "resource record")
    if record["schema_version"] != 1:
        raise EvidenceError("resource record schema version is unsupported")
    for field in (
        "evaluator_version",
        "candidate_commit",
        "package_version",
        "policy_digest",
        "manifest_digest",
        "environment_identity",
    ):
        value = record[field]
        if not isinstance(value, str) or not value or value == "unbound":
            raise EvidenceError(f"resource record {field} is invalid")
    if not _hex_digest(record["candidate_commit"], length=40):
        raise EvidenceError("resource record candidate_commit is invalid")
    for field in ("policy_digest", "manifest_digest"):
        if not _hex_digest(record[field], length=64):
            raise EvidenceError(f"resource record {field} is invalid")
    for field in ("per_parse_limits", "peak_visual_by_parse"):
        values = record[field]
        if (
            not isinstance(values, list)
            or not values
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in values
            )
        ):
            raise EvidenceError(f"resource record {field} is invalid")
    for field in (
        "requests_per_parse",
        "caller_limit",
        "peak_documents",
        "peak_visual_global",
        "peak_rss_raw",
    ):
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EvidenceError(f"resource record {field} is invalid")
    for field in ("timeout_seconds", "elapsed_seconds"):
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise EvidenceError(f"resource record {field} is invalid")
    for field in ("completed", "timed_out", "dispatchers_closed", "passed"):
        if not isinstance(record[field], bool):
            raise EvidenceError(f"resource record {field} is invalid")
    return record


def render_release_evidence(
    records: Sequence[object],
    resource_records: Sequence[object] = (),
) -> str:
    validated = tuple(validate_safe_quality_record(record) for record in records)
    validated_resources = tuple(
        validate_safe_resource_record(record) for record in resource_records
    )
    if {record["split"] for record in validated} != {"tuning", "holdout"}:
        raise EvidenceError("release evidence requires tuning and holdout records")
    if not all(record["passed"] is True for record in validated):
        raise EvidenceError("failed or incomplete evidence cannot be rendered as passing")

    identity_fields = (
        "candidate_commit",
        "package_version",
        "policy_version",
        "policy_digest",
        "manifest_digest",
        "evaluator_version",
        "model_identity",
        "environment_identity",
        "replay_identity",
    )
    first = validated[0]
    for record in validated[1:]:
        for field in identity_fields:
            if record[field] != first[field]:
                raise EvidenceError(f"quality records disagree on {field}")
    for record in validated_resources:
        if record["passed"] is not True:
            raise EvidenceError("failed or incomplete resource evidence cannot be rendered")
        for field in (
            "candidate_commit",
            "package_version",
            "policy_digest",
            "manifest_digest",
        ):
            if record[field] != first[field]:
                raise EvidenceError(f"resource record disagrees on {field}")

    lines = [
        f"# OpenDocs v{first['package_version']} 发布证据",
        "",
        f"Candidate commit: {first['candidate_commit']}",
        f"- 质量策略: `{first['policy_version']}` (`{first['policy_digest']}`)",
        f"- manifest 摘要: `{first['manifest_digest']}`",
        f"- evaluator: `{first['evaluator_version']}`",
        f"- 模型身份: `{first['model_identity']}`",
        f"- 环境身份: `{first['environment_identity']}`",
        "",
        "| Split | 数量 | 指标 | 未解析 | 结果 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for record in sorted(validated, key=lambda item: cast(str, item["split"])):
        split_counts = cast(dict[str, int], record["split_counts"])
        metrics = cast(list[dict[str, object]], record["metrics"])
        unresolved = sum(cast(int, metric["unresolved_count"]) for metric in metrics)
        lines.append(
            f"| {record['split']} | {sum(split_counts.values())} | {len(metrics)} | "
            f"{unresolved} | PASS |"
        )
    if validated_resources:
        lines.extend(
            (
                "",
                "| 资源环境 | 文档峰值 | 视觉峰值 | 耗时秒 | 结果 |",
                "| --- | ---: | ---: | ---: | --- |",
            )
        )
        for record in validated_resources:
            lines.append(
                f"| {record['environment_identity']} | {record['peak_documents']} | "
                f"{record['peak_visual_global']} | "
                f"{cast(float, record['elapsed_seconds']):.4f} | PASS |"
            )
    lines.extend(
        (
            "",
            "该文档仅由安全聚合记录生成, 不包含文件名、源文本、标注、提示词、",
            "提供商载荷或原始 Markdown。",
            "",
        )
    )
    return "\n".join(lines)


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{where} must be an object")
    return cast(dict[str, object], value)


def _exact_fields(
    value: dict[str, object],
    expected: set[str],
    where: str,
) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing or unknown:
        unsafe = sorted(unknown)
        raise EvidenceError(f"{where} fields mismatch; missing={sorted(missing)}, unsafe={unsafe}")


def _validate_ratio(value: object, where: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise EvidenceError(f"{where} must be null or a ratio")


def _hex_digest(value: object, *, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render privacy-safe M3 release evidence from passing aggregate records."
    )
    parser.add_argument("quality_records", nargs="+", type=Path)
    parser.add_argument("--resource", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    quality_records = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.quality_records
    ]
    resource_records = [json.loads(path.read_text(encoding="utf-8")) for path in args.resource]
    rendered = render_release_evidence(quality_records, resource_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
